from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.domain import EnvironmentStatus, ExecutionStage, LogLevel
from app.schemas.k8s import DeployMode
from app.schemas.cloud import CloudCredentials


class PreviewEndpoint(BaseModel):
    """One browser-reachable service preview (instance / compose multi-URL)."""

    name: str
    app_kind: str = "backend"
    url: str
    port: int | None = None
    exposed: bool = True


def parse_preview_endpoints_json(raw: str | None) -> list[PreviewEndpoint]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[PreviewEndpoint] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            out.append(PreviewEndpoint.model_validate(item))
        except Exception:
            continue
    return out


def dump_preview_endpoints(endpoints: list[dict[str, Any]] | list[PreviewEndpoint]) -> str:
    payload: list[dict[str, Any]] = []
    for item in endpoints:
        if isinstance(item, PreviewEndpoint):
            payload.append(item.model_dump(mode="json"))
        else:
            payload.append(dict(item))
    return json.dumps(payload)


class PreviewProvider(str, Enum):
    """Where a one-click preview deploys - local kind or a cloud account."""

    LOCAL = "local"
    GCP = "gcp"
    AWS = "aws"
    AZURE = "azure"
    CLOUDFLARE = "cloudflare"


class ErrorDetail(BaseModel):
    code: str
    message: str
    correlation_id: str | None = None
    details: dict[str, object] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    git_branch: str = Field(min_length=1, max_length=256)
    git_repo_url: str = Field(min_length=8, max_length=512)
    ttl_hours: int | None = Field(default=None, ge=1, le=720)
    ttl_minutes: int | None = Field(default=None, ge=1, le=43_200)
    workspace_id: UUID | None = None
    template_id: str | None = Field(default=None, max_length=64)
    cost_estimate_hourly: Decimal | None = Field(default=None, ge=0)
    provider: str | None = Field(default=None, max_length=32)
    workload_image: str | None = Field(default=None, max_length=256)
    github_pr_number: int | None = Field(default=None, ge=1)
    github_pr_url: str | None = Field(default=None, max_length=512)
    deploy_mode: DeployMode | None = None
    enable_postgres: bool = False
    enable_redis: bool = False

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("git_branch")
    @classmethod
    def normalize_branch(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("git_branch is required")
        if any(ch in cleaned for ch in (" ", "..", "\\")):
            raise ValueError("git_branch contains invalid characters")
        return cleaned

    @field_validator("git_repo_url")
    @classmethod
    def normalize_repo_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("git_repo_url is required")
        lower = cleaned.lower()
        if not (
            lower.startswith("https://")
            or lower.startswith("http://")
            or lower.startswith("git@")
            or lower.startswith("ssh://")
        ):
            raise ValueError("git_repo_url must be an http(s), git@, or ssh URL")
        if any(ch in cleaned for ch in (" ", "\n", "\r", "\t")):
            raise ValueError("git_repo_url contains invalid characters")
        return cleaned

    @model_validator(mode="after")
    def resolve_ttl(self) -> EnvironmentCreate:
        if self.ttl_hours is not None and self.ttl_minutes is not None:
            raise ValueError("Provide ttl_hours or ttl_minutes, not both")
        if self.ttl_hours is None and self.ttl_minutes is None:
            self.ttl_hours = 72
        return self


class EnvironmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    workspace_id: UUID | None
    name: str
    git_branch: str
    git_repo_url: str
    latest_commit_sha: str | None = None
    status: EnvironmentStatus
    namespace_name: str
    preview_url: str | None = None
    preview_endpoints_json: str | None = None
    template_id: str | None = None
    provider: str | None = None
    workload_image: str | None = None
    node_port: int | None = None
    github_pr_number: int | None = None
    github_pr_url: str | None = None
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    stable_pr_url: str | None = None
    deploy_mode: DeployMode = DeployMode.PREVIEW
    manifest_packaging: str | None = None
    enable_postgres: bool = False
    enable_redis: bool = False
    ttl_expires_at: datetime
    cost_estimate_hourly: Decimal
    cost_accrued: Decimal = Decimal("0.0000")
    cost_sampled_at: datetime | None = None
    cost_source: str | None = None
    time_remaining_seconds: int = 0
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    # Enriched by EnvironmentService (not persisted).
    portal_url: str | None = None
    gitops_rebuild_enabled: bool = False
    app_ready: bool = False
    ttl_warning: bool = False
    is_local: bool = False
    soft_cost_cap_exceeded: bool = False
    concurrent_active_count: int | None = None
    max_concurrent_environments: int | None = None
    runtime_summary: str | None = None
    drift_detected: bool = False
    drift_summary: str | None = None
    preview_endpoints: list[PreviewEndpoint] = Field(default_factory=list)
    # Derived datastore health for UI (not persisted).
    postgres_status: str | None = None
    redis_status: str | None = None

    @model_validator(mode="after")
    def compute_runtime_fields(self) -> EnvironmentRead:
        from app.services.cost_metering import display_cost_accrued
        from app.services.datastore_status import derive_datastore_status
        from app.services.preview_urls import repair_stored_preview_url

        now = datetime.now(UTC)
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        expires = self.ttl_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)

        self.cost_accrued = display_cost_accrued(
            cost_accrued=self.cost_accrued or Decimal("0.0000"),
            cost_estimate_hourly=self.cost_estimate_hourly,
            cost_sampled_at=self.cost_sampled_at,
            created_at=created,
            status=self.status,
            now=now,
        )
        self.time_remaining_seconds = max(int((expires - now).total_seconds()), 0)
        deploy_mode = (
            self.deploy_mode.value
            if hasattr(self.deploy_mode, "value")
            else str(self.deploy_mode or "")
        )
        if self.preview_url:
            self.preview_url = repair_stored_preview_url(
                self.preview_url,
                environment_id=self.id,
                deploy_mode=deploy_mode,
            )
        if not self.preview_endpoints:
            self.preview_endpoints = parse_preview_endpoints_json(self.preview_endpoints_json)
        if self.preview_endpoints:
            repaired_eps: list[PreviewEndpoint] = []
            for ep in self.preview_endpoints:
                repaired_eps.append(
                    ep.model_copy(
                        update={
                            "url": repair_stored_preview_url(
                                ep.url,
                                environment_id=self.id,
                                deploy_mode=deploy_mode,
                            )
                            or ep.url
                        }
                    )
                )
            self.preview_endpoints = repaired_eps
        if (
            not self.preview_endpoints
            and self.preview_url
        ):
            self.preview_endpoints = [
                PreviewEndpoint(
                    name="app",
                    app_kind="frontend",
                    url=self.preview_url,
                    port=self.node_port,
                    exposed=True,
                )
            ]
        if not self.app_ready:
            self.app_ready = bool(self.preview_url) and self.status in {
                EnvironmentStatus.RUNNING,
                EnvironmentStatus.FAILED,
            }
        self.postgres_status = derive_datastore_status(
            enabled=self.enable_postgres,
            env_status=self.status,
            app_ready=self.app_ready,
        )
        self.redis_status = derive_datastore_status(
            enabled=self.enable_redis,
            env_status=self.status,
            app_ready=self.app_ready,
        )
        return self


class PreviewAppTemplateRead(BaseModel):
    id: str
    title: str
    description: str
    icon: str
    git_repo_url: str
    git_branch: str
    default_ttl_hours: int
    hourly_cost_hint: str
    workload_image: str
    tags: list[str]


class PreviewLaunchRequest(BaseModel):
    """Happy path: choose target + template or your own repo → running preview."""

    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    template_id: str | None = Field(default=None, min_length=2, max_length=64)
    git_repo_url: str | None = Field(default=None, min_length=8, max_length=512)
    git_branch: str | None = Field(default=None, min_length=1, max_length=256)
    provider: PreviewProvider = PreviewProvider.LOCAL
    credentials: CloudCredentials = Field(default_factory=CloudCredentials)
    ttl_hours: int | None = Field(default=None, ge=1, le=168)
    ttl_minutes: int | None = Field(default=None, ge=1, le=10_080)
    workspace_id: UUID | None = None
    workload_image: str | None = Field(default=None, min_length=3, max_length=256)
    github_pr_number: int | None = Field(default=None, ge=1)
    github_pr_url: str | None = Field(default=None, max_length=512)
    deploy_mode: DeployMode | None = None
    enable_postgres: bool = False
    enable_redis: bool = False

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("git_branch")
    @classmethod
    def normalize_branch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if any(ch in cleaned for ch in (" ", "..", "\\")):
            raise ValueError("git_branch contains invalid characters")
        return cleaned

    @field_validator("git_repo_url")
    @classmethod
    def normalize_repo_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        lower = cleaned.lower()
        if not (
            lower.startswith("https://")
            or lower.startswith("http://")
            or lower.startswith("git@")
            or lower.startswith("ssh://")
        ):
            raise ValueError("git_repo_url must be an http(s), git@, or ssh URL")
        if any(ch in cleaned for ch in (" ", "\n", "\r", "\t")):
            raise ValueError("git_repo_url contains invalid characters")
        return cleaned

    @field_validator("workload_image")
    @classmethod
    def normalize_workload_image(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if any(ch.isspace() for ch in cleaned):
            raise ValueError("workload_image contains invalid whitespace")
        return cleaned

    @model_validator(mode="after")
    def require_source_and_credentials(self) -> PreviewLaunchRequest:
        if self.ttl_hours is not None and self.ttl_minutes is not None:
            raise ValueError("Provide ttl_hours or ttl_minutes, not both")

        has_template = bool(self.template_id)
        has_custom = bool(self.git_repo_url)
        has_workspace = self.workspace_id is not None
        has_local_image = (
            self.provider == PreviewProvider.LOCAL
            and bool(self.workload_image)
            and not has_template
            and not has_custom
            and not has_workspace
        )
        source_modes = sum([has_template, has_custom, has_workspace])
        if has_local_image:
            return self
        if source_modes != 1:
            raise ValueError(
                "Provide exactly one of workspace_id, template_id, or git_repo_url (+ git_branch)"
            )
        if has_custom and not self.git_branch:
            raise ValueError("git_branch is required when using git_repo_url")

        if self.provider == PreviewProvider.LOCAL:
            return self
        if self.workspace_id is not None:
            return self
        from app.core.secrets import validate_cloud_credentials

        validate_cloud_credentials(self.provider, self.credentials)
        return self


class EnvironmentExtendRequest(BaseModel):
    hours: int | None = Field(default=None, ge=1, le=72)
    minutes: int | None = Field(default=None, ge=1, le=4_320)

    @model_validator(mode="after")
    def resolve_extend_unit(self) -> EnvironmentExtendRequest:
        if self.hours is not None and self.minutes is not None:
            raise ValueError("Provide hours or minutes, not both")
        return self


class EnvironmentPromoteRequest(BaseModel):
    """Redeploy a local (or existing) preview onto a cloud provider."""

    provider: PreviewProvider
    credentials: CloudCredentials = Field(default_factory=CloudCredentials)
    name: str | None = Field(default=None, min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    ttl_hours: int | None = Field(default=None, ge=1, le=168)
    ttl_minutes: int | None = Field(default=None, ge=1, le=10_080)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()

    @model_validator(mode="after")
    def require_cloud_provider(self) -> EnvironmentPromoteRequest:
        if self.ttl_hours is not None and self.ttl_minutes is not None:
            raise ValueError("Provide ttl_hours or ttl_minutes, not both")
        if self.provider == PreviewProvider.LOCAL:
            raise ValueError("Promote target must be a cloud provider")
        from app.core.secrets import validate_cloud_credentials

        validate_cloud_credentials(self.provider, self.credentials)
        return self


class EnvStreamEvent(BaseModel):
    type: str
    status: str | None = None
    commit_sha: str | None = None
    message: str | None = None
    log_level: str | None = None
    environment_id: str | None = None
    preview_url: str | None = None


class DeploymentLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    environment_id: UUID
    log_level: LogLevel
    stage: ExecutionStage | None = None
    message: str
    timestamp: datetime


class HealthResponse(BaseModel):
    status: str
    service: str


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID | None = None
    environment_id: UUID | None = None
    actor_id: str
    action: str
    commit_sha: str | None = None
    status: str
    detail: str | None = None
    timestamp: datetime


class KindClusterStatus(BaseModel):
    """Read-only local cluster preflight for Launch → Local (k3s/k3d or kind)."""

    status: str
    cluster: str
    engine: str = "k3s"
    tool: str = "k3d"
    context: str
    kind_installed: bool
    kubectl_installed: bool
    cluster_exists: bool
    api_reachable: bool
    auto_manage: bool
    message: str
    can_launch: bool


class PreviewBuildStatus(BaseModel):
    """Whether Launchpad builds preview images from git repos (Dockerfile)."""

    enabled: bool
    dockerfile: str
    kind_load: bool
    registry: str | None = None
    message: str
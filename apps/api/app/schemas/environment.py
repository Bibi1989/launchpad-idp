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
from app.schemas.cloud import CloudCredentials, CloudPluginTarget, ImageSecurityScanConfig, KubernetesImageSource


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
    # When true, persist ttl_expires_at=None (staging/production cloud deploys).
    disable_ttl: bool = False
    lifecycle_stage: str | None = Field(default=None, max_length=32)
    promotion_lineage_id: UUID | None = None
    promoted_from_id: UUID | None = None
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
    kubernetes_image_source: str | None = Field(default=None, max_length=32)
    kubernetes_image_scan_json: str | None = Field(default=None)

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

    @field_validator("lifecycle_stage")
    @classmethod
    def normalize_lifecycle_stage(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if cleaned not in {"preview", "staging", "production"}:
            raise ValueError("lifecycle_stage must be preview, staging, or production")
        return cleaned

    @model_validator(mode="after")
    def resolve_ttl(self) -> EnvironmentCreate:
        if self.disable_ttl:
            if self.ttl_hours is not None or self.ttl_minutes is not None:
                raise ValueError("Do not set ttl_hours/ttl_minutes when disable_ttl is true")
            return self
        if self.ttl_hours is not None and self.ttl_minutes is not None:
            raise ValueError("Provide ttl_hours or ttl_minutes, not both")
        if self.ttl_hours is None and self.ttl_minutes is None:
            self.ttl_hours = 2
        return self


class EnvironmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    workspace_id: UUID | None
    # Enriched from ProvisioningWorkspace (not persisted on Environment).
    workspace_name: str | None = None
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
    kubernetes_image_source: str | None = None
    kubernetes_image_scan_json: str | None = None
    enable_postgres: bool = False
    enable_redis: bool = False
    ttl_expires_at: datetime | None = None
    cost_estimate_hourly: Decimal
    cost_accrued: Decimal = Decimal("0.0000")
    cost_sampled_at: datetime | None = None
    cost_source: str | None = None
    time_remaining_seconds: int = 0
    ttl_disabled: bool = False
    lifecycle_stage: str = "preview"
    promotion_lineage_id: UUID | None = None
    promoted_from_id: UUID | None = None
    can_promote_to_staging: bool = False
    can_promote_to_production: bool = False
    can_promote_to_cloud: bool = False
    pending_promotion_id: UUID | None = None
    error_message: str | None
    failure_summary: str | None = None
    seed_status: str | None = None
    # Last execution-pipeline stage (INIT/VALIDATE/PLAN/BUILD/APPLY) so a reload restores
    # the pipeline to the real stage instead of INIT. Derived from the latest log.
    stage: str | None = None
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

        from app.core.config import get_settings
        from app.services.cost_metering import convert_display_cost

        settings = get_settings()
        accrued_usd = display_cost_accrued(
            cost_accrued=self.cost_accrued or Decimal("0.0000"),
            cost_estimate_hourly=self.cost_estimate_hourly,
            cost_sampled_at=self.cost_sampled_at,
            created_at=created,
            status=self.status,
            now=now,
        )
        self.cost_accrued = convert_display_cost(accrued_usd, settings=settings)
        self.cost_estimate_hourly = convert_display_cost(
            self.cost_estimate_hourly or Decimal("0.0000"),
            settings=settings,
        )
        if self.ttl_expires_at is None:
            self.ttl_disabled = True
            self.time_remaining_seconds = 0
        else:
            self.ttl_disabled = False
            expires = self.ttl_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
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
    disable_ttl: bool = False
    lifecycle_stage: str | None = Field(default=None, max_length=32)
    promotion_lineage_id: UUID | None = None
    promoted_from_id: UUID | None = None
    workspace_id: UUID | None = None
    workload_image: str | None = Field(default=None, min_length=3, max_length=256)
    github_pr_number: int | None = Field(default=None, ge=1)
    github_pr_url: str | None = Field(default=None, max_length=512)
    deploy_mode: DeployMode | None = None
    enable_postgres: bool = False
    enable_redis: bool = False
    kubernetes_image_source: KubernetesImageSource | None = None
    kubernetes_image_scan: ImageSecurityScanConfig | None = None
    cloud_plugin: CloudPluginTarget | None = None

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

    @field_validator("lifecycle_stage")
    @classmethod
    def normalize_lifecycle_stage(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if cleaned not in {"preview", "staging", "production"}:
            raise ValueError("lifecycle_stage must be preview, staging, or production")
        return cleaned

    @model_validator(mode="after")
    def require_source_and_credentials(self) -> PreviewLaunchRequest:
        if self.disable_ttl:
            if self.ttl_hours is not None or self.ttl_minutes is not None:
                raise ValueError("Do not set ttl_hours/ttl_minutes when disable_ttl is true")
        elif self.ttl_hours is not None and self.ttl_minutes is not None:
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
        # Credentials may be left blank when the user has encrypted account
        # credentials stored in settings. In that case provisioning will fill
        # them server-side before sandbox / IaC materialization.
        from app.core.secrets import (
            has_aws_auth,
            has_gcp_auth,
            validate_cloud_credentials,
        )

        creds = self.credentials
        data = creds.model_dump()

        def any_non_empty(keys: list[str]) -> bool:
            return any((data.get(key) or "").strip() for key in keys)

        if self.provider == PreviewProvider.GCP:
            if has_gcp_auth(creds):
                return self
            if not any_non_empty(
                [
                    "gcp_sa_key_json",
                    "gcp_wif_project_number",
                    "gcp_wif_pool_id",
                    "gcp_wif_provider_id",
                    "gcp_wif_target_sa_email",
                ],
            ):
                return self
        elif self.provider == PreviewProvider.AWS:
            if has_aws_auth(creds):
                return self
            if not any_non_empty(
                [
                    "aws_access_key_id",
                    "aws_secret_access_key",
                    "aws_session_token",
                    "aws_role_arn",
                    "aws_role_session_name",
                ],
            ):
                return self
        elif self.provider == PreviewProvider.AZURE:
            azure_ok = bool(
                creds.azure_client_id
                and creds.azure_client_secret
                and creds.azure_tenant_id
                and creds.azure_subscription_id
            )
            if azure_ok:
                return self
            if not any_non_empty(
                [
                    "azure_client_id",
                    "azure_client_secret",
                    "azure_tenant_id",
                    "azure_subscription_id",
                ],
            ):
                return self
        elif self.provider == PreviewProvider.CLOUDFLARE:
            if creds.cloudflare_api_token:
                return self
            if not any_non_empty(["cloudflare_api_token"]):
                return self

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
    """Redeploy a local staging/production environment onto a cloud provider."""

    provider: PreviewProvider
    credentials: CloudCredentials = Field(default_factory=CloudCredentials)
    name: str | None = Field(default=None, min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    ttl_hours: int | None = Field(default=None, ge=1, le=168)
    ttl_minutes: int | None = Field(default=None, ge=1, le=10_080)
    primary_service: str | None = Field(
        default=None,
        max_length=64,
        description="Multi-service instance/compose: which service gets the preview URL",
    )
    code_source: str | None = Field(
        default=None,
        description="How source reaches a cloud VM: ssh (copy) or github (clone). Ignored for docker strategy.",
    )
    region: str | None = Field(
        default=None,
        max_length=64,
        description="Cloud region/location for the promoted preview (e.g. us-central1, us-east-1, eastus).",
    )
    create_vpc: bool = Field(
        default=False,
        description="Create an isolated VPC/VNet for this cloud preview instead of the default network.",
    )
    create_subnets: bool = Field(
        default=False,
        description="Create subnets in the preview VPC/VNet (implies create_vpc).",
    )
    existing_vpc_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Reuse an existing VPC/network id (AWS vpc-… or GCP network name). "
            "When set, create_vpc is ignored."
        ),
    )
    existing_security_group_id: str | None = Field(
        default=None,
        max_length=128,
        description="Reuse an existing AWS security group (sg-…). AWS only.",
    )
    kubernetes_image_scan: ImageSecurityScanConfig | None = None
    cloud_plugin: CloudPluginTarget | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()

    @field_validator("code_source")
    @classmethod
    def normalize_code_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if cleaned not in {"ssh", "github"}:
            raise ValueError("code_source must be ssh or github")
        return cleaned

    @field_validator("region")
    @classmethod
    def normalize_region(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        return cleaned or None

    @field_validator("existing_vpc_id")
    @classmethod
    def normalize_existing_vpc_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("existing_security_group_id")
    @classmethod
    def normalize_existing_security_group_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def imply_vpc_from_subnets(self) -> EnvironmentPromoteRequest:
        if self.existing_vpc_id:
            self.create_vpc = False
            self.create_subnets = False
            return self
        if self.create_subnets and not self.create_vpc:
            self.create_vpc = True
        return self

    @model_validator(mode="after")
    def require_cloud_provider(self) -> EnvironmentPromoteRequest:
        if self.ttl_hours is not None and self.ttl_minutes is not None:
            raise ValueError("Provide ttl_hours or ttl_minutes, not both")
        if self.provider == PreviewProvider.LOCAL:
            raise ValueError("Promote target must be a cloud provider")
        # Credentials may be left blank when the user has encrypted account
        # credentials stored in settings. In that case we validate "empty
        # allowed", and server-side preview launch will fill from the vault.
        from app.core.secrets import (
            has_aws_auth,
            has_gcp_auth,
            validate_cloud_credentials,
        )

        creds = self.credentials
        data = creds.model_dump()

        def any_non_empty(keys: list[str]) -> bool:
            return any((data.get(key) or "").strip() for key in keys)

        if self.provider == PreviewProvider.GCP:
            if has_gcp_auth(creds):
                return self
            if not any_non_empty(
                [
                    "gcp_sa_key_json",
                    "gcp_wif_project_number",
                    "gcp_wif_pool_id",
                    "gcp_wif_provider_id",
                    "gcp_wif_target_sa_email",
                ],
            ):
                return self
        elif self.provider == PreviewProvider.AWS:
            if has_aws_auth(creds):
                return self
            if not any_non_empty(
                [
                    "aws_access_key_id",
                    "aws_secret_access_key",
                    "aws_session_token",
                    "aws_role_arn",
                    "aws_role_session_name",
                ],
            ):
                return self
        elif self.provider == PreviewProvider.AZURE:
            azure_ok = bool(
                creds.azure_client_id
                and creds.azure_client_secret
                and creds.azure_tenant_id
                and creds.azure_subscription_id
            )
            if azure_ok:
                return self
            if not any_non_empty(
                [
                    "azure_client_id",
                    "azure_client_secret",
                    "azure_tenant_id",
                    "azure_subscription_id",
                ],
            ):
                return self
        elif self.provider == PreviewProvider.CLOUDFLARE:
            if creds.cloudflare_api_token:
                return self
            if not any_non_empty(["cloudflare_api_token"]):
                return self

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


class KindClusterActionRequest(BaseModel):
    """Optional cluster name for Settings → Local Kubernetes create/destroy."""

    cluster_name: str | None = Field(
        default=None,
        max_length=63,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
        description="DNS-1123 label; omit to use server KIND_CLUSTER_NAME / LOCAL_CLUSTER_NAME.",
    )


class KindClusterActionResult(BaseModel):
    status: str
    cluster: str
    engine: str = "k3s"
    context: str | None = None
    message: str
    output: str | None = None
    reason: str | None = None


class PreviewBuildStatus(BaseModel):
    """Whether Launchpad builds preview images from git repos (Dockerfile)."""

    enabled: bool
    dockerfile: str
    kind_load: bool
    registry: str | None = None
    message: str
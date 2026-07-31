from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.domain import EnvironmentStatus, ExecutionStage, LogLevel
from app.schemas.k8s import DeployMode
from app.schemas.cloud import CloudCredentials


class PreviewProvider(str, Enum):
    """Where a one-click preview deploys — local kind or a cloud account."""

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
    ttl_hours: int = Field(default=72, ge=1, le=720)
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
    template_id: str | None = None
    provider: str | None = None
    workload_image: str | None = None
    node_port: int | None = None
    github_pr_number: int | None = None
    github_pr_url: str | None = None
    stable_pr_url: str | None = None
    deploy_mode: DeployMode = DeployMode.PREVIEW
    manifest_packaging: str | None = None
    enable_postgres: bool = False
    enable_redis: bool = False
    ttl_expires_at: datetime
    cost_estimate_hourly: Decimal
    cost_accrued: Decimal = Decimal("0.0000")
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

    @model_validator(mode="after")
    def compute_runtime_fields(self) -> EnvironmentRead:
        now = datetime.now(UTC)
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        expires = self.ttl_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)

        elapsed_hours = max((now - created).total_seconds() / 3600.0, 0.0)
        self.cost_accrued = (
            self.cost_estimate_hourly * Decimal(str(round(elapsed_hours, 4)))
        ).quantize(Decimal("0.0001"))
        self.time_remaining_seconds = max(int((expires - now).total_seconds()), 0)
        if not self.app_ready:
            self.app_ready = bool(self.preview_url) and self.status in {
                EnvironmentStatus.RUNNING,
                EnvironmentStatus.FAILED,
            }
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
        has_template = bool(self.template_id)
        has_custom = bool(self.git_repo_url)
        has_workspace = self.workspace_id is not None
        source_modes = sum([has_template, has_custom, has_workspace])
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
        from app.core.secrets import has_aws_auth, has_gcp_auth

        creds = self.credentials
        if self.provider == PreviewProvider.GCP and not has_gcp_auth(creds):
            raise ValueError(
                "GCP credentials required: service account JSON or complete Workload Identity Federation config"
            )
        if self.provider == PreviewProvider.AWS and not has_aws_auth(creds):
            raise ValueError(
                "AWS credentials required: access key + secret, or IAM role ARN for keyless OIDC"
            )
        if self.provider == PreviewProvider.AZURE and (
            not creds.azure_client_id
            or not creds.azure_client_secret
            or not creds.azure_tenant_id
            or not creds.azure_subscription_id
        ):
            raise ValueError("Azure service principal fields are required")
        if self.provider == PreviewProvider.CLOUDFLARE and not creds.cloudflare_api_token:
            raise ValueError("Cloudflare API token is required")
        return self


class EnvironmentExtendRequest(BaseModel):
    hours: int | None = Field(default=None, ge=1, le=72)


class EnvironmentPromoteRequest(BaseModel):
    """Redeploy a local (or existing) preview onto a cloud provider."""

    provider: PreviewProvider
    credentials: CloudCredentials = Field(default_factory=CloudCredentials)
    name: str | None = Field(default=None, min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    ttl_hours: int | None = Field(default=None, ge=1, le=168)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()

    @model_validator(mode="after")
    def require_cloud_provider(self) -> EnvironmentPromoteRequest:
        if self.provider == PreviewProvider.LOCAL:
            raise ValueError("Promote target must be a cloud provider")
        from app.core.secrets import has_aws_auth, has_gcp_auth

        creds = self.credentials
        if self.provider == PreviewProvider.GCP and not has_gcp_auth(creds):
            raise ValueError(
                "GCP credentials required: service account JSON or complete Workload Identity Federation config"
            )
        if self.provider == PreviewProvider.AWS and not has_aws_auth(creds):
            raise ValueError(
                "AWS credentials required: access key + secret, or IAM role ARN for keyless OIDC"
            )
        if self.provider == PreviewProvider.AZURE and (
            not creds.azure_client_id
            or not creds.azure_client_secret
            or not creds.azure_tenant_id
            or not creds.azure_subscription_id
        ):
            raise ValueError("Azure service principal fields are required")
        if self.provider == PreviewProvider.CLOUDFLARE and not creds.cloudflare_api_token:
            raise ValueError("Cloudflare API token is required")
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
    """Read-only Kind/local cluster preflight for Launch → Local."""

    status: str
    cluster: str
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
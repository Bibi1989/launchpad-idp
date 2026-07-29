from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Launchpad IDP Control Plane"
    environment: str = "development"
    log_level: str = "INFO"
    correlation_header: str = "X-Correlation-ID"

    database_url: str = Field(
        default="postgresql+asyncpg://launchpad:launchpad@localhost:5432/launchpad"
    )
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Distributed state lock (Redis) — guards concurrent provision / rebuild / teardown
    state_lock_timeout_seconds: float = 900.0
    state_lock_blocking_timeout_seconds: float = 0.0

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    default_ttl_hours: int = 72
    cost_estimate_hourly: Decimal = Decimal("0.4200")
    provision_step_delay_seconds: float = 0.75

    # Preview governance (Launch / Environments)
    max_concurrent_environments: int = 4
    preview_soft_cost_cap: Decimal = Decimal("25.00")
    ttl_extend_hours_default: int = 8
    ttl_warning_hours: int = 2
    ttl_max_total_hours_from_create: int = 168

    # Preview drift scanner (live K8s vs control-plane expectations)
    drift_scan_enabled: bool = True
    drift_scan_interval_seconds: float = 600.0

    # When false, workers simulate Kubernetes mutations (local/dev without a cluster).
    # For real local previews with kind: set kubernetes_enabled=true and
    # kubernetes_context=kind-launchpad (after `make kind-up`).
    kubernetes_enabled: bool = False
    kubernetes_in_cluster: bool = False
    kubernetes_kubeconfig_path: str | None = None
    kubernetes_context: str | None = None
    kubernetes_cpu_request: str = "500m"
    kubernetes_cpu_limit: str = "2"
    kubernetes_memory_request: str = "512Mi"
    kubernetes_memory_limit: str = "4Gi"
    kubernetes_pod_limit: str = "20"
    kubernetes_ready_timeout_seconds: float = 120.0
    kubernetes_ready_poll_seconds: float = 2.0

    sse_poll_interval_seconds: float = 0.5
    ttl_reaper_interval_seconds: float = 300.0

    # Secrets encryption (Fernet key material — prefer 32+ char random string)
    secrets_encryption_key: str | None = None

    # Ephemeral IaC workspaces
    iac_workspace_root: str = "/tmp/launchpad-workspaces"

    # Sandbox execution
    sandbox_docker_enabled: bool = False
    sandbox_image: str = "ghcr.io/launchpad-idp/sandbox:latest"
    sandbox_network_mode: str = "bridge"
    sandbox_memory_limit: str = "2g"
    sandbox_cpu_limit: float = 1.0

    # GitHub App (preferred) — short-lived installation tokens for repo bootstrap
    github_app_id: int | None = None
    github_app_private_key: str | None = None
    github_app_private_key_path: str | None = None
    github_app_slug: str | None = None
    github_app_installation_id: int | None = None
    # Where GitHub redirects after App install (configure as App "Setup URL")
    github_app_setup_url: str = "http://localhost:3000/integrations/github"

    # Deprecated: long-lived PAT fallback (prefer GitHub App)
    github_pat: str | None = None

    # GitHub webhook (GitOps rebuild) — HMAC SHA-256 shared secret
    webhook_secret: str | None = None

    # Auth (HS256 JWT; shape claims for future OIDC: sub, iss, email)
    jwt_secret: str = "dev-only-jwt-secret-change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24
    auth_dev_login_enabled: bool = True

    # OIDC (Authorization Code) — when enabled, /auth/oidc/* becomes available
    oidc_enabled: bool = False
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str = "http://localhost:3000/auth/callback"
    oidc_scopes: str = "openid profile email"
    oidc_provider_name: str = "SSO"
    # Claim path for group membership (e.g. "groups", "roles")
    oidc_group_claim: str = "groups"
    # Optional global fallback: {"idp-group":"admin","other":"member"}
    oidc_group_role_map: dict[str, str] = Field(default_factory=dict)
    # Target org slug for global OIDC map (required when map is non-empty)
    oidc_default_org_slug: str | None = None

    # Invite + transactional email
    invite_base_url: str = "http://localhost:3000/invite"
    invite_ttl_hours: int = 168
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True

    # Default container image applied into ephemeral namespaces
    default_workload_image: str = "nginx:1.27-alpine"

    # Preview image build (clone repo + Dockerfile + kind load / registry push)
    preview_build_enabled: bool = False
    preview_build_dockerfile: str = "Dockerfile"
    preview_build_image_prefix: str = "launchpad-preview"
    preview_image_registry: str | None = None
    preview_build_kind_load: bool = True
    preview_build_timeout_seconds: float = 900.0

    # Auto-manage kind for Dev (kind) / Launch → Local (runs scripts/kind-up|down.sh)
    kind_auto_manage: bool = True
    kind_cluster_name: str = "launchpad"
    # Optional absolute path to the directory that contains kind-up.sh / kind-down.sh
    kind_scripts_dir: str | None = None

    # Portal status page base (simulate mode + /p/{id} deep links)
    preview_public_base_url: str = "http://localhost:3000"

    # When kubernetes_enabled, Open Preview uses NodePort on this host.
    # Keep the range small (≤10 ports) — large maps often break kind on Docker Desktop.
    preview_node_host: str = "127.0.0.1"
    preview_node_port_min: int = 30080
    preview_node_port_max: int = 30084

    # Optional Ingress host template when kubernetes_enabled (e.g. "{name}.localtest.me")
    preview_ingress_host_template: str | None = None

    # Launch Preview Analyzer (Gemini structured diagnostics)
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    preview_analyzer_heuristic_fallback: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            import json

            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return [item.strip() for item in value.split(",") if item.strip()]
            return parsed
        return value

    @field_validator(
        "github_app_id",
        "github_app_installation_id",
        "github_app_private_key",
        "github_app_private_key_path",
        "github_app_slug",
        "github_pat",
        "webhook_secret",
        "kubernetes_kubeconfig_path",
        "kubernetes_context",
        "preview_ingress_host_template",
        "kind_scripts_dir",
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "smtp_from",
        "oidc_default_org_slug",
        "gemini_api_key",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("oidc_group_role_map", mode="before")
    @classmethod
    def parse_oidc_group_role_map(cls, value: object) -> object:
        if isinstance(value, str):
            import json

            cleaned = value.strip()
            if not cleaned:
                return {}
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                raise ValueError("OIDC_GROUP_ROLE_MAP must be valid JSON object") from exc
            if not isinstance(parsed, dict):
                raise ValueError("OIDC_GROUP_ROLE_MAP must be a JSON object")
            return {str(k): str(v) for k, v in parsed.items()}
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()

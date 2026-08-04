from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
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

    # Usage-based cost metering (ResourceQuota / pod requests × rate card)
    cost_metering_enabled: bool = True
    cost_sample_interval_seconds: float = 300.0
    # Rough public-cloud shadow rates ($/hour). Local soft-cap still skips local envs.
    cost_rate_cpu_core_hour: Decimal = Decimal("0.0310")
    cost_rate_memory_gib_hour: Decimal = Decimal("0.0042")
    cost_rate_postgres_hour: Decimal = Decimal("0.0800")
    cost_rate_redis_hour: Decimal = Decimal("0.0400")

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
    # Preview namespace ResourceQuota (total across the app + its in-cluster
    # datastores). Sized so a workload plus postgres/redis/mysql/mongodb fits;
    # the old 512Mi memory cap rejected datastore pods ("exceeded quota"), which
    # left the app stuck in PodInitializing on its wait-for-db init container.
    kubernetes_cpu_request: str = "2"
    kubernetes_cpu_limit: str = "4"
    kubernetes_memory_request: str = "2Gi"
    kubernetes_memory_limit: str = "8Gi"
    kubernetes_pod_limit: str = "20"
    kubernetes_ready_timeout_seconds: float = 120.0
    kubernetes_ready_poll_seconds: float = 2.0

    sse_poll_interval_seconds: float = 0.5
    ttl_reaper_interval_seconds: float = 300.0

    # Secrets encryption (Fernet key material — prefer 32+ char random string)
    secrets_encryption_key: str | None = None

    # Ephemeral IaC workspaces (durable path — /tmp is wiped on reboot/cleanup)
    iac_workspace_root: str = str(Path.home() / ".launchpad" / "workspaces")

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

    # GitLab — OAuth Application and/or per-user PAT (stored encrypted)
    gitlab_base_url: str = "https://gitlab.com"
    gitlab_oauth_client_id: str | None = None
    gitlab_oauth_client_secret: str | None = None
    gitlab_oauth_redirect_uri: str = "http://localhost:3000/integrations/gitlab"

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

    # Launchpad OIDC Issuer (Keyless Workload Identity for GCP & AWS)
    launchpad_oidc_issuer_url: str = "https://api.launchpad.yourdomain.com"
    launchpad_oidc_private_key: str | None = None
    launchpad_oidc_private_key_path: str | None = None
    launchpad_oidc_key_id: str = "launchpad-key-1"
    launchpad_oidc_token_ttl_seconds: int = 900



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

    # Local K8s cluster engine: "k3s" (default, via k3d) or "kind".
    # Override with LOCAL_K8S_ENGINE=kind to use Kubernetes-in-Docker instead.
    local_k8s_engine: str = "k3s"

    # Auto-manage local cluster for Dev / Launch → Local (runs scripts/kind-up|down.sh or k3s up)
    kind_auto_manage: bool = True
    kind_cluster_name: str = "launchpad"
    # Optional absolute path to the directory that contains kind-up.sh / kind-down.sh
    kind_scripts_dir: str | None = None

    # Portal status page base (simulate mode + /p/{id} deep links)
    preview_public_base_url: str = "http://localhost:3000"
    # Stable PR preview URL template. Use {pr} placeholder, e.g.
    # "https://pr-{pr}.preview.example.com". Empty → portal path /pr/{pr}.
    preview_pr_hostname_template: str | None = None
    # Smoke-test the preview URL before marking GitHub status success.
    preview_smoke_enabled: bool = True
    preview_smoke_timeout_seconds: float = 8.0

    # When kubernetes_enabled, Open Preview uses NodePort on this host.
    # Keep the range small (≤10 ports) — large maps often break kind on Docker Desktop.
    preview_node_host: str | None = None
    preview_node_port_min: int = 30080
    preview_node_port_max: int = 30089

    # Optional Ingress host template when kubernetes_enabled (e.g. "{name}.localtest.me")
    preview_ingress_host_template: str | None = None

    # Per-preview cloudflared quick tunnels so "Open app" works remotely, not just on
    # 127.0.0.1. "off" (default) keeps the NodePort URL + client-side host detection;
    # "cloudflared" starts a `cloudflared tunnel --url` quick tunnel per local preview
    # and uses its https://<random>.trycloudflare.com URL. No Cloudflare account needed.
    preview_tunnel_mode: str = "off"
    cloudflared_bin: str = "cloudflared"
    preview_tunnel_timeout_seconds: float = 30.0
    # Where the tunnel registry (pid/url per preview) is persisted; default ~/.launchpad
    preview_tunnel_state_dir: str | None = None

    # Cloud/production previews: how long to wait for a LoadBalancer/Ingress to get a
    # public address before falling back to the default preview URL.
    preview_cloud_url_timeout_seconds: float = 120.0

    # Launch Preview Analyzer (Gemini structured diagnostics)
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    preview_analyzer_heuristic_fallback: bool = True

    @property
    def local_cluster_tool(self) -> str:
        """CLI that manages the local cluster for the active engine (k3d or kind)."""
        return "k3d" if self.local_k8s_engine == "k3s" else "kind"

    @property
    def local_cluster_context(self) -> str:
        """kube-context the active local engine creates for ``kind_cluster_name``.

        Both k3d and kind derive a stable ``<tool>-<name>`` context, so switching
        ``LOCAL_K8S_ENGINE`` is enough to re-point every consumer.
        """
        prefix = "k3d" if self.local_k8s_engine == "k3s" else "kind"
        return f"{prefix}-{self.kind_cluster_name}"

    @property
    def resolved_kubernetes_context(self) -> str | None:
        ctx = self.kubernetes_context
        if ctx:
            # A context that still names the *other* engine's local cluster (or the
            # bare "k3s" sentinel) follows the active engine instead, so one env
            # var — LOCAL_K8S_ENGINE — flips both the runtime and its context.
            engine_local = {
                f"kind-{self.kind_cluster_name}",
                f"k3d-{self.kind_cluster_name}",
                "k3s",
                "kind",
            }
            if ctx in engine_local:
                return self.local_cluster_context
            return ctx
        return self.local_cluster_context

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
        "gitlab_oauth_client_id",
        "gitlab_oauth_client_secret",
        "webhook_secret",
        "kubernetes_kubeconfig_path",
        "kubernetes_context",
        "preview_ingress_host_template",
        "kind_scripts_dir",
        "preview_tunnel_state_dir",
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "smtp_from",
        "oidc_default_org_slug",
        "launchpad_oidc_private_key",
        "launchpad_oidc_private_key_path",
        "gemini_api_key",
        mode="before",
    )

    @classmethod
    def empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("local_k8s_engine", mode="before")
    @classmethod
    def normalize_local_k8s_engine(cls, value: object) -> str:
        if isinstance(value, str):
            val = value.strip().lower()
            if val in ("k3s", "k3d"):
                return "k3s"
            if val in ("kind", "local"):
                return "kind"
        return "k3s"

    @field_validator("preview_tunnel_mode", mode="before")
    @classmethod
    def normalize_preview_tunnel_mode(cls, value: object) -> str:
        if isinstance(value, str):
            val = value.strip().lower()
            if val in ("cloudflared", "cloudflare", "cf", "quick", "trycloudflare"):
                return "cloudflared"
        return "off"

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

    @model_validator(mode="after")
    def _compute_preview_node_host(self) -> "Settings":
        if self.preview_node_host is None:
            from urllib.parse import urlparse
            try:
                parsed = urlparse(self.preview_public_base_url)
                if parsed.hostname:
                    self.preview_node_host = parsed.hostname
                else:
                    self.preview_node_host = "127.0.0.1"
            except Exception:
                self.preview_node_host = "127.0.0.1"
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

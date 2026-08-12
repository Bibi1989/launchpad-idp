from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
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

    # Distributed state lock (Redis) - guards concurrent provision / rebuild / teardown
    state_lock_timeout_seconds: float = 900.0
    state_lock_blocking_timeout_seconds: float = 0.0
    # TEARDOWN state lock should expire quickly so worker restarts
    # can re-queue orphaned TEARDOWN_PENDING environments without waiting
    # for the full provisioning lock TTL.
    teardown_state_lock_timeout_seconds: float = 180.0

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    default_ttl_hours: int = 2
    cost_estimate_hourly: Decimal = Decimal("0.4200")
    provision_step_delay_seconds: float = 0.0

    # Preview governance (Launch / Environments)
    # Free tier: max running environments per project per user.
    # Pro tier: unlimited unless max_concurrent_environments_pro is set.
    max_concurrent_environments: int = 6
    max_concurrent_environments_pro: int | None = None
    preview_soft_cost_cap: Decimal = Decimal("25.00")
    ttl_extend_hours_default: int = 1
    ttl_warning_hours: int = 2
    # Total TTL hard cap from create (TTL extension cannot push past this).
    ttl_max_total_hours_from_create: int = 2

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
    kubernetes_ready_timeout_seconds: float = 180.0
    kubernetes_ready_poll_seconds: float = 1.0

    sse_poll_interval_seconds: float = 0.5
    ttl_reaper_interval_seconds: float = 300.0

    # Secrets encryption (Fernet key material - prefer 32+ char random string)
    secrets_encryption_key: str | None = None

    # Durable IaC / import workspaces. Never use /tmp: macOS and many hosts wipe it.
    iac_workspace_root: str = str(Path.home() / ".launchpad" / "workspaces")
    # Temporary clone root for repository import sessions (ok under /tmp; short-lived)
    repo_import_root: str = "/tmp/launchpad/imports"

    # Sandbox execution
    sandbox_docker_enabled: bool = False
    sandbox_image: str = "ghcr.io/launchpad-idp/sandbox:latest"
    sandbox_network_mode: str = "bridge"
    sandbox_memory_limit: str = "2g"
    sandbox_cpu_limit: float = 1.0

    # GitHub App (preferred) - short-lived installation tokens for repo bootstrap
    github_app_id: int | None = None
    github_app_private_key: str | None = None
    github_app_private_key_path: str | None = None
    github_app_slug: str | None = None
    github_app_installation_id: int | None = None
    # Where GitHub redirects after App install (configure as App "Setup URL")
    github_app_setup_url: str = "http://localhost:3000/integrations/github"

    # Deprecated: long-lived PAT fallback (prefer GitHub App)
    github_pat: str | None = None

    # GitLab - OAuth Application and/or per-user PAT (stored encrypted)
    gitlab_base_url: str = "https://gitlab.com"
    gitlab_oauth_client_id: str | None = None
    gitlab_oauth_client_secret: str | None = None
    gitlab_oauth_redirect_uri: str = "http://localhost:3000/integrations/gitlab"

    # GitHub webhook (GitOps rebuild) - HMAC SHA-256 shared secret
    webhook_secret: str | None = None

    # Auth (HS256 JWT; shape claims for future OIDC: sub, iss, email)
    jwt_secret: str = "dev-only-jwt-secret-change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24
    auth_dev_login_enabled: bool = True

    # OIDC (Authorization Code) - when enabled, /auth/oidc/* becomes available
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
    resend_api_key: str | None = None
    # Accept RESEND_FROM or common RESEND_FROM_EMAIL spelling from .env files.
    resend_from: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "RESEND_FROM",
            "RESEND_FROM_EMAIL",
            "resend_from",
            "resend_from_email",
        ),
    )

    # Public web app URL (Stripe redirects, email links)
    public_app_url: str = "http://localhost:3000"

    # Stripe SaaS (Pro plan)
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_id_pro: str | None = None

    # Default container image applied into ephemeral namespaces
    # Empty: image must come from workspace manifests, a build, or an explicit Launch override.
    default_workload_image: str = ""

    # Preview image build (clone repo + Dockerfile + kind load / registry push)
    preview_build_enabled: bool = False
    preview_build_dockerfile: str = "Dockerfile"
    preview_build_image_prefix: str = "launchpad-preview"
    preview_image_registry: str | None = None
    preview_build_kind_load: bool = True
    preview_build_timeout_seconds: float = 900.0
    # When true, docker build re-pulls base images (slower; useful for CI freshness).
    preview_build_pull_base: bool = False

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
    # Keep the range small (≤10 ports) - large maps often break kind on Docker Desktop.
    preview_node_host: str | None = None
    preview_node_port_min: int = 30080
    preview_node_port_max: int = 30089

    # Optional Ingress host template when kubernetes_enabled (e.g. "{name}.localtest.me")
    preview_ingress_host_template: str | None = None
    preview_ingress_class: str = "nginx"
    # Host port published by k3d for ingress-nginx HTTP (Cloudflare → this port).
    preview_ingress_http_port: int = 3080

    # Cloudflare Tunnel (homelab prod): public HTTPS/443 is routed straight to the
    # in-cluster Ingress controller, so preview URLs must be host-only (no NodePort)
    # and served over https. Toggle with USE_CLOUDFLARE_TUNNEL=true; ENVIRONMENT=
    # production enables it implicitly. NodePort URLs are retained only when neither
    # is active (local offline development).
    use_cloudflare_tunnel: bool = False
    # Wildcard base domain for per-workspace ingress hosts, e.g. "preview.mydomain.com"
    # (matches the *.preview.mydomain.com CNAME in Cloudflare DNS). Combined as
    # ws-{workspace_id}.{preview_base_domain}.
    preview_base_domain: str | None = None
    # Host port published by k3d for ingress-nginx HTTP (Cloudflare Tunnel target).
    preview_ingress_http_port: int = 3080

    # Per-preview cloudflared quick tunnels so "Open app" works remotely, not just on
    # 127.0.0.1. "off" (default) keeps the NodePort URL + client-side host detection;
    # "cloudflared" starts a `cloudflared tunnel --url` quick tunnel per local preview
    # and uses its https://<random>.trycloudflare.com URL. No Cloudflare account needed.
    preview_tunnel_mode: str = "off"
    cloudflared_bin: str = "cloudflared"
    preview_tunnel_timeout_seconds: float = 30.0
    # Where the tunnel registry (pid/url per preview) is persisted; default ~/.launchpad
    preview_tunnel_state_dir: str | None = None
    # Upstream host for quick tunnels. Inside Docker workers use host.docker.internal
    # so published host ports / NodePorts on the Docker host are reachable.
    preview_tunnel_upstream_host: str | None = None
    # Host alias / IP that in-cluster Ingress uses to reach Docker-published ports
    # (attach/compose → ws-* on the named Cloudflare Tunnel). k3d injects
    # host.k3d.internal; override with an IP when DNS is unavailable.
    preview_docker_host_alias: str = "host.k3d.internal"
    preview_docker_host_ip: str | None = None

    # Cloud/production previews: how long to wait for a LoadBalancer/Ingress to get a
    # public address before falling back to the default preview URL.
    preview_cloud_url_timeout_seconds: float = 120.0

    # Launch Preview Analyzer (Gemini structured diagnostics)
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    preview_analyzer_heuristic_fallback: bool = True

    # --- Hybrid local/edge agent nodes ---
    # Cadence the agent daemon uses for telemetry heartbeats over the reverse tunnel.
    agent_heartbeat_interval_seconds: int = 10
    # A node with no heartbeat newer than this is reported OFFLINE (a few missed beats).
    agent_offline_after_seconds: int = 35
    # How long the control plane waits for a dispatched command result from an agent.
    agent_command_timeout_seconds: float = 30.0
    # Enrollment (install) tokens are single-use and short lived.
    agent_enrollment_ttl_seconds: int = 900
    # Public origin agents reach the CONTROL PLANE (API) at - used for /install.sh,
    # the register endpoint, and WS URL derivation. This is the API origin, NOT the
    # web app: in dev set http://localhost:8000; in prod the tunnel/ingress host that
    # routes /install.sh and /api to the API. Falls back to the request origin, then
    # public_app_url. public_app_url (the Nuxt web app) is the wrong target and returns
    # an HTML 404 for /install.sh.
    agent_control_plane_url: str | None = None
    # Public wss:// origin agents dial back to. Defaults to the control-plane URL with ws(s) scheme.
    agent_ws_public_url: str | None = None
    # Container image the generated install.sh runs on the homelab host.
    agent_image: str = "ghcr.io/launchpad/agent:latest"
    # Guardrails applied to AI blueprints targeting a homelab/local node.
    agent_local_node_max_vcpu: float = 2.0
    agent_local_node_max_memory_mb: int = 8192

    # AI Infrastructure Provisioner (Gemini blueprint generation, heuristic fallback)
    ai_provisioner_heuristic_fallback: bool = True
    # Hours/month used to project an hourly rate-card estimate to a monthly figure.
    cost_hours_per_month: int = 730

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
            # var - LOCAL_K8S_ENGINE - flips both the runtime and its context.
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

    @field_validator("iac_workspace_root", mode="after")
    @classmethod
    def reject_ephemeral_iac_root(cls, value: str) -> str:
        """Keep workspaces off /tmp so imports survive reboot and OS cleanup."""
        raw = (value or "").strip() or str(Path.home() / ".launchpad" / "workspaces")
        path = Path(raw).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        tmp_roots = (
            Path("/tmp").resolve(),
            Path("/var/tmp").resolve(),
            Path("/private/tmp").resolve(),
        )
        if any(resolved == tmp or tmp in resolved.parents for tmp in tmp_roots):
            return str(Path.home() / ".launchpad" / "workspaces")
        return str(path)

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
        "resend_api_key",
        "resend_from",
        "stripe_secret_key",
        "stripe_webhook_secret",
        "stripe_price_id_pro",
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
        # Treat an unset (None), empty, or whitespace-only value as "not
        # configured" so an empty env var (e.g. PREVIEW_NODE_HOST= from
        # docker-compose) still gets backfilled instead of producing
        # "http://:30087" preview URLs.
        if self.preview_node_host is not None:
            self.preview_node_host = self.preview_node_host.strip()
        if not self.preview_node_host:
            # In Cloudflare Tunnel / production, public previews are host-only
            # (ws-*.domain). Never backfill the public apex as the NodePort host
            # or callers emit unreachable ``https://apex:2001`` links.
            if self.use_cloudflare_tunnel or self.environment.strip().lower() == "production":
                self.preview_node_host = "127.0.0.1"
            else:
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

    @model_validator(mode="after")
    def _validate_production_preview_ingress(self) -> "Settings":
        production_like = self.use_cloudflare_tunnel or self.environment.strip().lower() == "production"
        if production_like and not (self.preview_base_domain or "").strip():
            raise ValueError(
                "PREVIEW_BASE_DOMAIN is required when USE_CLOUDFLARE_TUNNEL=true or ENVIRONMENT=production"
            )
        return self

    @property
    def preview_tunnel_active(self) -> bool:
        """True when preview URLs must be host-only https (Cloudflare Tunnel / prod).

        Requires ``PREVIEW_BASE_DOMAIN`` so we never emit host-only ``ws-*`` URLs that
        resolve nowhere and show nginx ``404 page not found``. Local offline keeps
        NodePort URLs.
        """
        if not (self.preview_base_domain or "").strip():
            return False
        return self.use_cloudflare_tunnel or self.environment.strip().lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

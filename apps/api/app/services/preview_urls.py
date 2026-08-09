"""Stable preview URL helpers for PR-native environments."""

from __future__ import annotations

from urllib.parse import urlparse
from uuid import UUID

from app.core.config import Settings, get_settings

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "localtest.me"})


def workspace_ingress_preview_url(
    environment_id: UUID | str, *, settings: Settings | None = None
) -> str | None:
    """Host-only https preview URL: ``https://ws-{id}.{preview_base_domain}``."""
    cfg = settings or get_settings()
    base = (cfg.preview_base_domain or "").strip().strip(".")
    if not base:
        return None
    scheme = "https" if cfg.preview_tunnel_active else "http"
    return f"{scheme}://ws-{environment_id}.{base}"


def looks_like_broken_apex_node_port(url: str, *, apex: str) -> bool:
    """True for ``http://apex:2001`` / loopback NodePort URLs (not ws-* ingress)."""
    raw = (url or "").strip()
    apex = (apex or "").strip().strip(".").lower()
    if not raw or not apex:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if host.endswith(".trycloudflare.com"):
        return False
    if host.startswith("ws-") and host.endswith(f".{apex}"):
        return False
    if host == apex and port:
        return True
    if host in _LOOPBACK_HOSTS and port:
        return True
    if not host and port:
        return True
    return False


_K8S_DEPLOY_MODES = frozenset({"preview", "manifest"})


def deploy_mode_uses_workspace_ingress(deploy_mode: str | None) -> bool:
    """True when this mode creates ``ws-*`` Ingress (not Docker publish / attach)."""
    return (deploy_mode or "").strip().lower() in _K8S_DEPLOY_MODES


def repair_stored_preview_url(
    url: str | None,
    *,
    environment_id: UUID | str,
    deploy_mode: str | None = None,
    settings: Settings | None = None,
) -> str | None:
    """Replace apex:port / loopback NodePort with workspace ingress when configured.

    Applies whenever the named Cloudflare Tunnel + ``PREVIEW_BASE_DOMAIN`` are
    active. Kubernetes, attach, and compose all publish via ``ws-{id}.{domain}``
    (attach/compose through the Docker-host Ingress bridge).
    """
    if not url:
        return url
    _ = deploy_mode  # retained for call-site compatibility
    cfg = settings or get_settings()
    base = (cfg.preview_base_domain or "").strip().strip(".")
    if not base or not cfg.preview_tunnel_active:
        return url
    if not looks_like_broken_apex_node_port(url, apex=base):
        return url
    repaired = workspace_ingress_preview_url(environment_id, settings=cfg)
    return repaired or url


def portal_status_url(environment_id: UUID | str, *, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    base = cfg.preview_public_base_url.rstrip("/")
    return f"{base}/p/{environment_id}"


def portal_environment_url(environment_id: UUID | str, *, settings: Settings | None = None) -> str:
    """Deep link into the Launchpad environment detail page."""
    cfg = settings or get_settings()
    base = cfg.preview_public_base_url.rstrip("/")
    return f"{base}/environments/{environment_id}"


def stable_pr_preview_url(pr_number: int, *, settings: Settings | None = None) -> str:
    """Stable shareable URL for a PR preview.

    Prefer hostname template when configured (e.g. ``https://pr-{pr}.preview.example.com``),
    otherwise fall back to a path on the portal base (``/pr/{pr}``).
    """
    cfg = settings or get_settings()
    template = (cfg.preview_pr_hostname_template or "").strip()
    if template:
        formatted = template.format(pr=pr_number).rstrip("/")
        if not formatted.startswith(("http://", "https://")):
            formatted = f"https://{formatted}"
        return formatted
    base = cfg.preview_public_base_url.rstrip("/")
    return f"{base}/pr/{pr_number}"


def resolve_public_preview_url(
    *,
    app_url: str | None,
    pr_number: int | None,
    environment_id: UUID | str,
    settings: Settings | None = None,
) -> str:
    """Prefer live app URL, then stable PR URL, then portal status page."""
    if app_url:
        return app_url
    if pr_number is not None:
        return stable_pr_preview_url(pr_number, settings=settings)
    return portal_status_url(environment_id, settings=settings)

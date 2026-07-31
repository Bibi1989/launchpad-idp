"""Stable preview URL helpers for PR-native environments."""

from __future__ import annotations

from uuid import UUID

from app.core.config import Settings, get_settings


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

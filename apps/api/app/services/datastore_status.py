"""Derive UI-facing datastore status from environment lifecycle."""

from __future__ import annotations

from app.models.domain import EnvironmentStatus


def derive_datastore_status(
    *,
    enabled: bool,
    env_status: EnvironmentStatus | str,
    app_ready: bool = False,
) -> str | None:
    """Return ``pending`` / ``running`` / ``failed`` / ``stopped``, or None if disabled.

    Control-plane does not probe DB/Redis sockets yet; status follows the preview
    lifecycle so the UI can show whether the intended infra is live with the app.
    """
    if not enabled:
        return None
    status = env_status.value if hasattr(env_status, "value") else str(env_status or "")
    status = status.upper()
    if status == EnvironmentStatus.FAILED.value:
        return "failed"
    if status == EnvironmentStatus.PROVISIONING.value:
        return "pending"
    if status == EnvironmentStatus.RUNNING.value:
        return "running" if app_ready else "pending"
    if status in {
        EnvironmentStatus.PAUSED.value,
        EnvironmentStatus.EXPIRED.value,
        EnvironmentStatus.TEARDOWN_PENDING.value,
        EnvironmentStatus.DESTROYED.value,
    }:
        return "stopped"
    return "pending"

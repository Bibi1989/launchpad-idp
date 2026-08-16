"""Regression: provision must not use the short teardown lock TTL."""

from __future__ import annotations

from app.core.config import Settings
from app.workers.tasks import STALE_PROVISIONING_SECONDS


def test_provision_lock_outlives_teardown_grace_and_stale_reaper() -> None:
    settings = Settings()
    assert settings.provision_state_lock_timeout_seconds > settings.teardown_state_lock_timeout_seconds
    assert settings.provision_state_lock_timeout_seconds >= 1800
    # Soft limit fires first; lock and stale reaper must outlive it.
    assert settings.provision_state_lock_timeout_seconds > settings.celery_provision_soft_time_limit_seconds
    assert STALE_PROVISIONING_SECONDS > settings.provision_state_lock_timeout_seconds
    assert (
        settings.celery_provision_time_limit_seconds
        >= settings.celery_provision_soft_time_limit_seconds
    )


def test_init_attach_message_is_not_preview_only() -> None:
    from app.schemas.k8s import DeployMode
    from app.services.deploy_mode_routing import init_workflow_message

    msg = init_workflow_message(DeployMode.ATTACH.value)
    assert "preview workflow" not in msg.lower()
    assert "running-instance" in msg

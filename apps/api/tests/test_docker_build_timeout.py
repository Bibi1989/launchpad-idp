"""Docker build timeout helpers for workspace/cloud provision."""

from __future__ import annotations

from app.services.cloud_instance_compute import (
    CLOUD_CONTAINER_PLATFORM,
    format_docker_build_timeout_message,
    workspace_docker_build_timeout_seconds,
)


def test_workspace_docker_build_timeout_defaults(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("WORKSPACE_IMAGE_BUILD_TIMEOUT_SECONDS", "2400")
    get_settings.cache_clear()
    try:
        assert workspace_docker_build_timeout_seconds() == 2400.0
    finally:
        get_settings.cache_clear()


def test_format_docker_build_timeout_message_includes_context() -> None:
    msg = format_docker_build_timeout_message(
        timeout=2100,
        dockerfile="apps/virtual-office-frontend/Dockerfile",
        platform=CLOUD_CONTAINER_PLATFORM,
    )
    assert "timed out after 2100s" in msg
    assert "virtual-office-frontend/Dockerfile" in msg
    assert "linux/amd64" in msg

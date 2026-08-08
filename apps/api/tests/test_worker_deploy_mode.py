"""Tests for deploy-mode routing helpers used by the Celery worker."""

from __future__ import annotations

from app.schemas.k8s import DeployMode
from app.services.deploy_mode_routing import init_workflow_message, normalize_deploy_mode


def test_normalize_deploy_mode_accepts_attach_and_compose() -> None:
    assert normalize_deploy_mode("attach") == DeployMode.ATTACH.value
    assert normalize_deploy_mode("COMPOSE") == DeployMode.COMPOSE.value
    assert normalize_deploy_mode(" preview ") == DeployMode.PREVIEW.value
    assert normalize_deploy_mode(None) == DeployMode.PREVIEW.value
    assert normalize_deploy_mode("bogus") == DeployMode.PREVIEW.value


def test_init_workflow_message_is_runtime_aware() -> None:
    assert "Compose" in init_workflow_message(DeployMode.COMPOSE.value)
    assert "running-instance" in init_workflow_message(DeployMode.ATTACH.value)
    assert "Kubernetes" in init_workflow_message(DeployMode.PREVIEW.value)
    assert "manifest" in init_workflow_message(DeployMode.MANIFEST.value).lower()

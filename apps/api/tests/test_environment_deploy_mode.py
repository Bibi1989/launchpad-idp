from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.schemas.cloud import KubernetesPackaging
from app.schemas.environment import EnvironmentCreate
from app.schemas.k8s import DeployMode
from app.services.environment import EnvironmentService


def _payload(*, workspace_id, deploy_mode: DeployMode | None) -> EnvironmentCreate:
    # Minimal EnvironmentCreate object for testing _resolve_deploy_mode.
    ws_git_url = f"https://launchpad.local/workspaces/{workspace_id}"
    return EnvironmentCreate(
        name="ws-demo",
        git_branch="main",
        git_repo_url=ws_git_url,
        ttl_hours=1,
        workspace_id=workspace_id,
        template_id=None,
        provider="local",
        workload_image=None,
        deploy_mode=deploy_mode,
    )


def test_resolve_deploy_mode_helm_defaults_to_manifest() -> None:
    service = EnvironmentService(session=MagicMock())
    provisioning = MagicMock()
    provisioning.get_workspace_kubernetes_packaging.return_value = KubernetesPackaging.HELM

    workspace = MagicMock()
    payload = _payload(workspace_id=uuid4(), deploy_mode=None)

    deploy_mode, packaging = service._resolve_deploy_mode(payload, workspace, provisioning)
    assert deploy_mode == DeployMode.MANIFEST
    assert packaging == KubernetesPackaging.HELM.value


def test_resolve_deploy_mode_helm_explicit_preview_stays_preview() -> None:
    service = EnvironmentService(session=MagicMock())
    provisioning = MagicMock()
    provisioning.get_workspace_kubernetes_packaging.return_value = KubernetesPackaging.HELM

    workspace = MagicMock()
    payload = _payload(workspace_id=uuid4(), deploy_mode=DeployMode.PREVIEW)

    deploy_mode, packaging = service._resolve_deploy_mode(payload, workspace, provisioning)
    assert deploy_mode == DeployMode.PREVIEW
    assert packaging == KubernetesPackaging.HELM.value


def test_resolve_deploy_mode_helm_explicit_manifest_is_allowed() -> None:
    service = EnvironmentService(session=MagicMock())
    provisioning = MagicMock()
    provisioning.get_workspace_kubernetes_packaging.return_value = KubernetesPackaging.HELM

    workspace = MagicMock()
    payload = _payload(workspace_id=uuid4(), deploy_mode=DeployMode.MANIFEST)

    deploy_mode, packaging = service._resolve_deploy_mode(payload, workspace, provisioning)
    assert deploy_mode == DeployMode.MANIFEST
    assert packaging == KubernetesPackaging.HELM.value


def test_environment_read_supports_docker_compose() -> None:
    from datetime import datetime, timezone
    from decimal import Decimal
    from app.schemas.environment import EnvironmentRead, EnvironmentStatus

    now = datetime.now(timezone.utc)
    env_dict = {
        "id": uuid4(),
        "owner_id": uuid4(),
        "workspace_id": None,
        "name": "docker-compose-env",
        "git_branch": "main",
        "git_repo_url": "https://example.com/repo",
        "status": EnvironmentStatus.RUNNING,
        "namespace_name": "ns-test",
        "deploy_mode": "docker_compose",
        "cost_estimate_hourly": Decimal("0.00"),
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    read_obj = EnvironmentRead.model_validate(env_dict)
    assert read_obj.deploy_mode == DeployMode.DOCKER_COMPOSE_UNDERSCORE

    env_dict["deploy_mode"] = "docker-compose"
    read_obj_dash = EnvironmentRead.model_validate(env_dict)
    assert read_obj_dash.deploy_mode == DeployMode.DOCKER_COMPOSE




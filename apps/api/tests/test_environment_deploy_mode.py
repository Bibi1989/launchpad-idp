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


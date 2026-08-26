"""Kubernetes client loading must not silently fall back to current-context."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.services.kubernetes import KubernetesProvisioner


def test_load_clients_does_not_fall_back_when_requested_context_missing() -> None:
    """Local launches must not inherit kubectl current-context (often remote GKE)."""
    settings = Settings(
        kubernetes_enabled=True,
        kubernetes_in_cluster=False,
        kubernetes_context="k3d-launchpad",
        local_k8s_engine="k3s",
        kind_cluster_name="launchpad",
        _env_file=None,
    )
    with patch("kubernetes.config.load_kube_config", side_effect=Exception("context not found")):
        provisioner = KubernetesProvisioner(settings)
    assert provisioner._core is None


def test_assert_cluster_ready_raises_when_client_missing() -> None:
    provisioner = KubernetesProvisioner(
        Settings(kubernetes_enabled=False, _env_file=None)
    )
    provisioner._settings = Settings(
        kubernetes_enabled=True,
        kubernetes_context="k3d-launchpad",
        local_k8s_engine="k3s",
        _env_file=None,
    )
    provisioner._core = None
    with pytest.raises(RuntimeError, match="Kubernetes client not connected"):
        provisioner.assert_cluster_ready(timeout_seconds=1.0)


def test_assert_cluster_ready_uses_request_timeout() -> None:
    provisioner = KubernetesProvisioner(
        Settings(kubernetes_enabled=False, _env_file=None)
    )
    provisioner._settings = Settings(
        kubernetes_enabled=True,
        kubernetes_context="k3d-launchpad",
        local_k8s_engine="k3s",
        _env_file=None,
    )
    core = MagicMock()
    provisioner._core = core
    provisioner.assert_cluster_ready(timeout_seconds=3.0)
    core.list_namespace.assert_called_once_with(limit=1, _request_timeout=3.0)


def test_retarget_loads_cloud_kubeconfig_not_local_context(tmp_path) -> None:
    settings = Settings(
        kubernetes_enabled=True,
        kubernetes_in_cluster=False,
        kubernetes_context="k3d-launchpad",
        local_k8s_engine="k3s",
        kind_cluster_name="launchpad",
        _env_file=None,
    )
    kubeconfig = tmp_path / "gke.yaml"
    kubeconfig.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_load(**kwargs):
        captured.update(kwargs)

    with (
        patch("kubernetes.config.load_kube_config", side_effect=fake_load),
        patch("kubernetes.client.Configuration.get_default_copy", return_value=MagicMock()),
        patch("kubernetes.client.Configuration.set_default"),
        patch("kubernetes.client.CoreV1Api", return_value=MagicMock()),
        patch("kubernetes.client.NetworkingV1Api", return_value=MagicMock()),
        patch("kubernetes.client.AppsV1Api", return_value=MagicMock()),
        patch("kubernetes.client.AutoscalingV2Api", return_value=MagicMock()),
    ):
        provisioner = KubernetesProvisioner(settings)
        provisioner.retarget(
            kubeconfig_path=str(kubeconfig),
            kube_context="gke_launchpad-504012_europe-west3_launchpad-previews",
        )
    assert captured.get("config_file") == str(kubeconfig)
    assert captured.get("context") == "gke_launchpad-504012_europe-west3_launchpad-previews"
    assert provisioner.remote_cluster is True


@pytest.mark.asyncio
async def test_retarget_provisioner_skips_when_provider_is_local(tmp_path) -> None:
    """When environment or workspace provider is local, retargeting to cloud must be skipped."""
    from uuid import UUID
    from unittest.mock import AsyncMock, MagicMock
    from app.models.domain import Environment
    from app.workers.tasks import _retarget_provisioner_for_cloud_k8s

    settings = Settings(kubernetes_enabled=True)
    provisioner = KubernetesProvisioner(settings)
    assert provisioner.remote_cluster is False

    session = AsyncMock()
    env = Environment(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        provider="local",
        deploy_mode="preview",
    )
    result = await _retarget_provisioner_for_cloud_k8s(
        session,
        environment=env,
        provisioner=provisioner,
        deploy_mode="preview",
        create_cluster=False,
    )
    assert result is provisioner
    assert result.remote_cluster is False


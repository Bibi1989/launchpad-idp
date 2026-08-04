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

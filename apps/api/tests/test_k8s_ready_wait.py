"""Deployment readiness must track the current revision, not a stale Ready pod."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.services.kubernetes import KubernetesProvisioner


def _deployment(*, generation=1, observed=1, ready=1, updated=1, unavailable=0, replicas=1):
    return SimpleNamespace(
        metadata=SimpleNamespace(generation=generation),
        spec=SimpleNamespace(replicas=replicas),
        status=SimpleNamespace(
            ready_replicas=ready,
            updated_replicas=updated,
            unavailable_replicas=unavailable,
            observed_generation=observed,
        ),
    )


def test_wait_for_workload_ready_rejects_stale_ready_replicas() -> None:
    settings = Settings(kubernetes_enabled=True, kubernetes_ready_timeout_seconds=0.05, kubernetes_ready_poll_seconds=0.01)
    provisioner = KubernetesProvisioner(settings)
    provisioner._apps = MagicMock()
    provisioner._core = MagicMock()
    # Old revision still Ready; new revision not updated yet.
    provisioner._apps.read_namespaced_deployment.return_value = _deployment(
        ready=1, updated=0, unavailable=1
    )
    provisioner._core.list_namespaced_pod.return_value = SimpleNamespace(items=[])

    with pytest.raises(TimeoutError, match="current revision"):
        provisioner.wait_for_workload_ready(namespace="ns", timeout_seconds=0.05, expected_image="dhi.io/build:2-source")


def test_wait_for_workload_ready_fails_fast_on_image_pull_error() -> None:
    settings = Settings(kubernetes_enabled=True, kubernetes_ready_timeout_seconds=5, kubernetes_ready_poll_seconds=0.01)
    provisioner = KubernetesProvisioner(settings)
    provisioner._apps = MagicMock()
    provisioner._core = MagicMock()
    provisioner._apps.read_namespaced_deployment.return_value = _deployment(ready=0, updated=0, unavailable=1)

    waiting = SimpleNamespace(reason="ErrImagePull", message="401 Unauthorized")
    container_status = SimpleNamespace(
        image="dhi.io/build:2-source",
        state=SimpleNamespace(waiting=waiting),
        ready=False,
    )
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="app-1"),
        status=SimpleNamespace(container_statuses=[container_status], phase="Pending"),
    )
    provisioner._core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

    with pytest.raises(RuntimeError, match="Failed to pull image"):
        provisioner.wait_for_workload_ready(namespace="ns", timeout_seconds=5, expected_image="dhi.io/build:2-source")

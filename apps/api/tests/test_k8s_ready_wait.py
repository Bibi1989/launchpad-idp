"""Deployment readiness aggregates across ALL namespace deployments.

Workspaces no longer ship a single hardcoded ``app`` Deployment - they may ship
``launch-web``, ``launch-server``, ``postgres``, etc. Readiness must list all
Deployments and wait for every one to complete its current-revision rollout,
never ``read_namespaced_deployment("app")`` (which 404s for launch-* workspaces).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.services.kubernetes import KubernetesProvisioner


def _deployment(
    name="app", *, generation=1, observed=1, ready=1, updated=1, unavailable=0, replicas=1, total=None
):
    status = SimpleNamespace(
        ready_replicas=ready,
        updated_replicas=updated,
        unavailable_replicas=unavailable,
        observed_generation=observed,
        replicas=(total if total is not None else updated),
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, generation=generation),
        spec=SimpleNamespace(replicas=replicas),
        status=status,
    )


def _provisioner(*, deployments, pods=None, timeout=0.05, grace=0.0):
    settings = Settings(
        kubernetes_enabled=True,
        kubernetes_ready_timeout_seconds=timeout,
        kubernetes_ready_poll_seconds=0.01,
        kubernetes_ready_grace_seconds=grace,
    )
    prov = KubernetesProvisioner(settings)
    prov._apps = MagicMock()
    prov._core = MagicMock()
    prov._apps.list_namespaced_deployment.return_value = SimpleNamespace(items=deployments)
    prov._core.list_namespaced_pod.return_value = SimpleNamespace(items=pods or [])
    # control-plane hint probe (kube-system) returns nothing
    return prov


def test_all_deployments_ready_marks_ready() -> None:
    prov = _provisioner(
        deployments=[
            _deployment("launch-web", ready=1, updated=1, replicas=1),
            _deployment("launch-server", ready=1, updated=1, replicas=1),
            _deployment("postgres", ready=1, updated=1, replicas=1),
        ],
        timeout=5,
    )
    # Does not raise -> all deployments Ready on the first stable poll.
    prov.wait_for_workload_ready(namespace="lp-shop", timeout_seconds=5)


def test_ready_returns_on_first_stable_poll() -> None:
    """With required_stable_polls=1, Ready on the first observation succeeds."""
    calls = {"n": 0}

    def _snapshot(**_kwargs):
        calls["n"] += 1
        return {
            "desired": 1,
            "ready": 1,
            "updated": 1,
            "unavailable": 0,
            "revision_ready": True,
            "deployment_count": 1,
            "pending": [],
        }

    prov = _provisioner(deployments=[_deployment("launch-web")], timeout=5)
    prov._deployment_ready_snapshot = _snapshot  # type: ignore[method-assign]
    prov.wait_for_workload_ready(namespace="lp-shop", timeout_seconds=5)
    assert calls["n"] == 1



def test_one_deployment_not_ready_times_out_with_pending() -> None:
    prov = _provisioner(
        deployments=[
            _deployment("launch-web", ready=1, updated=1, replicas=1),
            _deployment("launch-server", ready=0, updated=0, unavailable=1, replicas=1),
        ],
    )
    with pytest.raises(TimeoutError, match="launch-server"):
        prov.wait_for_workload_ready(namespace="lp-shop", timeout_seconds=0.05)


def test_lingering_old_replicaset_not_counted_ready() -> None:
    # updated=1 but total replicas=2 (old nginx pod lingering) -> not complete.
    prov = _provisioner(
        deployments=[_deployment("launch-web", ready=1, updated=1, unavailable=0, replicas=1, total=2)],
    )
    with pytest.raises(TimeoutError, match="current revision"):
        prov.wait_for_workload_ready(namespace="lp-shop", timeout_seconds=0.05)


def test_empty_namespace_is_not_ready() -> None:
    prov = _provisioner(deployments=[])
    with pytest.raises(TimeoutError):
        prov.wait_for_workload_ready(namespace="lp-shop", timeout_seconds=0.05)


def test_scaled_to_zero_deployment_counts_ready() -> None:
    # A paused/scaled-to-0 datastore must not block readiness.
    prov = _provisioner(
        deployments=[
            _deployment("launch-web", ready=1, updated=1, replicas=1),
            _deployment("redis", ready=0, updated=0, replicas=0),
        ],
        timeout=5,
    )
    prov.wait_for_workload_ready(namespace="lp-shop", timeout_seconds=5)


def test_fails_fast_on_image_pull_error_any_deployment() -> None:
    waiting = SimpleNamespace(reason="ErrImagePull", message="401 Unauthorized")
    container_status = SimpleNamespace(
        name="launch-server",
        image="server:latest",
        state=SimpleNamespace(waiting=waiting),
        ready=False,
    )
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="launch-server-1"),
        status=SimpleNamespace(container_statuses=[container_status], phase="Pending"),
    )
    prov = _provisioner(
        deployments=[_deployment("launch-server", ready=0, updated=0, unavailable=1)],
        pods=[pod],
        timeout=5,
    )
    with pytest.raises(RuntimeError, match="Failed to pull image"):
        prov.wait_for_workload_ready(namespace="lp-shop", timeout_seconds=5)


def test_grace_window_catches_late_ready() -> None:
    """Pods that turn Ready just after the primary deadline succeed via the grace window."""
    calls = {"n": 0}

    def _snapshot(**_kwargs):
        calls["n"] += 1
        ready = calls["n"] >= 3  # not ready until the grace window
        return {
            "desired": 1,
            "ready": 1 if ready else 0,
            "updated": 1,
            "unavailable": 0 if ready else 1,
            "revision_ready": ready,
            "deployment_count": 1,
            "pending": [] if ready else ["launch-web"],
        }

    prov = _provisioner(deployments=[_deployment("launch-web")], timeout=0.02, grace=5.0)
    prov._deployment_ready_snapshot = _snapshot  # type: ignore[method-assign]
    prov._first_pod_image_error = lambda **_k: None  # type: ignore[method-assign]
    prov._first_pod_crash_error = lambda **_k: None  # type: ignore[method-assign]
    # Does not raise: becomes Ready during the grace window.
    prov.wait_for_workload_ready(namespace="lp-shop", timeout_seconds=0.02)
    assert calls["n"] >= 3


def _pod(name="launch-web-1", *, phase="Running", app="launch-web"):
    state = SimpleNamespace(waiting=None, terminated=None, running=SimpleNamespace())
    cs = SimpleNamespace(
        name="app",
        image="app:latest",
        state=state,
        last_state=SimpleNamespace(terminated=None),
        ready=False,
        restart_count=0,
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels={"app": app}, deletion_timestamp=None),
        status=SimpleNamespace(phase=phase, conditions=[], container_statuses=[cs]),
    )


def test_timeout_error_includes_pod_logs() -> None:
    """A Running-but-not-Ready pod surfaces its logs so the failure is diagnosable."""
    prov = _provisioner(
        deployments=[_deployment("launch-web", ready=0, updated=1, unavailable=1)],
        pods=[_pod()],
        timeout=0.02,
    )
    prov._core.read_namespaced_pod_log.return_value = (
        "> app@1.0.0 start\nServer listening on http://0.0.0.0:3000"
    )
    with pytest.raises(TimeoutError) as excinfo:
        prov.wait_for_workload_ready(namespace="lp-shop", timeout_seconds=0.02)
    msg = str(excinfo.value)
    # The port the app actually bound (3000) is now visible in the failure.
    assert "3000" in msg
    assert "launch-web-1" in msg

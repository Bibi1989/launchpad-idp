"""Readiness wait must ignore pods from superseded ReplicaSets.

Regression for a linked-repo preview stuck as ``Provision failed ... CrashLoopBackOff
(exit 127)`` on a STALE pod: repeated deploys left an old ReplicaSet's pod
crash-looping (from a since-fixed image), and the wait failed on it even though the
current rollout was healthy. The wait is now scoped to the current rollout's
pod-template-hash(es).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.config import Settings
from app.services.kubernetes import KubernetesProvisioner


def _provisioner() -> KubernetesProvisioner:
    settings = Settings(kubernetes_enabled=True, _env_file=None)  # type: ignore[arg-type]
    prov = KubernetesProvisioner(settings)
    prov._core = MagicMock()
    prov._apps = MagicMock()
    return prov


def _rs(name: str, app: str, pth: str, replicas: int):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels={"app": app, "pod-template-hash": pth}),
        spec=SimpleNamespace(replicas=replicas),
    )


def _pod(app: str, pth: str, *, terminating: bool = False):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=f"{app}-{pth}-x",
            labels={"app": app, "pod-template-hash": pth},
            deletion_timestamp="now" if terminating else None,
        ),
        status=SimpleNamespace(container_statuses=[]),
    )


def test_active_hashes_only_include_scaled_up_replicasets() -> None:
    prov = _provisioner()
    prov._apps.list_namespaced_replica_set.return_value = SimpleNamespace(
        items=[
            _rs("fe-old", "launch-test-frontend", "59d8d944d9", replicas=0),  # superseded
            _rs("fe-new", "launch-test-frontend", "6d6d97db9d", replicas=1),  # current
            _rs("be", "launch-test-backend", "abc123", replicas=1),  # other app
        ]
    )
    hashes = prov._active_pod_template_hashes("ns", "launch-test-frontend")
    assert hashes == {"6d6d97db9d"}  # only the current, scaled-up frontend RS


def test_pod_from_superseded_replicaset_is_out_of_scope() -> None:
    prov = _provisioner()
    active = {"6d6d97db9d"}
    stale = _pod("launch-test-frontend", "59d8d944d9")
    current = _pod("launch-test-frontend", "6d6d97db9d")
    assert (
        prov._pod_matches_wait_scope(
            stale, app_label="launch-test-frontend", expected_image=None, active_hashes=active
        )
        is False
    )
    assert (
        prov._pod_matches_wait_scope(
            current, app_label="launch-test-frontend", expected_image=None, active_hashes=active
        )
        is True
    )


def test_terminating_pod_is_out_of_scope() -> None:
    prov = _provisioner()
    term = _pod("launch-test-frontend", "6d6d97db9d", terminating=True)
    assert (
        prov._pod_matches_wait_scope(
            term, app_label="launch-test-frontend", expected_image=None, active_hashes={"6d6d97db9d"}
        )
        is False
    )


def test_crash_error_ignores_stale_replicaset_pod() -> None:
    """A crash-looping pod from a superseded ReplicaSet must not fail the wait."""
    prov = _provisioner()
    prov._apps.list_namespaced_replica_set.return_value = SimpleNamespace(
        items=[
            _rs("fe-old", "launch-test-frontend", "OLD", replicas=0),
            _rs("fe-new", "launch-test-frontend", "NEW", replicas=1),
        ]
    )
    # Stale pod (OLD hash) is crash-looping with 43 restarts; current pod (NEW) is fine.
    stale = _pod("launch-test-frontend", "OLD")
    stale.status = SimpleNamespace(
        container_statuses=[
            SimpleNamespace(
                name="launch-test-frontend",
                restart_count=43,
                state=SimpleNamespace(
                    waiting=SimpleNamespace(reason="CrashLoopBackOff", message="back-off"),
                    terminated=None,
                ),
                last_state=SimpleNamespace(
                    terminated=SimpleNamespace(exit_code=127, reason="Error", message="")
                ),
            )
        ]
    )
    current = _pod("launch-test-frontend", "NEW")
    prov._core.list_namespaced_pod.return_value = SimpleNamespace(items=[stale, current])

    err = prov._first_pod_crash_error(namespace="ns", app_label="launch-test-frontend")
    assert err is None  # the stale pod is out of scope, so no crash is reported

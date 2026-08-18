"""Retry recovery when a namespace is stuck in ``Terminating``.

A retry can land while a prior teardown / failed provision is still deleting the
namespace. Creating resources in a Terminating namespace is rejected by the API
server, so ``_ensure_namespace_exists`` must wait for it to clear and then recreate
it - otherwise retries fail on "namespace is terminating" and never recover.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.services.kubernetes import KubernetesProvisioner, ProvisionedResources


def _ns(phase: str):
    return SimpleNamespace(status=SimpleNamespace(phase=phase))


def _provisioner(*, wait=5.0, poll=0.01):
    settings = Settings(
        kubernetes_enabled=True,
        kubernetes_ready_poll_seconds=poll,
        kubernetes_namespace_terminating_wait_seconds=wait,
    )
    prov = KubernetesProvisioner(settings)
    prov._core = MagicMock()
    return prov


def test_terminating_namespace_waits_then_recreates() -> None:
    from kubernetes.client.rest import ApiException

    prov = _provisioner()
    # read_namespace: Terminating, Terminating, then 404 (gone) for the wait loop.
    prov._core.read_namespace.side_effect = [
        _ns("Terminating"),  # initial ensure check
        _ns("Terminating"),  # wait poll 1
        ApiException(status=404),  # wait poll 2 -> deleted
    ]
    resources = ProvisionedResources(namespace="lp-shop")
    prov._ensure_namespace_exists("lp-shop", {"app": "x"}, resources)
    # Recreated fresh after the terminating namespace cleared.
    prov._core.create_namespace.assert_called_once()
    assert resources.created_namespace is True


def test_terminating_namespace_stuck_raises_clear_error() -> None:
    prov = _provisioner(wait=0.05)
    # Never clears -> always Terminating.
    prov._core.read_namespace.return_value = _ns("Terminating")
    resources = ProvisionedResources(namespace="lp-shop")
    with pytest.raises(RuntimeError, match="stuck in Terminating"):
        prov._ensure_namespace_exists("lp-shop", {"app": "x"}, resources)
    prov._core.create_namespace.assert_not_called()


def test_active_namespace_is_left_untouched() -> None:
    prov = _provisioner()
    prov._core.read_namespace.return_value = _ns("Active")
    resources = ProvisionedResources(namespace="lp-shop")
    prov._ensure_namespace_exists("lp-shop", {"app": "x"}, resources)
    prov._core.create_namespace.assert_not_called()
    assert resources.created_namespace is False


def _svc(namespace: str, name: str, *, node_port: int | None, svc_type: str = "NodePort"):
    ports = [SimpleNamespace(node_port=node_port)] if node_port else []
    return SimpleNamespace(
        metadata=SimpleNamespace(namespace=namespace, name=name),
        spec=SimpleNamespace(type=svc_type, ports=ports),
    )


def test_reclaim_frees_node_ports_from_terminating_namespaces() -> None:
    prov = _provisioner()
    prov._core.list_service_for_all_namespaces.return_value = SimpleNamespace(
        items=[
            _svc("launchpad-env-dead", "app", node_port=30081),      # Terminating -> reclaim
            _svc("launchpad-env-live", "app", node_port=30082),      # Active -> keep
            _svc("launchpad-env-cur", "app", node_port=30083),       # excluded (current) -> keep
            _svc("kube-system", "kube-dns", node_port=30084),        # not a preview ns -> keep
            _svc("launchpad-env-cluster", "db", node_port=None, svc_type="ClusterIP"),  # not NodePort
        ]
    )

    def _read_ns(ns, **_kw):
        return _ns("Terminating" if ns == "launchpad-env-dead" else "Active")

    prov._core.read_namespace.side_effect = _read_ns
    freed = prov.reclaim_orphaned_preview_node_ports(exclude_namespace="launchpad-env-cur")
    assert freed == 1
    prov._core.delete_namespaced_service.assert_called_once_with(
        "app", "launchpad-env-dead", _request_timeout=10
    )


def test_reclaim_treats_missing_namespace_as_free() -> None:
    from kubernetes.client.rest import ApiException

    prov = _provisioner()
    prov._core.list_service_for_all_namespaces.return_value = SimpleNamespace(
        items=[_svc("launchpad-env-gone", "app", node_port=30081)]
    )
    prov._core.read_namespace.side_effect = ApiException(status=404)
    freed = prov.reclaim_orphaned_preview_node_ports()
    assert freed == 1

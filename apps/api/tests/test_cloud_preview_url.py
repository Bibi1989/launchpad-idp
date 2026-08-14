"""Cloud/production previews resolve their public URL from the cluster.

The provisioner reads a LoadBalancer Service or Ingress external address so a cloud
preview's Open-app link is the real production URL - never a NodePort/loopback guess.
Local previews are unaffected (they keep localhost / the cloudflared tunnel).
"""

from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.services.kubernetes import (
    KubernetesProvisioner,
    _external_url,
    _lb_ingress_address,
)


def test_external_url_scheme_and_ports() -> None:
    assert _external_url("1.2.3.4", 80) == "http://1.2.3.4"
    assert _external_url("host", 443) == "https://host"
    assert _external_url("1.2.3.4", 8080) == "http://1.2.3.4:8080"
    assert _external_url("host", None) == "http://host"


def test_lb_ingress_address_prefers_hostname() -> None:
    status = NS(load_balancer=NS(ingress=[NS(hostname="x.elb.amazonaws.com", ip="9.9.9.9")]))
    assert _lb_ingress_address(status) == "x.elb.amazonaws.com"
    status_ip = NS(load_balancer=NS(ingress=[NS(hostname=None, ip="34.120.10.5")]))
    assert _lb_ingress_address(status_ip) == "34.120.10.5"
    assert _lb_ingress_address(NS(load_balancer=None)) is None


def _provisioner_with_mocks() -> KubernetesProvisioner:
    # kubernetes_enabled=False → __init__ skips the cluster connection (_core stays None).
    prov = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    prov._settings = Settings(kubernetes_enabled=True)
    prov._core = MagicMock()
    prov._networking = MagicMock()
    return prov


def test_resolves_loadbalancer_service_url() -> None:
    prov = _provisioner_with_mocks()
    svc = NS(
        spec=NS(type="LoadBalancer", ports=[NS(port=80)]),
        status=NS(load_balancer=NS(ingress=[NS(hostname="lb.elb.amazonaws.com", ip=None)])),
    )
    prov._core.list_namespaced_service.return_value = NS(items=[svc])
    prov._networking.list_namespaced_ingress.return_value = NS(items=[])

    assert prov.resolve_external_preview_url("ns", timeout_seconds=0) == "http://lb.elb.amazonaws.com"


def test_resolves_loadbalancer_ip_with_port() -> None:
    prov = _provisioner_with_mocks()
    svc = NS(
        spec=NS(type="LoadBalancer", ports=[NS(port=8080)]),
        status=NS(load_balancer=NS(ingress=[NS(hostname=None, ip="34.120.10.5")])),
    )
    prov._core.list_namespaced_service.return_value = NS(items=[svc])
    prov._networking.list_namespaced_ingress.return_value = NS(items=[])

    assert prov.resolve_external_preview_url("ns", timeout_seconds=0) == "http://34.120.10.5:8080"


def test_resolves_ingress_host_https_when_tls() -> None:
    prov = _provisioner_with_mocks()
    ing = NS(spec=NS(tls=[NS()], rules=[NS(host="preview.example.com")]), status=NS(load_balancer=None))
    prov._core.list_namespaced_service.return_value = NS(items=[])
    prov._networking.list_namespaced_ingress.return_value = NS(items=[ing])

    assert prov.resolve_external_preview_url("ns", timeout_seconds=0) == "https://preview.example.com"


def test_ignores_local_preview_ingress_when_lb_pending() -> None:
    """ws-* hosts must not win over a pending cloud LoadBalancer."""
    prov = _provisioner_with_mocks()
    prov._settings = Settings(
        kubernetes_enabled=True,
        preview_base_domain="launchpad-idp.online",
        preview_tunnel_mode="cloudflared",
    )
    pending_lb = NS(
        metadata=NS(name="app"),
        spec=NS(type="LoadBalancer", ports=[NS(port=80)]),
        status=NS(load_balancer=NS(ingress=None)),
    )
    local_ing = NS(
        spec=NS(tls=None, rules=[NS(host="ws-abc.launchpad-idp.online")]),
        status=NS(load_balancer=None),
    )
    prov._core.list_namespaced_service.return_value = NS(items=[pending_lb])
    # Keep returning pending so the loop exits on timeout without a URL.
    prov._networking.list_namespaced_ingress.return_value = NS(items=[local_ing])

    assert prov.resolve_external_preview_url("ns", timeout_seconds=0) is None


def test_workspace_preview_host_none_on_remote_cluster() -> None:
    prov = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    prov._settings = Settings(
        kubernetes_enabled=True,
        preview_base_domain="launchpad-idp.online",
        preview_tunnel_mode="cloudflared",
    )
    prov._remote_cluster = True
    assert (
        prov.workspace_preview_host(
            name="demo", environment_id="abc", namespace="ns"
        )
        is None
    )


def test_returns_none_when_nothing_exposed() -> None:
    prov = _provisioner_with_mocks()
    prov._core.list_namespaced_service.return_value = NS(items=[])
    prov._networking.list_namespaced_ingress.return_value = NS(items=[])

    assert prov.resolve_external_preview_url("ns", timeout_seconds=5) is None


def test_disabled_returns_none() -> None:
    prov = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    assert prov.resolve_external_preview_url("ns", timeout_seconds=0) is None

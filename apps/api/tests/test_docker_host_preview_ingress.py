from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.services.kubernetes import KubernetesProvisioner


def _provisioner(**overrides: object) -> KubernetesProvisioner:
    base: dict[str, object] = {
        "kubernetes_enabled": True,
        "use_cloudflare_tunnel": True,
        "environment": "production",
        "preview_base_domain": "launchpad-idp.online",
        "preview_docker_host_ip": "172.18.0.1",
        "_env_file": None,
    }
    base.update(overrides)
    settings = Settings(**base)  # type: ignore[arg-type]
    prov = KubernetesProvisioner(settings)
    prov._core = MagicMock()
    prov._networking = MagicMock()
    return prov


def test_apply_docker_host_preview_ingress_sets_ws_url() -> None:
    from kubernetes.client.rest import ApiException

    prov = _provisioner()
    missing = ApiException(status=404)
    prov._core.read_namespace.side_effect = missing
    prov._core.read_namespaced_service.side_effect = missing
    prov._core.read_namespaced_endpoints.side_effect = missing
    prov._networking.read_namespaced_ingress.side_effect = missing

    url = prov.apply_docker_host_preview_ingress(
        namespace="launchpad-env-e8f9cf54",
        environment_id="e8f9cf54-60c2-4556-8e45-2b654ea4e976",
        name="demo",
        host_port=8090,
    )
    assert url == "https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online"
    prov._core.create_namespace.assert_called_once()
    prov._core.create_namespaced_service.assert_called_once()
    prov._core.create_namespaced_endpoints.assert_called_once()
    endpoints_body = prov._core.create_namespaced_endpoints.call_args.args[1]
    assert endpoints_body.subsets[0].addresses[0].ip == "172.18.0.1"
    assert endpoints_body.subsets[0].ports[0].port == 8090
    prov._networking.create_namespaced_ingress.assert_called_once()
    ingress_body = prov._networking.create_namespaced_ingress.call_args.args[1]
    assert (
        ingress_body.spec.rules[0].host
        == "ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online"
    )


def test_apply_docker_host_preview_ingress_skips_without_tunnel() -> None:
    prov = _provisioner(use_cloudflare_tunnel=False, environment="development")
    assert (
        prov.apply_docker_host_preview_ingress(
            namespace="ns",
            environment_id="e8f9cf54-60c2-4556-8e45-2b654ea4e976",
            name="demo",
            host_port=8090,
        )
        is None
    )


def test_resolve_docker_host_gateway_ip_prefers_explicit() -> None:
    prov = _provisioner(preview_docker_host_ip="10.0.0.9")
    assert prov.resolve_docker_host_gateway_ip() == "10.0.0.9"


@pytest.mark.asyncio
async def test_attach_docker_host_preview_helper_rewrites_attach_url() -> None:
    from app.workers import tasks as tasks_mod

    resources = SimpleNamespace(
        namespace="ns-1",
        node_port=8090,
        preview_url="http://127.0.0.1:8090",
        preview_endpoints=[
            {"name": "web", "app_kind": "frontend", "url": "http://127.0.0.1:8090", "port": 8090},
            {"name": "api", "app_kind": "backend", "url": "http://127.0.0.1:8083", "port": 8083},
        ],
        labels={},
    )
    environment = SimpleNamespace(
        deploy_mode="attach",
        provider="local",
        name="demo",
        namespace_name="ns-1",
    )
    provisioner = MagicMock()
    provisioner.apply_docker_host_preview_ingress.return_value = (
        "https://ws-aaa.launchpad-idp.online"
    )

    await tasks_mod._attach_docker_host_preview_ingress(
        "aaa", environment, resources, provisioner
    )
    assert resources.preview_url == "https://ws-aaa.launchpad-idp.online"
    assert resources.preview_endpoints[0]["url"] == "https://ws-aaa.launchpad-idp.online"
    assert resources.preview_endpoints[1]["url"] == "http://127.0.0.1:8083"

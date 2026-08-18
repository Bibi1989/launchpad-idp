"""Dynamic preview ingress route generator: pr-{id}.preview.{base} subdomains."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.preview_route import (
    generate_preview_ingress,
    preview_subdomain_host,
    preview_subdomain_url,
)


def _settings(**over) -> Settings:
    base = {
        "preview_base_domain": "preview.launchpad.domain",
        "preview_subdomain_template": "pr-{id}.preview.{base}",
        "preview_ingress_class": "nginx",
        "use_cloudflare_tunnel": True,
    }
    base.update(over)
    return Settings.model_construct(**base)


def test_subdomain_host() -> None:
    host = preview_subdomain_host("abc123", settings=_settings())
    assert host == "pr-abc123.preview.preview.launchpad.domain"


def test_subdomain_host_sanitizes_id() -> None:
    host = preview_subdomain_host("Env/With_Weird.Chars", settings=_settings())
    assert host.startswith("pr-env-with-weird-chars.preview.")


def test_subdomain_host_none_without_base() -> None:
    assert preview_subdomain_host("abc", settings=_settings(preview_base_domain=None)) is None


def test_subdomain_url_scheme() -> None:
    url = preview_subdomain_url("abc", settings=_settings())
    assert url == "https://pr-abc.preview.preview.launchpad.domain"


def test_generate_preview_ingress_manifest() -> None:
    ing = generate_preview_ingress(
        environment_id="e1",
        namespace="launchpad-env-e1",
        service_name="app",
        service_port=8080,
        settings=_settings(),
    )
    assert ing["kind"] == "Ingress"
    assert ing["apiVersion"] == "networking.k8s.io/v1"
    assert ing["spec"]["ingressClassName"] == "nginx"
    rule = ing["spec"]["rules"][0]
    assert rule["host"] == "pr-e1.preview.preview.launchpad.domain"
    backend = rule["http"]["paths"][0]["backend"]["service"]
    assert backend == {"name": "app", "port": {"number": 8080}}
    assert ing["metadata"]["labels"]["launchpad.io/environment-id"] == "e1"
    assert ing["metadata"]["labels"]["launchpad.io/preview-route"] == "true"


def test_generate_requires_base_domain() -> None:
    with pytest.raises(ValueError, match="PREVIEW_BASE_DOMAIN"):
        generate_preview_ingress(
            environment_id="e1",
            namespace="ns",
            service_name="app",
            service_port=80,
            settings=_settings(preview_base_domain=None),
        )

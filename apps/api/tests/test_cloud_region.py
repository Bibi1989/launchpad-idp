"""Tests for cloud region resolution."""

from __future__ import annotations

from app.schemas.cloud import CloudCredentials
from app.services.cloud_region import region_from_wizard


def test_region_from_wizard_prefers_plugin_region() -> None:
    snapshot = {
        "cloud_plugin": {"region": "europe-west3"},
        "cloud": {"resources": {"region": "us-central1"}},
    }
    assert region_from_wizard("gcp", snapshot) == "europe-west3"


def test_region_from_wizard_uses_vault_when_resources_still_default() -> None:
    creds = CloudCredentials(gcp_region="europe-west3")
    snapshot = {"cloud": {"resources": {"region": "us-central1"}}}
    assert region_from_wizard("gcp", snapshot, creds) == "europe-west3"


def test_region_from_wizard_respects_explicit_non_default_resources() -> None:
    snapshot = {"cloud": {"resources": {"region": "asia-east1"}}}
    assert region_from_wizard("gcp", snapshot) == "asia-east1"


def test_region_from_wizard_aws_from_credentials() -> None:
    creds = CloudCredentials(aws_region="eu-central-1")
    assert region_from_wizard("aws", None, creds) == "eu-central-1"


def test_region_from_wizard_azure_from_credentials() -> None:
    creds = CloudCredentials(azure_location="westeurope")
    assert region_from_wizard("azure", None, creds) == "westeurope"


def test_region_from_wizard_prefers_credentials_over_plugin() -> None:
    creds = CloudCredentials(gcp_region="europe-west1")
    snapshot = {
        "cloud_plugin": {"region": "europe-west3"},
        "cloud": {"resources": {"region": "us-central1"}},
    }
    assert region_from_wizard("gcp", snapshot, creds) == "europe-west1"


def test_discover_backend_service_url_and_frontend_api_env() -> None:
    from app.services.manifest_deploy import (
        discover_backend_service_url,
        frontend_api_env_from_backend_url,
    )

    docs = [
        {
            "kind": "Service",
            "metadata": {"name": "launch-test-frontend-service"},
            "spec": {"ports": [{"port": 8080}]},
        },
        {
            "kind": "Service",
            "metadata": {"name": "launch-test-backend-service"},
            "spec": {"ports": [{"port": 3333}]},
        },
    ]
    url = discover_backend_service_url(docs)
    assert url == "http://launch-test-backend-service:3333"
    env = frontend_api_env_from_backend_url(url)
    assert env["VITE_API_URL"] == url
    assert env["API_URL"] == url
    assert env["NUXT_PUBLIC_API_URL"] == url


def test_default_region_eu_fallbacks() -> None:
    from app.schemas.cloud import CloudProvider
    from app.services.cloud_promote import default_region

    assert default_region(CloudProvider.GCP) == "europe-west3"
    assert default_region(CloudProvider.AWS) == "eu-central-1"
    assert default_region(CloudProvider.AZURE) == "westeurope"

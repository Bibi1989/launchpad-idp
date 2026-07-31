from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.schemas.cloud import GcpResources, SecretBackend
from app.services.gcp_api_enablement import (
    GcpApiEnablementError,
    enable_gcp_apis,
)
from app.services.terraform_bundle import gcp_required_apis


def test_gcp_required_apis_includes_container_when_gke() -> None:
    apis = gcp_required_apis(
        GcpResources(project_id="launchpad-504012", gke=True, vpc=True),
    )
    assert apis[0] == "cloudresourcemanager.googleapis.com"
    assert "serviceusage.googleapis.com" in apis
    assert "compute.googleapis.com" in apis
    assert "container.googleapis.com" in apis
    assert "secretmanager.googleapis.com" in apis  # default secret backend


def test_gcp_required_apis_native_k8s_skips_secretmanager() -> None:
    apis = gcp_required_apis(
        GcpResources(
            project_id="proj",
            gke=True,
            secret_backend=SecretBackend.NATIVE_K8S,
        ),
    )
    assert "container.googleapis.com" in apis
    assert "secretmanager.googleapis.com" not in apis


def test_enable_gcp_apis_skips_already_enabled() -> None:
    sa = (
        '{"type":"service_account","project_id":"launchpad-504012",'
        '"client_email":"a@launchpad-504012.iam.gserviceaccount.com",'
        '"token_uri":"https://oauth2.googleapis.com/token",'
        '"private_key":"-----BEGIN PRIVATE KEY-----\\nMIIE\\n-----END PRIVATE KEY-----\\n"}'
    )
    session = MagicMock()
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = {"state": "ENABLED"}

    with patch(
        "app.services.gcp_api_enablement._authorized_session",
        return_value=session,
    ):
        result = enable_gcp_apis(
            sa_json=sa,
            project_id="launchpad-504012",
            apis=["compute.googleapis.com", "container.googleapis.com"],
        )

    assert result.newly_enabled == []
    assert set(result.already_enabled) >= {
        "cloudresourcemanager.googleapis.com",
        "serviceusage.googleapis.com",
        "compute.googleapis.com",
        "container.googleapis.com",
    }
    session.post.assert_not_called()


def test_enable_gcp_apis_batch_enables_and_confirms() -> None:
    sa = (
        '{"type":"service_account","project_id":"launchpad-504012",'
        '"client_email":"a@launchpad-504012.iam.gserviceaccount.com",'
        '"token_uri":"https://oauth2.googleapis.com/token",'
        '"private_key":"-----BEGIN PRIVATE KEY-----\\nMIIE\\n-----END PRIVATE KEY-----\\n"}'
    )
    session = MagicMock()
    enabled: set[str] = set()

    def get_side_effect(url: str, timeout: int = 60):
        resp = MagicMock()
        resp.status_code = 200
        if "/operations/" in url:
            resp.json.return_value = {"done": True}
            return resp
        api = url.rsplit("/services/", 1)[-1]
        resp.json.return_value = {
            "state": "ENABLED" if api in enabled else "DISABLED",
        }
        return resp

    def post_side_effect(*_args, **kwargs):
        ids = list((kwargs.get("json") or {}).get("serviceIds") or [])
        enabled.update(ids)
        post_resp = MagicMock()
        post_resp.status_code = 200
        post_resp.json.return_value = {"name": "operations/ops-123"}
        return post_resp

    session.get.side_effect = get_side_effect
    session.post.side_effect = post_side_effect

    with (
        patch(
            "app.services.gcp_api_enablement._authorized_session",
            return_value=session,
        ),
        patch("app.services.gcp_api_enablement.time.sleep"),
    ):
        result = enable_gcp_apis(
            sa_json=sa,
            project_id=None,  # derive from SA
            apis=["container.googleapis.com"],
            timeout_seconds=30,
        )

    assert result.project_id == "launchpad-504012"
    assert "container.googleapis.com" in result.newly_enabled
    assert session.post.call_count == 2
    first_ids = session.post.call_args_list[0].kwargs["json"]["serviceIds"]
    assert first_ids == [
        "cloudresourcemanager.googleapis.com",
        "serviceusage.googleapis.com",
    ]


def test_enable_gcp_apis_bootstraps_resource_manager_first() -> None:
    sa = (
        '{"type":"service_account","project_id":"launchpad-504012",'
        '"client_email":"a@launchpad-504012.iam.gserviceaccount.com",'
        '"token_uri":"https://oauth2.googleapis.com/token",'
        '"private_key":"-----BEGIN PRIVATE KEY-----\\nMIIE\\n-----END PRIVATE KEY-----\\n"}'
    )
    session = MagicMock()
    enabled: set[str] = set()
    posts: list[list[str]] = []

    def get_side_effect(url: str, timeout: int = 60):
        resp = MagicMock()
        resp.status_code = 200
        if "/operations/" in url:
            resp.json.return_value = {"done": True}
            return resp
        api = url.rsplit("/services/", 1)[-1]
        resp.json.return_value = {
            "state": "ENABLED" if api in enabled else "DISABLED",
        }
        return resp

    def post_side_effect(_url: str, json: dict | None = None, timeout: int = 120):
        ids = list((json or {}).get("serviceIds") or [])
        posts.append(ids)
        enabled.update(ids)
        post_resp = MagicMock()
        post_resp.status_code = 200
        post_resp.json.return_value = {"name": f"operations/ops-{len(posts)}"}
        return post_resp

    session.get.side_effect = get_side_effect
    session.post.side_effect = post_side_effect

    with (
        patch(
            "app.services.gcp_api_enablement._authorized_session",
            return_value=session,
        ),
        patch("app.services.gcp_api_enablement.time.sleep"),
    ):
        result = enable_gcp_apis(
            sa_json=sa,
            project_id="launchpad-504012",
            apis=["container.googleapis.com"],
            timeout_seconds=30,
        )

    assert posts[0] == [
        "cloudresourcemanager.googleapis.com",
        "serviceusage.googleapis.com",
    ]
    assert posts[1] == ["container.googleapis.com"]
    assert "cloudresourcemanager.googleapis.com" in result.newly_enabled
    assert "container.googleapis.com" in result.newly_enabled

from __future__ import annotations

from uuid import UUID

from app.core.config import Settings
from app.models.domain import EnvironmentStatus
from app.services.datastore_status import derive_datastore_status
from app.services.preview_urls import (
    looks_like_broken_apex_node_port,
    repair_stored_preview_url,
    workspace_ingress_preview_url,
)


def test_workspace_ingress_preview_url() -> None:
    cfg = Settings(
        preview_base_domain="launchpad-idp.online",
        use_cloudflare_tunnel=True,
        environment="production",
        _env_file=None,
    )
    env_id = UUID("e8f9cf54-60c2-4556-8e45-2b654ea4e976")
    assert (
        workspace_ingress_preview_url(env_id, settings=cfg)
        == "https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online"
    )


def test_looks_like_broken_apex_node_port() -> None:
    assert looks_like_broken_apex_node_port(
        "http://launchpad-idp.online:2001", apex="launchpad-idp.online"
    )
    assert looks_like_broken_apex_node_port(
        "http://127.0.0.1:2001", apex="launchpad-idp.online"
    )
    assert not looks_like_broken_apex_node_port(
        "https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online",
        apex="launchpad-idp.online",
    )
    assert not looks_like_broken_apex_node_port(
        "https://foo.trycloudflare.com", apex="launchpad-idp.online"
    )


def test_repair_stored_preview_url_k8s_only() -> None:
    cfg = Settings(
        preview_base_domain="launchpad-idp.online",
        use_cloudflare_tunnel=True,
        environment="production",
        _env_file=None,
    )
    env_id = UUID("e8f9cf54-60c2-4556-8e45-2b654ea4e976")
    assert (
        repair_stored_preview_url(
            "http://launchpad-idp.online:2001",
            environment_id=env_id,
            deploy_mode="preview",
            settings=cfg,
        )
        == "https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online"
    )
    # Attach/compose must invent ws-* when named tunnel is active (Docker-host bridge).
    assert (
        repair_stored_preview_url(
            "http://127.0.0.1:8090",
            environment_id=env_id,
            deploy_mode="attach",
            settings=cfg,
        )
        == "https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online"
    )
    assert (
        repair_stored_preview_url(
            "http://127.0.0.1:8090",
            environment_id=env_id,
            deploy_mode="compose",
            settings=cfg,
        )
        == "https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online"
    )
    good = "https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online"
    assert (
        repair_stored_preview_url(good, environment_id=env_id, deploy_mode="preview", settings=cfg)
        == good
    )


def test_derive_datastore_status() -> None:
    assert derive_datastore_status(enabled=False, env_status=EnvironmentStatus.RUNNING) is None
    assert (
        derive_datastore_status(
            enabled=True, env_status=EnvironmentStatus.RUNNING, app_ready=True
        )
        == "running"
    )
    assert (
        derive_datastore_status(
            enabled=True, env_status=EnvironmentStatus.PROVISIONING, app_ready=False
        )
        == "pending"
    )
    assert (
        derive_datastore_status(enabled=True, env_status=EnvironmentStatus.FAILED, app_ready=False)
        == "failed"
    )
    assert (
        derive_datastore_status(enabled=True, env_status=EnvironmentStatus.PAUSED, app_ready=False)
        == "stopped"
    )

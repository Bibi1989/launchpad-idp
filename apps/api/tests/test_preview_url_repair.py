from __future__ import annotations

from uuid import UUID

from app.core.config import Settings
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


def test_repair_stored_preview_url() -> None:
    cfg = Settings(
        preview_base_domain="launchpad-idp.online",
        use_cloudflare_tunnel=True,
        environment="production",
    )
    env_id = UUID("e8f9cf54-60c2-4556-8e45-2b654ea4e976")
    assert (
        repair_stored_preview_url(
            "http://launchpad-idp.online:2001",
            environment_id=env_id,
            settings=cfg,
        )
        == "https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online"
    )
    # Already correct.
    good = "https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online"
    assert repair_stored_preview_url(good, environment_id=env_id, settings=cfg) == good

"""Unit tests for Slack/Jira integration helpers."""

from __future__ import annotations

from app.schemas.integrations import SlackIntegrationUpdate
from app.services.integrations.jira import jira_browse_url
from app.services.integrations.slack import build_environment_blocks
from pydantic import ValidationError
import pytest


def test_slack_webhook_rejects_non_slack_url() -> None:
    with pytest.raises(ValidationError):
        SlackIntegrationUpdate(webhook_url="https://example.com/hooks")


def test_slack_webhook_accepts_hooks_slack() -> None:
    payload = SlackIntegrationUpdate(
        webhook_url="https://hooks.slack.com/services/T/B/xxx"
    )
    assert payload.webhook_url == "https://hooks.slack.com/services/T/B/xxx"


def test_build_environment_blocks_includes_links() -> None:
    blocks = build_environment_blocks(
        title="Preview ready",
        env_name="demo-env",
        status="RUNNING",
        portal_url="http://localhost:3000/p/abc",
        preview_url="http://localhost:18080",
        workspace_label="ws-1",
        correlation_id="corr-1",
        detail=None,
    )
    assert blocks[0]["type"] == "header"
    joined = str(blocks)
    assert "Open portal" in joined
    assert "Open app" in joined
    assert "demo-env" in joined


def test_jira_browse_url() -> None:
    assert (
        jira_browse_url("https://acme.atlassian.net/", "ENG-12")
        == "https://acme.atlassian.net/browse/ENG-12"
    )

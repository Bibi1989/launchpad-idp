"""Slack Incoming Webhook helpers."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

SLACK_TIMEOUT_SECONDS = 8.0


def build_environment_blocks(
    *,
    title: str,
    env_name: str,
    status: str,
    portal_url: str | None,
    preview_url: str | None,
    workspace_label: str | None,
    correlation_id: str | None,
    detail: str | None,
) -> list[dict[str, Any]]:
    fields = [
        {"type": "mrkdwn", "text": f"*Environment*\n`{env_name}`"},
        {"type": "mrkdwn", "text": f"*Status*\n`{status}`"},
    ]
    if workspace_label:
        fields.append({"type": "mrkdwn", "text": f"*Workspace*\n{workspace_label}"})
    if correlation_id:
        fields.append({"type": "mrkdwn", "text": f"*Correlation*\n`{correlation_id}`"})

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title[:150], "emoji": True},
        },
        {"type": "section", "fields": fields[:10]},
    ]
    if detail:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Detail*\n```{detail[:1800]}```"},
            }
        )
    links: list[str] = []
    if portal_url:
        links.append(f"<{portal_url}|Open portal>")
    if preview_url:
        links.append(f"<{preview_url}|Open app>")
    if links:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": " · ".join(links)}],
            }
        )
    return blocks


def post_slack_webhook(
    webhook_url: str,
    *,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
) -> bool:
    """POST to a Slack Incoming Webhook. Never raises; returns success bool."""
    payload: dict[str, Any] = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        with httpx.Client(timeout=SLACK_TIMEOUT_SECONDS) as client:
            response = client.post(webhook_url, json=payload)
        if response.status_code >= 400:
            logger.warning(
                "slack_webhook_failed",
                status_code=response.status_code,
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - never break callers
        logger.warning("slack_webhook_error", error=str(exc))
        return False

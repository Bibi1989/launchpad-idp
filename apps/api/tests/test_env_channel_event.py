"""Env SSE payloads carry preview URL / ready / error for live UI."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch
from uuid import uuid4

from app.core import events
from app.models.domain import ExecutionStage


def test_publish_env_event_includes_lifecycle_fields() -> None:
    env_id = uuid4()
    published: list[str] = []

    class _Client:
        async def publish(self, channel: str, message: str) -> int:
            published.append(message)
            return 1

        async def aclose(self) -> None:
            return None

    with patch("app.core.events.redis.from_url", return_value=_Client()):
        asyncio.run(
            events.publish_env_event(
                env_id,
                event_type="STATUS_CHANGE",
                status="RUNNING",
                message="http://127.0.0.1:8081",
                stage=ExecutionStage.APPLY,
                preview_url="http://127.0.0.1:8081",
                node_port=8081,
                app_ready=True,
                notice="Port remap: preferred host port 8080 in use, using 8081 instead",
            )
        )

    assert len(published) == 1
    payload = json.loads(published[0])
    assert payload["preview_url"] == "http://127.0.0.1:8081"
    assert payload["node_port"] == 8081
    assert payload["app_ready"] is True
    assert "8081" in (payload["notice"] or "")


def test_env_channel_event_model_accepts_failure_fields() -> None:
    event = events.EnvChannelEvent(
        type="STATUS_CHANGE",
        status="FAILED",
        message="boom",
        environment_id=str(uuid4()),
        error_message="boom",
        app_ready=False,
    )
    dumped = event.model_dump()
    assert dumped["error_message"] == "boom"
    assert dumped["app_ready"] is False

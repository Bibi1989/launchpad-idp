from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import redis.asyncio as redis
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.domain import ExecutionStage

logger = get_logger(__name__)

EnvEventType = Literal["STATUS_CHANGE", "LOG", "EXECUTION_FAILED"]


class EnvChannelEvent(BaseModel):
    type: EnvEventType
    status: str | None = None
    commit_sha: str | None = None
    message: str | None = None
    log_level: str | None = None
    environment_id: str | None = None
    stage: ExecutionStage | None = None
    timestamp: str | None = None
    # Rich lifecycle fields so SSE clients can open the preview / toast without REST.
    preview_url: str | None = None
    node_port: int | None = None
    app_ready: bool | None = None
    notice: str | None = None
    error_message: str | None = None
    preview_endpoints: list[dict[str, object]] | None = None
    failure_summary: str | None = None


def env_channel(environment_id: UUID | str) -> str:
    return f"env_channel:{environment_id}"


# Celery runs asyncio.run() per task (new event loop each time). A cached Redis
# client must not outlive its loop - otherwise publish fails with
# "Future attached to a different loop" / "Event loop is closed".
_redis_client: redis.Redis | None = None
_redis_loop_id: int | None = None


def _reset_redis_cache() -> None:
    global _redis_client, _redis_loop_id
    _redis_client = None
    _redis_loop_id = None


async def get_redis() -> redis.Redis:
    """Return a Redis client bound to the current running event loop."""
    global _redis_client, _redis_loop_id
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    if _redis_client is not None and _redis_loop_id != loop_id:
        # Do not await aclose() - that loop is often already closed (Celery).
        _reset_redis_cache()
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        _redis_loop_id = loop_id
    return _redis_client


async def close_redis() -> None:
    global _redis_client, _redis_loop_id
    if _redis_client is not None:
        client = _redis_client
        _reset_redis_cache()
        try:
            await client.aclose()
        except Exception:
            logger.exception("redis_close_failed")


def structured_log_payload(
    *,
    stage: ExecutionStage,
    message: str,
    log_level: str = "INFO",
    timestamp: datetime | None = None,
) -> dict[str, str]:
    """SSE / terminal structured log envelope."""
    ts = timestamp or datetime.now(UTC)
    return {
        "stage": stage.value,
        "log_level": log_level,
        "timestamp": ts.isoformat(),
        "message": message,
    }


async def publish_env_event(
    environment_id: UUID | str,
    *,
    event_type: EnvEventType,
    status: str | None = None,
    commit_sha: str | None = None,
    message: str | None = None,
    log_level: str | None = None,
    stage: ExecutionStage | None = None,
    timestamp: datetime | None = None,
    preview_url: str | None = None,
    node_port: int | None = None,
    app_ready: bool | None = None,
    notice: str | None = None,
    error_message: str | None = None,
    preview_endpoints: list[dict[str, object]] | None = None,
    failure_summary: str | None = None,
) -> None:
    ts = timestamp or datetime.now(UTC)
    payload = EnvChannelEvent(
        type=event_type,
        status=status,
        commit_sha=commit_sha,
        message=message,
        log_level=log_level,
        environment_id=str(environment_id),
        stage=stage,
        timestamp=ts.isoformat(),
        preview_url=preview_url,
        node_port=node_port,
        app_ready=app_ready,
        notice=notice,
        error_message=error_message,
        preview_endpoints=preview_endpoints,
        failure_summary=failure_summary,
    )
    channel = env_channel(environment_id)
    # Short-lived client: safe across Celery asyncio.run() per-task loops.
    settings = get_settings()
    client = redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        await client.publish(channel, payload.model_dump_json())
    except Exception:
        logger.exception(
            "env_event_publish_failed",
            channel=channel,
            event_type=event_type,
        )
    finally:
        try:
            await client.aclose()
        except Exception:
            logger.exception("env_event_redis_close_failed", channel=channel)


async def subscribe_env_events(
    environment_id: UUID | str,
) -> AsyncIterator[EnvChannelEvent]:
    """Yield parsed channel events until the generator is closed by the caller."""
    client = await get_redis()
    pubsub = client.pubsub()
    channel = env_channel(environment_id)
    await pubsub.subscribe(channel)
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if message is None:
                continue
            if message.get("type") != "message":
                continue
            raw = message.get("data")
            if not isinstance(raw, str):
                continue
            try:
                data: dict[str, Any] = json.loads(raw)
                yield EnvChannelEvent.model_validate(data)
            except (json.JSONDecodeError, ValueError):
                logger.warning("env_event_parse_failed", channel=channel, raw=raw[:200])
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception:
            logger.exception("env_pubsub_close_failed", channel=channel)


class WebhookAcceptResponse(BaseModel):
    accepted: bool
    event: str
    matched_environments: list[str] = Field(default_factory=list)
    message: str

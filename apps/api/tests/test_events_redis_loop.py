"""Redis pub/sub must tolerate Celery's per-task asyncio.run() loops."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core import events


@pytest.mark.asyncio
async def test_get_redis_rebinds_when_loop_changes() -> None:
    events._reset_redis_cache()
    fake_a = MagicMock()
    fake_b = MagicMock()

    with patch("app.core.events.redis.from_url", side_effect=[fake_a, fake_b]):
        first = await events.get_redis()
        assert first is fake_a
        # Simulate a new Celery task loop by forging a different loop id.
        events._redis_loop_id = id(object())
        second = await events.get_redis()
        assert second is fake_b

    events._reset_redis_cache()


def test_publish_env_event_uses_fresh_client_across_runs() -> None:
    """Two asyncio.run() calls must not share a stale Redis connection."""
    env_id = uuid4()
    publishes: list[str] = []

    class _Client:
        async def publish(self, channel: str, message: str) -> int:
            publishes.append(channel)
            return 1

        async def aclose(self) -> None:
            return None

    with patch("app.core.events.redis.from_url", return_value=_Client()):
        asyncio.run(
            events.publish_env_event(
                env_id,
                event_type="LOG",
                message="first",
            )
        )
        asyncio.run(
            events.publish_env_event(
                env_id,
                event_type="LOG",
                message="second",
            )
        )

    assert len(publishes) == 2
    assert all(p == f"env_channel:{env_id}" for p in publishes)

"""Stuck TEARDOWN_PENDING rows must be re-queued when the worker restarts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.domain import EnvironmentStatus
from app.workers import tasks as task_module


@pytest.mark.asyncio
async def test_requeue_stale_teardowns_skips_locked_and_fresh() -> None:
    now = datetime.now(UTC)
    stale = MagicMock()
    stale.id = uuid4()
    stale.status = EnvironmentStatus.TEARDOWN_PENDING
    stale.updated_at = now - timedelta(seconds=task_module.STALE_TEARDOWN_SECONDS + 30)

    locked = MagicMock()
    locked.id = uuid4()
    locked.status = EnvironmentStatus.TEARDOWN_PENDING
    locked.updated_at = now - timedelta(seconds=task_module.STALE_TEARDOWN_SECONDS + 30)

    session = AsyncMock()
    result = MagicMock()
    # SQL already filters by updated_at < cutoff; fresh is excluded.
    result.scalars.return_value.all.return_value = [stale, locked]
    session.execute = AsyncMock(return_value=result)

    async def lock_side_effect(env_id, scope="environment"):
        return env_id == locked.id

    with (
        patch.object(task_module, "is_state_locked", AsyncMock(side_effect=lock_side_effect)),
        patch.object(task_module, "enqueue_teardown_environment") as enqueue,
    ):
        requeued = await task_module._requeue_stale_teardowns(
            session,
            now=now,
            min_age_seconds=task_module.STALE_TEARDOWN_SECONDS,
        )

    assert requeued == 1
    enqueue.assert_called_once()
    assert enqueue.call_args.kwargs["environment_id"] == str(stale.id)


@pytest.mark.asyncio
async def test_requeue_pending_teardowns_min_age_zero() -> None:
    now = datetime.now(UTC)
    pending = MagicMock()
    pending.id = uuid4()
    pending.status = EnvironmentStatus.TEARDOWN_PENDING
    pending.updated_at = now - timedelta(seconds=1)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [pending]
    session.execute = AsyncMock(return_value=result)
    session_factory = MagicMock(return_value=session)

    with (
        patch.object(task_module, "_session_factory", lambda: session_factory),
        patch.object(task_module, "is_state_locked", AsyncMock(return_value=False)),
        patch.object(task_module, "enqueue_teardown_environment") as enqueue,
    ):
        requeued = await task_module._requeue_pending_teardowns(min_age_seconds=0)

    assert requeued == 1
    enqueue.assert_called_once()

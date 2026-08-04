"""Distributed Redis state locks for environment / workspace mutations.

Prevents concurrent terraform, helm, or kubectl operations from corrupting
shared infrastructure state. Locks are always released via context-manager
``finally`` semantics, including unexpected task crashes after acquire.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

import redis.asyncio as redis
from redis.exceptions import LockError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

LockScope = Literal["environment", "workspace"]

PROVISIONING_IN_PROGRESS_MESSAGE = "Provisioning already in progress for this environment."


class StateLockConflict(Exception):
    """Raised when a distributed lock cannot be acquired (duplicate trigger)."""

    def __init__(self, *, scope: LockScope, resource_id: str, message: str) -> None:
        self.scope = scope
        self.resource_id = resource_id
        self.message = message
        super().__init__(message)


def _lock_key(scope: LockScope, resource_id: str) -> str:
    return f"launchpad:{scope}:{resource_id}:state_lock"


@asynccontextmanager
async def acquire_state_lock(
    resource_id: UUID | str,
    *,
    scope: LockScope = "environment",
    settings: Settings | None = None,
    blocking: bool = False,
) -> AsyncIterator[None]:
    """Acquire a Redis lock for ``resource_id``.

    When ``blocking`` is False (default), a held lock raises
    :class:`StateLockConflict` immediately so callers can reject duplicates
    with HTTP 409 / a warning log.
    """
    cfg = settings or get_settings()
    key = _lock_key(scope, str(resource_id))
    client = redis.from_url(cfg.redis_url, encoding="utf-8", decode_responses=True)
    lock = client.lock(
        name=key,
        timeout=cfg.state_lock_timeout_seconds,
        blocking_timeout=cfg.state_lock_blocking_timeout_seconds if blocking else 0,
        thread_local=False,
    )
    acquired = False
    try:
        acquired = await lock.acquire(blocking=blocking)
        if not acquired:
            message = (
                PROVISIONING_IN_PROGRESS_MESSAGE
                if scope == "environment"
                else f"Operation already in progress for this {scope}."
            )
            logger.warning(
                "state_lock_conflict",
                scope=scope,
                resource_id=str(resource_id),
                lock_key=key,
                message=message,
            )
            raise StateLockConflict(
                scope=scope,
                resource_id=str(resource_id),
                message=message,
            )
        logger.info(
            "state_lock_acquired",
            scope=scope,
            resource_id=str(resource_id),
            lock_key=key,
        )
        yield
    finally:
        if acquired:
            try:
                await lock.release()
                logger.info(
                    "state_lock_released",
                    scope=scope,
                    resource_id=str(resource_id),
                    lock_key=key,
                )
            except LockError:
                # Lock expired or was already released - safe to ignore.
                logger.warning(
                    "state_lock_release_noop",
                    scope=scope,
                    resource_id=str(resource_id),
                    lock_key=key,
                )
            except Exception:
                logger.exception(
                    "state_lock_release_failed",
                    scope=scope,
                    resource_id=str(resource_id),
                    lock_key=key,
                )
        try:
            await client.aclose()
        except Exception:
            logger.exception("state_lock_redis_close_failed", lock_key=key)


async def is_state_locked(
    resource_id: UUID | str,
    *,
    scope: LockScope = "environment",
    settings: Settings | None = None,
) -> bool:
    """Return True when a state lock key is currently held."""
    cfg = settings or get_settings()
    key = _lock_key(scope, str(resource_id))
    client = redis.from_url(cfg.redis_url, encoding="utf-8", decode_responses=True)
    try:
        return bool(await client.exists(key))
    finally:
        try:
            await client.aclose()
        except Exception:
            logger.exception("state_lock_exists_close_failed", lock_key=key)

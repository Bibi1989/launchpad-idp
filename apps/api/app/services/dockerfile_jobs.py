"""Redis-backed job status for Dockerfile registry builds."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.core.events import get_redis
from app.core.logging import get_logger
from app.schemas.dockerfile_schema import (
    DockerfileBuildJobResponse,
    DockerfileBuildJobStatus,
)

logger = get_logger(__name__)

_JOB_TTL_SECONDS = 60 * 60 * 24  # 24h
_KEY_PREFIX = "dockerfile_build_job:"


def new_job_id() -> str:
    return str(uuid4())


def _key(job_id: str) -> str:
    return f"{_KEY_PREFIX}{job_id}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def create_build_job(job_id: str | None = None) -> DockerfileBuildJobResponse:
    jid = job_id or new_job_id()
    now = _now_iso()
    payload = DockerfileBuildJobResponse(
        job_id=jid,
        status=DockerfileBuildJobStatus.QUEUED,
        image_refs=[],
        logs=[],
        error=None,
        created_at=now,
        updated_at=now,
    )
    client = await get_redis()
    await client.set(_key(jid), payload.model_dump_json(), ex=_JOB_TTL_SECONDS)
    return payload


async def get_build_job(job_id: str) -> DockerfileBuildJobResponse | None:
    client = await get_redis()
    raw = await client.get(_key(job_id))
    if not raw:
        return None
    return DockerfileBuildJobResponse.model_validate_json(raw)


async def update_build_job(
    job_id: str,
    *,
    status: DockerfileBuildJobStatus | None = None,
    image_refs: list[str] | None = None,
    logs: list[str] | None = None,
    append_logs: list[str] | None = None,
    error: str | None = None,
) -> DockerfileBuildJobResponse | None:
    current = await get_build_job(job_id)
    if current is None:
        return None

    next_logs = list(current.logs)
    if logs is not None:
        next_logs = logs
    if append_logs:
        next_logs.extend(append_logs)
        # Cap log volume stored in Redis.
        next_logs = next_logs[-500:]

    updated = current.model_copy(
        update={
            "status": status or current.status,
            "image_refs": image_refs if image_refs is not None else current.image_refs,
            "logs": next_logs,
            "error": error if error is not None else current.error,
            "updated_at": _now_iso(),
        }
    )
    client = await get_redis()
    await client.set(_key(job_id), updated.model_dump_json(), ex=_JOB_TTL_SECONDS)
    return updated


def update_build_job_sync(
    job_id: str,
    *,
    status: DockerfileBuildJobStatus | None = None,
    image_refs: list[str] | None = None,
    logs: list[str] | None = None,
    append_logs: list[str] | None = None,
    error: str | None = None,
) -> None:
    """Synchronous Redis update for Celery workers (redis-py sync client)."""
    import redis

    from app.core.config import get_settings

    settings = get_settings()
    client = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    try:
        raw = client.get(_key(job_id))
        if not raw:
            logger.warning("dockerfile_build_job_missing", job_id=job_id)
            return
        current = DockerfileBuildJobResponse.model_validate_json(raw)
        next_logs = list(current.logs)
        if logs is not None:
            next_logs = logs
        if append_logs:
            next_logs.extend(append_logs)
            next_logs = next_logs[-500:]
        updated = current.model_copy(
            update={
                "status": status or current.status,
                "image_refs": image_refs if image_refs is not None else current.image_refs,
                "logs": next_logs,
                "error": error if error is not None else current.error,
                "updated_at": _now_iso(),
            }
        )
        client.set(_key(job_id), updated.model_dump_json(), ex=_JOB_TTL_SECONDS)
    finally:
        client.close()

"""Append-only immutable audit log pipeline for control-plane actions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, sanitize_log_message
from app.models.domain import AuditAction, AuditLog, AuditStatus

logger = get_logger(__name__)

WEBHOOK_ACTOR_PREFIX = "webhook:"
DEFAULT_AUDIT_LIMIT = 50
MAX_AUDIT_LIMIT = 200


class AuditService:
    """Audit trail service. Rows are append-only - never update or delete."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        action: AuditAction,
        actor_id: str,
        status: AuditStatus,
        workspace_id: UUID | None = None,
        environment_id: UUID | None = None,
        commit_sha: str | None = None,
        detail: str | None = None,
        timestamp: datetime | None = None,
    ) -> AuditLog:
        safe_detail = sanitize_log_message(detail) if detail else None
        entry = AuditLog(
            workspace_id=workspace_id,
            environment_id=environment_id,
            actor_id=actor_id[:128],
            action=action,
            commit_sha=commit_sha,
            status=status,
            detail=safe_detail,
            timestamp=timestamp or datetime.now(UTC),
        )
        self._session.add(entry)
        await self._session.flush()
        await self._session.refresh(entry)
        logger.info(
            "audit_recorded",
            audit_id=str(entry.id),
            action=action.value,
            status=status.value,
            actor_id=actor_id,
            environment_id=str(environment_id) if environment_id else None,
            workspace_id=str(workspace_id) if workspace_id else None,
            commit_sha=commit_sha,
        )
        return entry

    async def list_for_environment(
        self,
        environment_id: UUID,
        *,
        limit: int = DEFAULT_AUDIT_LIMIT,
    ) -> list[AuditLog]:
        capped = max(1, min(limit, MAX_AUDIT_LIMIT))
        result = await self._session.execute(
            select(AuditLog)
            .where(AuditLog.environment_id == environment_id)
            .order_by(AuditLog.timestamp.desc())
            .limit(capped)
        )
        return list(result.scalars().all())

    async def list_for_workspace(
        self,
        workspace_id: UUID,
        *,
        limit: int = DEFAULT_AUDIT_LIMIT,
    ) -> list[AuditLog]:
        capped = max(1, min(limit, MAX_AUDIT_LIMIT))
        result = await self._session.execute(
            select(AuditLog)
            .where(AuditLog.workspace_id == workspace_id)
            .order_by(AuditLog.timestamp.desc())
            .limit(capped)
        )
        return list(result.scalars().all())

    async def latest_for_environment(
        self,
        environment_id: UUID,
        action: AuditAction,
    ) -> AuditLog | None:
        result = await self._session.execute(
            select(AuditLog)
            .where(
                AuditLog.environment_id == environment_id,
                AuditLog.action == action,
            )
            .order_by(AuditLog.timestamp.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def has_unresolved_drift(self, environment_id: UUID) -> bool:
        """True when the latest drift audit is newer than the last successful deploy/rebuild."""
        drift = await self.latest_for_environment(environment_id, AuditAction.DRIFT_DETECTED)
        if drift is None:
            return False
        for resolved_action in (
            AuditAction.PROVISION_SUCCEEDED,
            AuditAction.REBUILD_SUCCEEDED,
        ):
            resolved = await self.latest_for_environment(environment_id, resolved_action)
            if resolved is not None and resolved.timestamp > drift.timestamp:
                return False
        return True

    @staticmethod
    def user_actor(user_id: UUID) -> str:
        return str(user_id)

    @staticmethod
    def webhook_actor(source: str = "github") -> str:
        return f"{WEBHOOK_ACTOR_PREFIX}{source}"

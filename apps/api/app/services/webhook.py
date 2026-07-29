from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.events import publish_env_event
from app.core.logging import get_logger
from app.models.domain import AuditAction, AuditStatus, EnvironmentStatus, LogLevel
from app.repositories.environment import DeploymentLogRepository, EnvironmentRepository
from app.services.audit import AuditService
from app.services.git_urls import branch_from_git_ref, short_commit_sha
from app.services.state_lock import (
    PROVISIONING_IN_PROGRESS_MESSAGE,
    is_state_locked,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PushEventDetails:
    repository_full_name: str
    branch: str
    commit_sha: str
    full_commit_sha: str


@dataclass(frozen=True, slots=True)
class WebhookProcessResult:
    accepted: bool
    event: str
    matched_environment_ids: list[UUID]
    message: str


class GitHubWebhookService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._environments = EnvironmentRepository(session)
        self._logs = DeploymentLogRepository(session)

    @staticmethod
    def verify_signature(*, body: bytes, signature_header: str | None, secret: str) -> bool:
        if not signature_header or not signature_header.startswith("sha256="):
            return False
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        expected = f"sha256={digest}"
        return hmac.compare_digest(expected, signature_header)

    @staticmethod
    def parse_push_event(payload: dict[str, Any]) -> PushEventDetails | None:
        repository = payload.get("repository")
        if not isinstance(repository, dict):
            return None
        full_name = repository.get("full_name")
        if not isinstance(full_name, str) or not full_name.strip():
            return None

        ref = payload.get("ref")
        if not isinstance(ref, str):
            return None
        branch = branch_from_git_ref(ref)
        if branch is None:
            return None

        after = payload.get("after")
        if not isinstance(after, str):
            return None
        short = short_commit_sha(after)
        if not short:
            return None

        return PushEventDetails(
            repository_full_name=full_name.strip(),
            branch=branch,
            commit_sha=short,
            full_commit_sha=after.strip(),
        )

    async def process_event(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> WebhookProcessResult:
        from app.workers.tasks import enqueue_rebuild_environment

        if event_name == "ping":
            return WebhookProcessResult(
                accepted=True,
                event=event_name,
                matched_environment_ids=[],
                message="pong",
            )

        if event_name != "push":
            return WebhookProcessResult(
                accepted=True,
                event=event_name,
                matched_environment_ids=[],
                message=f"Ignored event '{event_name}'",
            )

        details = self.parse_push_event(payload)
        if details is None:
            return WebhookProcessResult(
                accepted=True,
                event=event_name,
                matched_environment_ids=[],
                message="Push event missing repository/branch/commit details (or tag/delete)",
            )

        matches = await self._environments.list_active_for_repo_branch(
            repo_full_name=details.repository_full_name,
            branch=details.branch,
        )
        if not matches:
            logger.info(
                "webhook_push_no_match",
                repo=details.repository_full_name,
                branch=details.branch,
                commit=details.commit_sha,
            )
            return WebhookProcessResult(
                accepted=True,
                event=event_name,
                matched_environment_ids=[],
                message="No active environments matched repository/branch",
            )

        matched_ids: list[UUID] = []
        audit = AuditService(self._session)
        for candidate in matches:
            if await is_state_locked(candidate.id, scope="environment"):
                logger.warning(
                    "webhook_rebuild_skipped_lock_held",
                    environment_id=str(candidate.id),
                    message=PROVISIONING_IN_PROGRESS_MESSAGE,
                )
                await self._logs.create(
                    environment_id=candidate.id,
                    message=PROVISIONING_IN_PROGRESS_MESSAGE,
                    log_level=LogLevel.WARN,
                )
                await audit.record(
                    action=AuditAction.REBUILD_INITIATED,
                    actor_id=AuditService.webhook_actor("github"),
                    status=AuditStatus.REJECTED,
                    environment_id=candidate.id,
                    workspace_id=candidate.workspace_id,
                    commit_sha=details.commit_sha,
                    detail=PROVISIONING_IN_PROGRESS_MESSAGE,
                )
                await self._session.commit()
                continue

            environment = await self._environments.mark_rebuild(
                candidate.id,
                commit_sha=details.commit_sha,
            )
            if environment is None:
                continue

            await self._logs.create(
                environment_id=environment.id,
                message=(
                    f"GitOps rebuild queued for {details.repository_full_name}"
                    f"@{details.branch} ({details.commit_sha})"
                ),
                log_level=LogLevel.INFO,
            )
            await audit.record(
                action=AuditAction.REBUILD_INITIATED,
                actor_id=AuditService.webhook_actor("github"),
                status=AuditStatus.PENDING,
                environment_id=environment.id,
                workspace_id=environment.workspace_id,
                commit_sha=details.commit_sha,
            )
            await self._session.commit()

            await publish_env_event(
                environment.id,
                event_type="STATUS_CHANGE",
                status=EnvironmentStatus.PROVISIONING.value,
                commit_sha=details.commit_sha,
                message="Rebuild queued from GitHub push",
            )
            await publish_env_event(
                environment.id,
                event_type="LOG",
                status=EnvironmentStatus.PROVISIONING.value,
                commit_sha=details.commit_sha,
                message=f"GitOps rebuild queued for commit {details.commit_sha}",
                log_level=LogLevel.INFO.value,
            )

            enqueue_rebuild_environment(
                environment_id=str(environment.id),
                commit_sha=details.commit_sha,
                correlation_id=correlation_id,
            )
            matched_ids.append(environment.id)
            logger.info(
                "webhook_rebuild_enqueued",
                environment_id=str(environment.id),
                repo=details.repository_full_name,
                branch=details.branch,
                commit=details.commit_sha,
                correlation_id=correlation_id,
            )

        return WebhookProcessResult(
            accepted=True,
            event=event_name,
            matched_environment_ids=matched_ids,
            message=f"Queued rebuild for {len(matched_ids)} environment(s)",
        )

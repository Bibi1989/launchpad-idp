from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Literal
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

PullRequestAction = Literal[
    "opened",
    "reopened",
    "synchronize",
    "closed",
    "edited",
    "ready_for_review",
    "converted_to_draft",
    "labeled",
    "unlabeled",
    "assigned",
    "unassigned",
    "review_requested",
    "review_request_removed",
    "auto_merge_enabled",
    "auto_merge_disabled",
    "enqueued",
    "dequeued",
]


@dataclass(frozen=True, slots=True)
class PushEventDetails:
    repository_full_name: str
    branch: str
    commit_sha: str
    full_commit_sha: str


@dataclass(frozen=True, slots=True)
class PullRequestEventDetails:
    action: str
    repository_full_name: str
    pr_number: int
    pr_url: str
    head_branch: str
    head_sha: str
    short_sha: str
    merged: bool


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

    @staticmethod
    def parse_pull_request_event(payload: dict[str, Any]) -> PullRequestEventDetails | None:
        action = payload.get("action")
        if not isinstance(action, str) or not action:
            return None
        repository = payload.get("repository")
        pull = payload.get("pull_request")
        if not isinstance(repository, dict) or not isinstance(pull, dict):
            return None
        full_name = repository.get("full_name")
        number = pull.get("number")
        html_url = pull.get("html_url")
        head = pull.get("head")
        if not isinstance(full_name, str) or not full_name.strip():
            return None
        if not isinstance(number, int) or number < 1:
            return None
        if not isinstance(html_url, str) or not html_url.strip():
            return None
        if not isinstance(head, dict):
            return None
        head_ref = head.get("ref")
        head_sha = head.get("sha")
        if not isinstance(head_ref, str) or not head_ref.strip():
            return None
        if not isinstance(head_sha, str) or not head_sha.strip():
            return None
        short = short_commit_sha(head_sha) or head_sha[:7]
        merged = bool(pull.get("merged")) if action == "closed" else False
        return PullRequestEventDetails(
            action=action,
            repository_full_name=full_name.strip(),
            pr_number=number,
            pr_url=html_url.strip(),
            head_branch=head_ref.strip(),
            head_sha=head_sha.strip(),
            short_sha=short,
            merged=merged,
        )

    async def process_event(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> WebhookProcessResult:
        if event_name == "ping":
            return WebhookProcessResult(
                accepted=True,
                event=event_name,
                matched_environment_ids=[],
                message="pong",
            )

        if event_name == "pull_request":
            return await self._process_pull_request(
                payload=payload,
                correlation_id=correlation_id,
            )

        if event_name != "push":
            return WebhookProcessResult(
                accepted=True,
                event=event_name,
                matched_environment_ids=[],
                message=f"Ignored event '{event_name}'",
            )

        return await self._process_push(payload=payload, correlation_id=correlation_id)

    async def _process_pull_request(
        self,
        *,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> WebhookProcessResult:
        details = self.parse_pull_request_event(payload)
        if details is None:
            return WebhookProcessResult(
                accepted=True,
                event="pull_request",
                matched_environment_ids=[],
                message="Pull request event missing repository/PR details",
            )

        if details.action == "closed":
            return await self._teardown_pr_environments(details, correlation_id=correlation_id)

        if details.action in {"opened", "reopened", "synchronize", "ready_for_review"}:
            return await self._rebuild_pr_environments(details, correlation_id=correlation_id)

        return WebhookProcessResult(
            accepted=True,
            event="pull_request",
            matched_environment_ids=[],
            message=f"Ignored pull_request action '{details.action}'",
        )

    async def _rebuild_pr_environments(
        self,
        details: PullRequestEventDetails,
        *,
        correlation_id: str,
    ) -> WebhookProcessResult:
        from app.workers.tasks import enqueue_rebuild_environment

        matches = await self._environments.list_active_for_repo_pr(
            repo_full_name=details.repository_full_name,
            pr_number=details.pr_number,
        )
        if not matches:
            logger.info(
                "webhook_pr_no_match",
                repo=details.repository_full_name,
                pr=details.pr_number,
                action=details.action,
            )
            return WebhookProcessResult(
                accepted=True,
                event="pull_request",
                matched_environment_ids=[],
                message=(
                    f"No active PR preview for {details.repository_full_name}"
                    f"#{details.pr_number} - launch once from Launchpad with this PR number"
                ),
            )

        matched_ids: list[UUID] = []
        audit = AuditService(self._session)
        for candidate in matches:
            if await is_state_locked(candidate.id, scope="environment"):
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
                    commit_sha=details.short_sha,
                    detail=PROVISIONING_IN_PROGRESS_MESSAGE,
                )
                await self._session.commit()
                continue

            if candidate.github_pr_url is None:
                candidate.github_pr_url = details.pr_url

            environment = await self._environments.mark_rebuild(
                candidate.id,
                commit_sha=details.short_sha,
            )
            if environment is None:
                continue

            await self._logs.create(
                environment_id=environment.id,
                message=(
                    f"PR #{details.pr_number} {details.action}: rebuild queued "
                    f"({details.short_sha})"
                ),
                log_level=LogLevel.INFO,
            )
            await audit.record(
                action=AuditAction.REBUILD_INITIATED,
                actor_id=AuditService.webhook_actor("github"),
                status=AuditStatus.PENDING,
                environment_id=environment.id,
                workspace_id=environment.workspace_id,
                commit_sha=details.short_sha,
                detail=f"pull_request:{details.action}",
            )
            await self._session.commit()

            await publish_env_event(
                environment.id,
                event_type="STATUS_CHANGE",
                status=EnvironmentStatus.PROVISIONING.value,
                commit_sha=details.short_sha,
                message=f"Rebuild queued from PR #{details.pr_number} {details.action}",
            )
            enqueue_rebuild_environment(
                environment_id=str(environment.id),
                commit_sha=details.short_sha,
                correlation_id=correlation_id,
            )
            matched_ids.append(environment.id)

        return WebhookProcessResult(
            accepted=True,
            event="pull_request",
            matched_environment_ids=matched_ids,
            message=f"Queued PR rebuild for {len(matched_ids)} environment(s)",
        )

    async def _teardown_pr_environments(
        self,
        details: PullRequestEventDetails,
        *,
        correlation_id: str,
    ) -> WebhookProcessResult:
        from app.workers.tasks import enqueue_teardown_environment

        matches = await self._environments.list_active_for_repo_pr(
            repo_full_name=details.repository_full_name,
            pr_number=details.pr_number,
        )
        if not matches:
            return WebhookProcessResult(
                accepted=True,
                event="pull_request",
                matched_environment_ids=[],
                message=f"No active PR preview to destroy for #{details.pr_number}",
            )

        matched_ids: list[UUID] = []
        audit = AuditService(self._session)
        reason = "merged" if details.merged else "closed"
        for candidate in matches:
            if candidate.status in {
                EnvironmentStatus.TEARDOWN_PENDING,
                EnvironmentStatus.DESTROYED,
            }:
                continue
            if await is_state_locked(candidate.id, scope="environment"):
                await self._logs.create(
                    environment_id=candidate.id,
                    message=(
                        f"PR #{details.pr_number} {reason}: teardown deferred - "
                        f"{PROVISIONING_IN_PROGRESS_MESSAGE}"
                    ),
                    log_level=LogLevel.WARN,
                )
                await self._session.commit()
                continue

            await self._environments.update_status(candidate, EnvironmentStatus.TEARDOWN_PENDING)
            await self._logs.create(
                environment_id=candidate.id,
                message=(
                    f"PR #{details.pr_number} {reason}: auto-teardown queued "
                    f"(correlation_id={correlation_id})"
                ),
                log_level=LogLevel.INFO,
            )
            await audit.record(
                action=AuditAction.TEARDOWN_INITIATED,
                actor_id=AuditService.webhook_actor("github"),
                status=AuditStatus.PENDING,
                environment_id=candidate.id,
                workspace_id=candidate.workspace_id,
                commit_sha=details.short_sha,
                detail=f"pull_request:closed:{reason}",
            )
            await self._session.commit()

            await publish_env_event(
                candidate.id,
                event_type="STATUS_CHANGE",
                status=EnvironmentStatus.TEARDOWN_PENDING.value,
                commit_sha=details.short_sha,
                message=f"Auto-teardown from PR #{details.pr_number} {reason}",
            )
            enqueue_teardown_environment(
                environment_id=str(candidate.id),
                correlation_id=correlation_id,
            )
            matched_ids.append(candidate.id)
            logger.info(
                "webhook_pr_teardown_enqueued",
                environment_id=str(candidate.id),
                pr=details.pr_number,
                reason=reason,
            )

        return WebhookProcessResult(
            accepted=True,
            event="pull_request",
            matched_environment_ids=matched_ids,
            message=f"Queued teardown for {len(matched_ids)} PR preview(s)",
        )

    async def _process_push(
        self,
        *,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> WebhookProcessResult:
        from app.workers.tasks import enqueue_rebuild_environment

        details = self.parse_push_event(payload)
        if details is None:
            return WebhookProcessResult(
                accepted=True,
                event="push",
                matched_environment_ids=[],
                message="Push event missing repository/branch/commit details (or tag/delete)",
            )

        # Match the pushed repo against each environment's primary repo AND any
        # repo linked into its workspace, so a push to the frontend OR the backend
        # (or any linked repo) re-provisions the environment with the new commit.
        matches = await self._environments.list_active_for_any_linked_repo(
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
                event="push",
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
            event="push",
            matched_environment_ids=matched_ids,
            message=f"Queued rebuild for {len(matched_ids)} environment(s)",
        )

    async def process_actions_cd_notify(
        self,
        *,
        repository_full_name: str,
        branch: str,
        commit_sha: str,
        correlation_id: str,
        workspace_id: str | None = None,
    ) -> WebhookProcessResult:
        """Rebuild matching envs from a GitHub Actions CD notify (Option B)."""
        from app.services.git_urls import short_commit_sha
        from app.workers.tasks import enqueue_rebuild_environment

        full_name = repository_full_name.strip()
        branch_name = branch.strip()
        short = short_commit_sha(commit_sha) or commit_sha.strip()[:7]
        if not full_name or not branch_name or not short:
            return WebhookProcessResult(
                accepted=True,
                event="github_actions_cd",
                matched_environment_ids=[],
                message="Missing repository, branch, or commit",
            )

        matches = await self._environments.list_active_for_any_linked_repo(
            repo_full_name=full_name,
            branch=branch_name,
        )
        if workspace_id and workspace_id.strip():
            try:
                ws_uuid = UUID(workspace_id.strip())
            except ValueError:
                ws_uuid = None
            if ws_uuid is not None:
                scoped = [env for env in matches if env.workspace_id == ws_uuid]
                if scoped:
                    matches = scoped

        if not matches:
            logger.info(
                "webhook_actions_cd_no_match",
                repo=full_name,
                branch=branch_name,
                commit=short,
                workspace_id=workspace_id,
            )
            return WebhookProcessResult(
                accepted=True,
                event="github_actions_cd",
                matched_environment_ids=[],
                message="No active environments matched repository/branch",
            )

        matched_ids: list[UUID] = []
        audit = AuditService(self._session)
        for candidate in matches:
            if await is_state_locked(candidate.id, scope="environment"):
                logger.warning(
                    "webhook_actions_cd_skipped_lock_held",
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
                    actor_id=AuditService.webhook_actor("github-actions-cd"),
                    status=AuditStatus.REJECTED,
                    environment_id=candidate.id,
                    workspace_id=candidate.workspace_id,
                    commit_sha=short,
                    detail=PROVISIONING_IN_PROGRESS_MESSAGE,
                )
                await self._session.commit()
                continue

            environment = await self._environments.mark_rebuild(
                candidate.id,
                commit_sha=short,
            )
            if environment is None:
                continue

            await self._logs.create(
                environment_id=environment.id,
                message=(
                    f"GitHub Actions CD notify: rebuild queued for "
                    f"{full_name}@{branch_name} ({short})"
                ),
                log_level=LogLevel.INFO,
            )
            await audit.record(
                action=AuditAction.REBUILD_INITIATED,
                actor_id=AuditService.webhook_actor("github-actions-cd"),
                status=AuditStatus.PENDING,
                environment_id=environment.id,
                workspace_id=environment.workspace_id,
                commit_sha=short,
            )
            await self._session.commit()

            await publish_env_event(
                environment.id,
                event_type="STATUS_CHANGE",
                status=EnvironmentStatus.PROVISIONING.value,
                commit_sha=short,
                message="Rebuild queued from GitHub Actions CD",
            )
            enqueue_rebuild_environment(
                environment_id=str(environment.id),
                commit_sha=short,
                correlation_id=correlation_id,
            )
            matched_ids.append(environment.id)
            logger.info(
                "webhook_actions_cd_enqueued",
                environment_id=str(environment.id),
                repo=full_name,
                branch=branch_name,
                commit=short,
                correlation_id=correlation_id,
            )

        return WebhookProcessResult(
            accepted=True,
            event="github_actions_cd",
            matched_environment_ids=matched_ids,
            message=f"Queued rebuild for {len(matched_ids)} environment(s)",
        )


@dataclass(frozen=True, slots=True)
class GitLabPushEventDetails:
    repository_full_name: str
    branch: str
    commit_sha: str
    full_commit_sha: str


class GitLabWebhookService:
    """Process GitLab webhook events (primarily push) for GitOps rebuilds."""

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
        _ = body
        return bool(signature_header) and signature_header == secret

    @staticmethod
    def parse_push_event(payload: dict[str, Any]) -> GitLabPushEventDetails | None:
        if payload.get("object_kind") != "push":
            return None

        ref = payload.get("ref")
        if not isinstance(ref, str):
            return None
        branch = branch_from_git_ref(ref)
        if branch is None:
            return None

        project = payload.get("project")
        if not isinstance(project, dict):
            return None
        path_with_namespace = project.get("path_with_namespace")
        if not isinstance(path_with_namespace, str) or not path_with_namespace.strip():
            return None

        after = payload.get("after")
        checkout_sha = payload.get("checkout_sha")
        full_commit_sha: str | None = None
        if isinstance(after, str) and after.strip():
            full_commit_sha = after.strip()
        elif isinstance(checkout_sha, str) and checkout_sha.strip():
            full_commit_sha = checkout_sha.strip()
        if not full_commit_sha:
            return None

        short_sha = short_commit_sha(full_commit_sha) or full_commit_sha[:7]
        return GitLabPushEventDetails(
            repository_full_name=path_with_namespace.strip(),
            branch=branch,
            commit_sha=short_sha,
            full_commit_sha=full_commit_sha,
        )

    async def process_event(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> WebhookProcessResult:
        _ = event_name
        if payload.get("object_kind") == "push":
            return await self._process_push(payload=payload, correlation_id=correlation_id)

        return WebhookProcessResult(
            accepted=True,
            event=str(payload.get("object_kind") or event_name or "unknown"),
            matched_environment_ids=[],
            message="Ignored event",
        )

    async def _process_push(
        self,
        *,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> WebhookProcessResult:
        from app.workers.tasks import enqueue_rebuild_environment

        details = self.parse_push_event(payload)
        if details is None:
            return WebhookProcessResult(
                accepted=True,
                event="push",
                matched_environment_ids=[],
                message="Push event missing repository/branch/commit details (or tag/delete)",
            )

        matches = await self._environments.list_active_for_any_linked_repo(
            repo_full_name=details.repository_full_name,
            branch=details.branch,
        )
        if not matches:
            logger.info(
                "webhook_gitlab_push_no_match",
                repo=details.repository_full_name,
                branch=details.branch,
                commit=details.commit_sha,
            )
            return WebhookProcessResult(
                accepted=True,
                event="push",
                matched_environment_ids=[],
                message="No active environments matched repository/branch",
            )

        matched_ids: list[UUID] = []
        audit = AuditService(self._session)
        for candidate in matches:
            if await is_state_locked(candidate.id, scope="environment"):
                logger.warning(
                    "webhook_gitlab_rebuild_skipped_lock_held",
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
                    actor_id=AuditService.webhook_actor("gitlab"),
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
                actor_id=AuditService.webhook_actor("gitlab"),
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
                message="Rebuild queued from GitLab push",
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
                "webhook_gitlab_rebuild_enqueued",
                environment_id=str(environment.id),
                repo=details.repository_full_name,
                branch=details.branch,
                commit=details.commit_sha,
                correlation_id=correlation_id,
            )

        return WebhookProcessResult(
            accepted=True,
            event="push",
            matched_environment_ids=matched_ids,
            message=f"Queued rebuild for {len(matched_ids)} environment(s)",
        )

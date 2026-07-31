from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (
    DeploymentLog,
    Environment,
    EnvironmentStatus,
    ExecutionStage,
    LogLevel,
)
from app.services.git_urls import normalize_git_repo_full_name

ACTIVE_REBUILD_STATUSES = (
    EnvironmentStatus.RUNNING,
    EnvironmentStatus.FAILED,
    EnvironmentStatus.PROVISIONING,
)

# Used for PR-linked lifecycle (rebuild + destroy-on-close).
PR_LINKED_ACTIVE_STATUSES = (
    EnvironmentStatus.RUNNING,
    EnvironmentStatus.FAILED,
    EnvironmentStatus.PROVISIONING,
    EnvironmentStatus.PAUSED,
)

ACTIVE_CONCURRENCY_STATUSES = (
    EnvironmentStatus.PROVISIONING,
    EnvironmentStatus.RUNNING,
    EnvironmentStatus.TEARDOWN_PENDING,
)


class EnvironmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, environment_id: UUID) -> Environment | None:
        result = await self._session.execute(
            select(Environment).where(Environment.id == environment_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, environment_id: UUID) -> Environment | None:
        result = await self._session.execute(
            select(Environment)
            .where(Environment.id == environment_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Environment | None:
        """Return a non-destroyed environment with this name, if any."""
        result = await self._session.execute(
            select(Environment).where(
                Environment.name == name,
                Environment.status != EnvironmentStatus.DESTROYED,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_owner(
        self,
        owner_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Environment]:
        result = await self._session.execute(
            select(Environment)
            .where(Environment.owner_id == owner_id)
            .order_by(Environment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def list_for_org(
        self,
        org_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Environment]:
        result = await self._session.execute(
            select(Environment)
            .where(Environment.org_id == org_id)
            .order_by(Environment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_active_for_owner(self, owner_id: UUID) -> int:
        result = await self._session.execute(
            select(Environment).where(
                Environment.owner_id == owner_id,
                Environment.status.in_(ACTIVE_CONCURRENCY_STATUSES),
            )
        )
        return len(list(result.scalars().all()))

    async def list_active_for_repo_pr(
        self,
        *,
        repo_full_name: str,
        pr_number: int,
    ) -> list[Environment]:
        """Active environments linked to a specific GitHub pull request."""
        normalized_target = normalize_git_repo_full_name(repo_full_name)
        if normalized_target is None:
            return []

        result = await self._session.execute(
            select(Environment).where(
                Environment.github_pr_number == pr_number,
                Environment.status.in_(PR_LINKED_ACTIVE_STATUSES),
            )
        )
        matched: list[Environment] = []
        for environment in result.scalars().all():
            env_repo = normalize_git_repo_full_name(environment.git_repo_url)
            if env_repo == normalized_target:
                matched.append(environment)
        return matched

    async def list_active_for_repo_branch(
        self,
        *,
        repo_full_name: str,
        branch: str,
    ) -> list[Environment]:
        normalized_target = normalize_git_repo_full_name(repo_full_name)
        if normalized_target is None:
            return []

        result = await self._session.execute(
            select(Environment).where(
                Environment.git_branch == branch,
                Environment.status.in_(ACTIVE_REBUILD_STATUSES),
            )
        )
        matched: list[Environment] = []
        for environment in result.scalars().all():
            env_repo = normalize_git_repo_full_name(environment.git_repo_url)
            if env_repo == normalized_target:
                matched.append(environment)
        return matched

    async def create(
        self,
        *,
        owner_id: UUID,
        name: str,
        git_branch: str,
        git_repo_url: str,
        namespace_name: str,
        ttl_expires_at: datetime,
        cost_estimate_hourly: Decimal,
        workspace_id: UUID | None = None,
        org_id: UUID | None = None,
        latest_commit_sha: str | None = None,
        template_id: str | None = None,
        preview_url: str | None = None,
        provider: str | None = None,
        workload_image: str | None = None,
        github_pr_number: int | None = None,
        github_pr_url: str | None = None,
        deploy_mode: str = "preview",
        manifest_packaging: str | None = None,
        enable_postgres: bool = False,
        enable_redis: bool = False,
    ) -> Environment:
        environment = Environment(
            owner_id=owner_id,
            org_id=org_id,
            workspace_id=workspace_id,
            name=name,
            git_branch=git_branch,
            git_repo_url=git_repo_url,
            latest_commit_sha=latest_commit_sha,
            namespace_name=namespace_name,
            status=EnvironmentStatus.PROVISIONING,
            ttl_expires_at=ttl_expires_at,
            cost_estimate_hourly=cost_estimate_hourly,
            template_id=template_id,
            preview_url=preview_url,
            provider=provider,
            workload_image=workload_image,
            github_pr_number=github_pr_number,
            github_pr_url=github_pr_url,
            deploy_mode=deploy_mode,
            manifest_packaging=manifest_packaging,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
        )
        self._session.add(environment)
        await self._session.flush()
        await self._session.refresh(environment)
        return environment

    async def update_status(
        self,
        environment: Environment,
        status: EnvironmentStatus,
        *,
        error_message: str | None = None,
        latest_commit_sha: str | None = None,
        preview_url: str | None = None,
        node_port: int | None = None,
        workload_image: str | None = None,
        github_pr_url: str | None = None,
    ) -> Environment:
        environment.status = status
        environment.error_message = error_message
        if latest_commit_sha is not None:
            environment.latest_commit_sha = latest_commit_sha
        if preview_url is not None:
            environment.preview_url = preview_url
        if node_port is not None:
            environment.node_port = node_port
        if workload_image is not None:
            environment.workload_image = workload_image
        if github_pr_url is not None:
            environment.github_pr_url = github_pr_url
        if status == EnvironmentStatus.DESTROYED:
            self._release_unique_identity(environment)
        await self._session.flush()
        await self._session.refresh(environment)
        return environment

    @staticmethod
    def _release_unique_identity(environment: Environment) -> None:
        """Free unique name/namespace so the same name can be relaunched."""
        suffix = str(environment.id).replace("-", "")[:12]
        if "--destroyed-" not in environment.name:
            # Keep human-readable prefix; stay within String(128).
            base = environment.name[: max(1, 128 - len(suffix) - 12)]
            environment.name = f"{base}--destroyed-{suffix}"
        if not environment.namespace_name.startswith("destroyed-"):
            environment.namespace_name = f"destroyed-{environment.id}"[:253]

    async def update_ttl(
        self,
        environment: Environment,
        ttl_expires_at: datetime,
    ) -> Environment:
        environment.ttl_expires_at = ttl_expires_at
        await self._session.flush()
        await self._session.refresh(environment)
        return environment

    async def mark_rebuild(
        self,
        environment_id: UUID,
        *,
        commit_sha: str,
    ) -> Environment | None:
        """Atomically mark an active environment PROVISIONING for a GitOps rebuild."""
        environment = await self.get_by_id_for_update(environment_id)
        if environment is None:
            return None
        if environment.status not in ACTIVE_REBUILD_STATUSES:
            return None
        if (
            environment.status == EnvironmentStatus.PROVISIONING
            and environment.latest_commit_sha == commit_sha
        ):
            return None

        environment.status = EnvironmentStatus.PROVISIONING
        environment.latest_commit_sha = commit_sha
        environment.error_message = None
        await self._session.flush()
        await self._session.refresh(environment)
        return environment

    async def list_expired_running(self, *, now: datetime | None = None) -> list[Environment]:
        """Environments past TTL that still consume cluster capacity.

        Includes RUNNING and FAILED (Failed previews often still have pods / NodePorts).
        """
        cutoff = now or datetime.now(UTC)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        result = await self._session.execute(
            select(Environment).where(
                Environment.status.in_(
                    (
                        EnvironmentStatus.RUNNING,
                        EnvironmentStatus.FAILED,
                    )
                ),
                Environment.ttl_expires_at <= cutoff,
            )
        )
        return list(result.scalars().all())

    async def list_running(self) -> list[Environment]:
        result = await self._session.execute(
            select(Environment)
            .where(Environment.status == EnvironmentStatus.RUNNING)
            .order_by(Environment.created_at.asc())
        )
        return list(result.scalars().all())


class DeploymentLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        environment_id: UUID,
        message: str,
        log_level: LogLevel = LogLevel.INFO,
        stage: ExecutionStage | None = None,
        timestamp: datetime | None = None,
    ) -> DeploymentLog:
        entry = DeploymentLog(
            environment_id=environment_id,
            message=message,
            log_level=log_level,
            stage=stage,
            timestamp=timestamp or datetime.now(UTC),
        )
        self._session.add(entry)
        await self._session.flush()
        await self._session.refresh(entry)
        return entry

    async def list_for_environment(
        self,
        environment_id: UUID,
        *,
        limit: int = 1000,
    ) -> list[DeploymentLog]:
        result = await self._session.execute(
            select(DeploymentLog)
            .where(DeploymentLog.environment_id == environment_id)
            .order_by(DeploymentLog.timestamp.asc(), DeploymentLog.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

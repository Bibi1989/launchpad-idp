from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    EnvironmentStatus.EXPIRED,
)

ACTIVE_CONCURRENCY_STATUSES = (
    EnvironmentStatus.PROVISIONING,
    EnvironmentStatus.RUNNING,
    EnvironmentStatus.TEARDOWN_PENDING,
)

# For "running" governance caps: previews that are actively consuming runtime slots.
ACTIVE_RUNNING_STATUSES = (
    EnvironmentStatus.PROVISIONING,
    EnvironmentStatus.RUNNING,
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

    async def get_by_name(
        self,
        name: str,
        *,
        org_id: UUID | None = None,
    ) -> Environment | None:
        """Return a non-destroyed environment with this name in the org (or globally if org omitted)."""
        filters = [
            Environment.name == name,
            Environment.status.notin_(
                (EnvironmentStatus.DESTROYED, EnvironmentStatus.TEARDOWN_PENDING)
            ),
        ]
        if org_id is not None:
            filters.append(Environment.org_id == org_id)
        result = await self._session.execute(select(Environment).where(*filters))
        return result.scalar_one_or_none()

    async def list_for_owner(
        self,
        owner_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
        include_terminating: bool = False,
    ) -> list[Environment]:
        filters = [Environment.owner_id == owner_id]
        if not include_terminating:
            filters.append(
                Environment.status.notin_(
                    (EnvironmentStatus.DESTROYED, EnvironmentStatus.TEARDOWN_PENDING)
                )
            )
        result = await self._session.execute(
            select(Environment)
            .where(*filters)
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
        include_terminating: bool = False,
    ) -> list[Environment]:
        filters = [Environment.org_id == org_id]
        if not include_terminating:
            filters.append(
                Environment.status.notin_(
                    (EnvironmentStatus.DESTROYED, EnvironmentStatus.TEARDOWN_PENDING)
                )
            )
        result = await self._session.execute(
            select(Environment)
            .where(*filters)
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

    async def count_active_for_owner_project(self, owner_id: UUID, project_id: UUID) -> int:
        result = await self._session.execute(
            select(Environment).where(
                Environment.owner_id == owner_id,
                Environment.project_id == project_id,
                Environment.status.in_(ACTIVE_RUNNING_STATUSES),
            )
        )
        return len(list(result.scalars().all()))

    async def list_active_for_owner_project(
        self,
        owner_id: UUID,
        project_id: UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Environment]:
        stmt = (
            select(Environment)
            .where(
                Environment.owner_id == owner_id,
                Environment.project_id == project_id,
                Environment.status.in_(ACTIVE_RUNNING_STATUSES),
            )
            .order_by(Environment.created_at.asc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_active_for_org(self, org_id: UUID) -> int:
        """Active preview count for the whole ORGANISATION (all users/projects in it)."""
        result = await self._session.execute(
            select(Environment).where(
                Environment.org_id == org_id,
                Environment.status.in_(ACTIVE_RUNNING_STATUSES),
            )
        )
        return len(list(result.scalars().all()))

    async def list_active_for_org(
        self,
        org_id: UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Environment]:
        """Active previews for an org, oldest first (for per-org cap enforcement)."""
        stmt = (
            select(Environment)
            .where(
                Environment.org_id == org_id,
                Environment.status.in_(ACTIVE_RUNNING_STATUSES),
            )
            .order_by(Environment.created_at.asc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

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
        ttl_expires_at: datetime | None,
        cost_estimate_hourly: Decimal,
        project_id: UUID | None = None,
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
        kubernetes_image_source: str | None = None,
        kubernetes_image_scan_json: str | None = None,
        enable_postgres: bool = False,
        enable_redis: bool = False,
        lifecycle_stage: str = "preview",
        promotion_lineage_id: UUID | None = None,
        promoted_from_id: UUID | None = None,
        start_ttl_on_running: bool = False,
    ) -> Environment:
        ttl_duration_seconds: int | None = None
        if ttl_expires_at is not None:
            # We calculate this using a naive duration from now.
            # It will be applied EXACTLY when the environment becomes RUNNING.
            duration = (ttl_expires_at - datetime.now(UTC)).total_seconds()
            if duration > 0:
                ttl_duration_seconds = int(duration)

        # When start_ttl_on_running is set (the normal provision path), the TTL clock does
        # NOT start at creation - only the duration is captured, and update_status() applies
        # ttl_expires_at when the environment reaches RUNNING. This keeps a PROVISIONING /
        # FAILED environment from showing a misleading countdown before it ever succeeds.
        # Direct callers that pass an explicit ttl_expires_at (tests, promotion) keep it.
        initial_ttl_expires_at = None if start_ttl_on_running else ttl_expires_at

        environment = Environment(
            owner_id=owner_id,
            org_id=org_id,
            project_id=project_id,
            workspace_id=workspace_id,
            name=name,
            git_branch=git_branch,
            git_repo_url=git_repo_url,
            latest_commit_sha=latest_commit_sha,
            namespace_name=namespace_name,
            status=EnvironmentStatus.PROVISIONING,
            ttl_expires_at=initial_ttl_expires_at,
            ttl_duration_seconds=ttl_duration_seconds,
            cost_estimate_hourly=cost_estimate_hourly,
            template_id=template_id,
            preview_url=preview_url,
            provider=provider,
            workload_image=workload_image,
            github_pr_number=github_pr_number,
            github_pr_url=github_pr_url,
            deploy_mode=deploy_mode,
            manifest_packaging=manifest_packaging,
            kubernetes_image_source=kubernetes_image_source,
            kubernetes_image_scan_json=kubernetes_image_scan_json,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
            lifecycle_stage=lifecycle_stage,
            promotion_lineage_id=promotion_lineage_id,
            promoted_from_id=promoted_from_id,
        )
        self._session.add(environment)
        await self._session.flush()
        if environment.promotion_lineage_id is None:
            environment.promotion_lineage_id = environment.id
            await self._session.flush()
        await self._session.refresh(environment)
        return environment

    async def update_status(
        self,
        environment: Environment,
        status: EnvironmentStatus,
        *,
        error_message: str | None = None,
        failure_summary: str | None = None,
        seed_status: str | None = None,
        latest_commit_sha: str | None = None,
        preview_url: str | None = None,
        node_port: int | None = None,
        workload_image: str | None = None,
        github_pr_url: str | None = None,
        preview_endpoints_json: str | None = None,
        update_seed_status: bool = False,
    ) -> Environment:
        if status == EnvironmentStatus.RUNNING and environment.status != EnvironmentStatus.RUNNING:
            if environment.ttl_duration_seconds is not None:
                environment.ttl_expires_at = datetime.now(UTC) + timedelta(seconds=environment.ttl_duration_seconds)
                # Clear so we don't double-shift on rebuilds
                environment.ttl_duration_seconds = None

        environment.status = status
        environment.error_message = error_message
        if status == EnvironmentStatus.FAILED:
            environment.failure_summary = failure_summary
        elif status in (
            EnvironmentStatus.PROVISIONING,
            EnvironmentStatus.RUNNING,
            EnvironmentStatus.DESTROYED,
            EnvironmentStatus.TEARDOWN_PENDING,
        ):
            environment.failure_summary = None
        if update_seed_status:
            environment.seed_status = seed_status
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
        if preview_endpoints_json is not None:
            environment.preview_endpoints_json = preview_endpoints_json
        if status in (EnvironmentStatus.DESTROYED, EnvironmentStatus.TEARDOWN_PENDING):
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
        extend_delta: timedelta | None = None,
    ) -> Environment:
        environment.ttl_expires_at = ttl_expires_at
        if extend_delta is not None and environment.ttl_duration_seconds is not None:
            environment.ttl_duration_seconds += int(extend_delta.total_seconds())
        await self._session.flush()
        await self._session.refresh(environment)
        return environment

    async def update_cost(
        self,
        environment: Environment,
        *,
        cost_accrued: Decimal,
        cost_sampled_at: datetime,
        cost_source: str | None,
        cost_estimate_hourly: Decimal | None = None,
    ) -> Environment:
        environment.cost_accrued = cost_accrued
        environment.cost_sampled_at = cost_sampled_at
        environment.cost_source = cost_source
        if cost_estimate_hourly is not None:
            environment.cost_estimate_hourly = cost_estimate_hourly
        await self._session.flush()
        await self._session.refresh(environment)
        return environment

    async def list_billable_for_cost_metering(self) -> list[Environment]:
        """Environments that still consume cluster capacity for cost sampling."""
        result = await self._session.execute(
            select(Environment)
            .where(
                Environment.status.in_(
                    (
                        EnvironmentStatus.RUNNING,
                        EnvironmentStatus.PROVISIONING,
                        EnvironmentStatus.FAILED,
                    )
                )
            )
            .order_by(Environment.created_at.asc())
        )
        return list(result.scalars().all())

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
        environment.failure_summary = None
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
                Environment.ttl_expires_at.is_not(None),
                Environment.ttl_expires_at <= cutoff,
                Environment.lifecycle_stage != "production",
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

    async def count_non_destroyed_for_workspace(
        self,
        workspace_id: UUID,
        *,
        exclude_environment_id: UUID | None = None,
    ) -> int:
        """Environments still linked to a workspace (not DESTROYED)."""
        stmt = select(Environment).where(
            Environment.workspace_id == workspace_id,
            Environment.status != EnvironmentStatus.DESTROYED,
        )
        if exclude_environment_id is not None:
            stmt = stmt.where(Environment.id != exclude_environment_id)
        result = await self._session.execute(stmt)
        return len(list(result.scalars().all()))


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

    async def latest_stage_for(self, environment_id: UUID) -> ExecutionStage | None:
        """Most recent execution stage logged for an environment.

        Lets the detail page restore the pipeline to the real stage (BUILD/APPLY) on a
        browser reload, instead of resetting to INIT until the next live event.
        """
        result = await self._session.execute(
            select(DeploymentLog.stage)
            .where(
                DeploymentLog.environment_id == environment_id,
                DeploymentLog.stage.is_not(None),
            )
            .order_by(DeploymentLog.timestamp.desc(), DeploymentLog.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

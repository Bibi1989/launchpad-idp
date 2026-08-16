"""Lifecycle stage promotion: preview → staging → production with approvals."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.domain import (
    AuditAction,
    AuditStatus,
    Environment,
    EnvironmentStatus,
    LifecycleStage,
    Organization,
    OrgRole,
    PromotionRequest,
    PromotionRequestStatus,
    User,
)
from app.repositories.environment import DeploymentLogRepository, EnvironmentRepository
from app.schemas.environment import EnvironmentRead
from app.schemas.promotion import (
    OrgPromotionPolicyRead,
    OrgPromotionPolicyUpdate,
    PromotionRequestRead,
    PromotionReviewRequest,
    StagePromoteRequest,
    StagePromoteResponse,
    StagePromoteTarget,
)
from app.services.audit import AuditService
from app.services.orgs import OrganizationService, role_at_least
from app.workers.tasks import enqueue_provision_environment

logger = get_logger(__name__)

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    LifecycleStage.PREVIEW.value: frozenset(
        {LifecycleStage.STAGING.value, LifecycleStage.PRODUCTION.value}
    ),
    LifecycleStage.STAGING.value: frozenset({LifecycleStage.PRODUCTION.value}),
    LifecycleStage.PRODUCTION.value: frozenset(),
}

_PROMOTE_SOURCE_STATUSES = frozenset(
    {EnvironmentStatus.RUNNING, EnvironmentStatus.FAILED},
)

_DEFAULT_STAGING_TTL_HOURS = 168


class PromotionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._environments = EnvironmentRepository(session)
        self._logs = DeploymentLogRepository(session)

    async def get_org_policy(self, org_id: UUID) -> OrgPromotionPolicyRead:
        org = await self._session.get(Organization, org_id)
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "org_not_found", "message": "Organization not found"},
            )
        return OrgPromotionPolicyRead(
            staging_requires_approval=bool(org.promotion_staging_requires_approval),
            production_requires_approval=bool(org.promotion_production_requires_approval),
        )

    async def update_org_policy(
        self,
        org_id: UUID,
        payload: OrgPromotionPolicyUpdate,
        *,
        actor: User,
    ) -> OrgPromotionPolicyRead:
        orgs = OrganizationService(self._session)
        ctx = await orgs.resolve_context(user=actor, org_id=org_id)
        orgs.require_role(ctx, OrgRole.ADMIN)
        org = ctx.organization
        if payload.staging_requires_approval is not None:
            org.promotion_staging_requires_approval = payload.staging_requires_approval
        if payload.production_requires_approval is not None:
            org.promotion_production_requires_approval = payload.production_requires_approval
        await self._session.commit()
        await self._session.refresh(org)
        return OrgPromotionPolicyRead(
            staging_requires_approval=bool(org.promotion_staging_requires_approval),
            production_requires_approval=bool(org.promotion_production_requires_approval),
        )

    async def list_for_org(
        self,
        org_id: UUID,
        *,
        status_filter: str | None = None,
        actor: User,
    ) -> list[PromotionRequestRead]:
        orgs = OrganizationService(self._session)
        await orgs.resolve_context(user=actor, org_id=org_id)
        stmt = select(PromotionRequest).where(PromotionRequest.org_id == org_id)
        if status_filter:
            stmt = stmt.where(PromotionRequest.status == status_filter)
        stmt = stmt.order_by(PromotionRequest.created_at.desc()).limit(100)
        rows = list((await self._session.execute(stmt)).scalars().all())
        return [await self._to_read(row) for row in rows]

    async def request_promote(
        self,
        environment_id: UUID,
        payload: StagePromoteRequest,
        *,
        owner: User,
        correlation_id: str,
        org_id: UUID,
    ) -> StagePromoteResponse:
        source = await self._require_source(environment_id, owner, org_id)
        target_stage = payload.target_stage.value
        source_stage = (source.lifecycle_stage or LifecycleStage.PREVIEW.value).lower()
        allowed = _ALLOWED_TRANSITIONS.get(source_stage, frozenset())
        if target_stage not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_stage_transition",
                    "message": (
                        f"Cannot promote from {source_stage} to {target_stage}. "
                        f"Allowed: {', '.join(sorted(allowed)) or 'none'}"
                    ),
                },
            )
        if source.status not in _PROMOTE_SOURCE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "environment_not_promotable",
                    "message": "Environment must be RUNNING or FAILED to promote",
                },
            )

        pending = await self._pending_for_source(source.id)
        if pending is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "promotion_already_pending",
                    "message": "A promotion request is already pending for this environment",
                    "details": {"promotion_id": str(pending.id)},
                },
            )

        org = await self._session.get(Organization, org_id)
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "org_not_found", "message": "Organization not found"},
            )

        requires_approval = (
            target_stage == StagePromoteTarget.PRODUCTION.value
            and bool(org.promotion_production_requires_approval)
        ) or (
            target_stage == StagePromoteTarget.STAGING.value
            and bool(org.promotion_staging_requires_approval)
        )

        name = await self._allocate_name(
            payload.name or f"{source.name}-{target_stage}"[:64],
            org_id=org_id,
        )
        body = {
            "name": name,
            "ttl_hours": payload.ttl_hours,
            "correlation_id": correlation_id,
        }
        request = PromotionRequest(
            org_id=org_id,
            source_environment_id=source.id,
            target_stage=target_stage,
            status=PromotionRequestStatus.PENDING.value,
            requested_by=owner.id,
            payload_json=json.dumps(body),
        )
        self._session.add(request)
        await self._session.flush()

        await AuditService(self._session).record(
            action=AuditAction.PROMOTION_REQUESTED,
            actor_id=AuditService.user_actor(owner.id),
            status=AuditStatus.PENDING if requires_approval else AuditStatus.SUCCESS,
            environment_id=source.id,
            workspace_id=source.workspace_id,
            commit_sha=source.latest_commit_sha,
            detail=f"target_stage={target_stage} requires_approval={requires_approval}",
        )

        if requires_approval:
            await self._logs.create(
                environment_id=source.id,
                message=(
                    f"Promotion to {target_stage} requested (pending approval) "
                    f"as {name} (request={request.id})"
                ),
            )
            await self._session.commit()
            await self._session.refresh(request)
            return StagePromoteResponse(
                promotion=await self._to_read(request, requires_approval=True),
                environment_id=None,
                environment=None,
            )

        env_read = await self._execute_promotion(
            request,
            source=source,
            actor=owner,
            correlation_id=correlation_id,
        )
        return StagePromoteResponse(
            promotion=await self._to_read(request, requires_approval=False, executed=True),
            environment_id=env_read.id,
            environment=env_read.model_dump(mode="json"),
        )

    async def approve(
        self,
        promotion_id: UUID,
        payload: PromotionReviewRequest,
        *,
        actor: User,
        correlation_id: str,
    ) -> StagePromoteResponse:
        request = await self._get_request(promotion_id)
        orgs = OrganizationService(self._session)
        ctx = await orgs.resolve_context(user=actor, org_id=request.org_id)
        orgs.require_role(ctx, OrgRole.ADMIN)

        if request.status != PromotionRequestStatus.PENDING.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "promotion_not_pending",
                    "message": f"Promotion is {request.status}, not pending",
                },
            )

        source = await self._session.get(Environment, request.source_environment_id)
        if source is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "source_not_found", "message": "Source environment missing"},
            )

        request.status = PromotionRequestStatus.APPROVED.value
        request.reviewed_by = actor.id
        request.reviewed_at = datetime.now(UTC)
        request.review_note = (payload.note or "").strip() or None

        await AuditService(self._session).record(
            action=AuditAction.PROMOTION_APPROVED,
            actor_id=AuditService.user_actor(actor.id),
            status=AuditStatus.SUCCESS,
            environment_id=source.id,
            workspace_id=source.workspace_id,
            commit_sha=source.latest_commit_sha,
            detail=request.review_note or f"approved → {request.target_stage}",
        )

        env_read = await self._execute_promotion(
            request,
            source=source,
            actor=actor,
            correlation_id=correlation_id,
        )
        return StagePromoteResponse(
            promotion=await self._to_read(request, requires_approval=True, executed=True),
            environment_id=env_read.id,
            environment=env_read.model_dump(mode="json"),
        )

    async def reject(
        self,
        promotion_id: UUID,
        payload: PromotionReviewRequest,
        *,
        actor: User,
    ) -> PromotionRequestRead:
        request = await self._get_request(promotion_id)
        orgs = OrganizationService(self._session)
        ctx = await orgs.resolve_context(user=actor, org_id=request.org_id)
        orgs.require_role(ctx, OrgRole.ADMIN)

        if request.status != PromotionRequestStatus.PENDING.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "promotion_not_pending",
                    "message": f"Promotion is {request.status}, not pending",
                },
            )

        request.status = PromotionRequestStatus.REJECTED.value
        request.reviewed_by = actor.id
        request.reviewed_at = datetime.now(UTC)
        request.review_note = (payload.note or "").strip() or None

        await AuditService(self._session).record(
            action=AuditAction.PROMOTION_REJECTED,
            actor_id=AuditService.user_actor(actor.id),
            status=AuditStatus.REJECTED,
            environment_id=request.source_environment_id,
            detail=request.review_note or f"rejected → {request.target_stage}",
        )
        await self._logs.create(
            environment_id=request.source_environment_id,
            message=f"Promotion to {request.target_stage} rejected",
        )
        await self._session.commit()
        await self._session.refresh(request)
        return await self._to_read(request)

    async def _execute_promotion(
        self,
        request: PromotionRequest,
        *,
        source: Environment,
        actor: User,
        correlation_id: str,
    ) -> EnvironmentRead:
        from app.services.environment import EnvironmentService

        body: dict = {}
        if request.payload_json:
            try:
                body = json.loads(request.payload_json)
            except json.JSONDecodeError:
                body = {}

        name = str(body.get("name") or f"{source.name}-{request.target_stage}")[:64]
        name = await self._allocate_name(name, org_id=request.org_id)

        target_stage = request.target_stage
        ttl_expires_at: datetime | None
        if target_stage == LifecycleStage.PRODUCTION.value:
            ttl_expires_at = None
        else:
            hours = body.get("ttl_hours")
            if hours is None:
                hours = _DEFAULT_STAGING_TTL_HOURS
            ttl_expires_at = datetime.now(UTC) + timedelta(hours=int(hours))

        lineage = source.promotion_lineage_id or source.id
        placeholder_namespace = f"launchpad-env-pending-{name}"[:253]

        environment = await self._environments.create(
            owner_id=source.owner_id,
            org_id=request.org_id,
            project_id=source.project_id,
            name=name,
            git_branch=source.git_branch,
            git_repo_url=source.git_repo_url,
            namespace_name=placeholder_namespace,
            ttl_expires_at=ttl_expires_at,
            cost_estimate_hourly=source.cost_estimate_hourly or Decimal("0.0000"),
            workspace_id=source.workspace_id,
            template_id=source.template_id,
            provider=source.provider,
            workload_image=source.workload_image,
            github_pr_number=None,
            github_pr_url=None,
            deploy_mode=source.deploy_mode or "preview",
            manifest_packaging=source.manifest_packaging,
            kubernetes_image_source=source.kubernetes_image_source,
            kubernetes_image_scan_json=source.kubernetes_image_scan_json,
            enable_postgres=bool(source.enable_postgres),
            enable_redis=bool(source.enable_redis),
            lifecycle_stage=target_stage,
            promotion_lineage_id=lineage,
            promoted_from_id=source.id,
            latest_commit_sha=source.latest_commit_sha,
        )
        environment.namespace_name = f"launchpad-env-{environment.id}"
        if environment.promotion_lineage_id is None:
            environment.promotion_lineage_id = lineage
        await self._session.flush()

        request.target_environment_id = environment.id
        request.status = PromotionRequestStatus.COMPLETED.value
        request.completed_at = datetime.now(UTC)

        await self._logs.create(
            environment_id=source.id,
            message=(
                f"Promoted to {target_stage} environment {environment.name} "
                f"({environment.id})"
            ),
        )
        await self._logs.create(
            environment_id=environment.id,
            message=(
                f"Created via stage promotion from {source.name} "
                f"(stage={target_stage}, correlation_id={correlation_id})"
            ),
        )
        await AuditService(self._session).record(
            action=AuditAction.PROMOTION_COMPLETED,
            actor_id=AuditService.user_actor(actor.id),
            status=AuditStatus.SUCCESS,
            environment_id=environment.id,
            workspace_id=environment.workspace_id,
            commit_sha=environment.latest_commit_sha,
            detail=f"from={source.id} stage={target_stage}",
        )
        await AuditService(self._session).record(
            action=AuditAction.PROVISION_INITIATED,
            actor_id=AuditService.user_actor(actor.id),
            status=AuditStatus.PENDING,
            environment_id=environment.id,
            workspace_id=environment.workspace_id,
            detail=f"correlation_id={correlation_id}",
        )
        await self._session.commit()

        enqueue_provision_environment(
            environment_id=str(environment.id),
            correlation_id=correlation_id,
        )
        logger.info(
            "stage_promotion_enqueued",
            source_id=str(source.id),
            target_id=str(environment.id),
            target_stage=target_stage,
            correlation_id=correlation_id,
        )

        # Commit expires ORM attrs; refresh before Pydantic reads columns.
        await self._session.refresh(environment)
        await self._session.refresh(request)
        env_service = EnvironmentService(self._session)
        reads = await env_service._enrich_workspace_names([env_service._to_read(environment)])
        return reads[0]

    async def _require_source(
        self,
        environment_id: UUID,
        owner: User,
        org_id: UUID,
    ) -> Environment:
        environment = await self._environments.get_by_id(environment_id)
        if environment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "environment_not_found", "message": "Environment not found"},
            )
        if environment.org_id is not None and environment.org_id != org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "org_mismatch", "message": "Environment is not in this org"},
            )
        orgs = OrganizationService(self._session)
        ctx = await orgs.resolve_context(user=owner, org_id=org_id)
        if not role_at_least(ctx.role, OrgRole.MEMBER):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Members or above can request promotions"},
            )
        return environment

    async def _get_request(self, promotion_id: UUID) -> PromotionRequest:
        request = await self._session.get(PromotionRequest, promotion_id)
        if request is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "promotion_not_found", "message": "Promotion request not found"},
            )
        return request

    async def _pending_for_source(self, source_id: UUID) -> PromotionRequest | None:
        result = await self._session.execute(
            select(PromotionRequest).where(
                PromotionRequest.source_environment_id == source_id,
                PromotionRequest.status == PromotionRequestStatus.PENDING.value,
            )
        )
        return result.scalars().first()

    async def _allocate_name(self, preferred: str, *, org_id: UUID) -> str:
        base = preferred.strip().lower()[:64]
        if len(base) < 3:
            base = f"env-{base}"[:64]
        name = base
        for i in range(0, 20):
            if i > 0:
                suffix = f"-{i}"
                name = f"{base[:64 - len(suffix)]}{suffix}"
            existing = await self._environments.get_by_name(name, org_id=org_id)
            if existing is None:
                return name
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "name_exhausted", "message": "Could not allocate a unique environment name"},
        )

    async def _to_read(
        self,
        row: PromotionRequest,
        *,
        requires_approval: bool = False,
        executed: bool = False,
    ) -> PromotionRequestRead:
        source_name = None
        target_name = None
        source = await self._session.get(Environment, row.source_environment_id)
        if source is not None:
            source_name = source.name
        if row.target_environment_id is not None:
            target = await self._session.get(Environment, row.target_environment_id)
            if target is not None:
                target_name = target.name
        return PromotionRequestRead(
            id=row.id,
            org_id=row.org_id,
            source_environment_id=row.source_environment_id,
            target_environment_id=row.target_environment_id,
            target_stage=row.target_stage,
            status=row.status,
            requested_by=row.requested_by,
            reviewed_by=row.reviewed_by,
            review_note=row.review_note,
            created_at=row.created_at,
            reviewed_at=row.reviewed_at,
            completed_at=row.completed_at,
            source_environment_name=source_name,
            target_environment_name=target_name,
            requires_approval=requires_approval
            or row.status == PromotionRequestStatus.PENDING.value,
            executed=executed or row.status == PromotionRequestStatus.COMPLETED.value,
        )

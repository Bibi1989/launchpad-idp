"""Lifecycle stage promotion API (preview → staging → production)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.deps.org import CurrentOrg
from app.schemas.promotion import (
    OrgPromotionPolicyRead,
    OrgPromotionPolicyUpdate,
    PromotionRequestRead,
    PromotionReviewRequest,
    StagePromoteRequest,
    StagePromoteResponse,
)
from app.services.promotion import PromotionService

router = APIRouter(tags=["promotions"])


def get_promotion_service(session: AsyncSession = Depends(get_db_session)) -> PromotionService:
    return PromotionService(session)


@router.get(
    "/orgs/{org_id}/promotion-policy",
    response_model=OrgPromotionPolicyRead,
)
async def get_promotion_policy(
    org_id: UUID,
    user: CurrentUser,
    service: PromotionService = Depends(get_promotion_service),
) -> OrgPromotionPolicyRead:
    from app.services.orgs import OrganizationService

    orgs = OrganizationService(service._session)
    await orgs.resolve_context(user=user, org_id=org_id)
    return await service.get_org_policy(org_id)


@router.patch(
    "/orgs/{org_id}/promotion-policy",
    response_model=OrgPromotionPolicyRead,
)
async def update_promotion_policy(
    org_id: UUID,
    payload: OrgPromotionPolicyUpdate,
    user: CurrentUser,
    service: PromotionService = Depends(get_promotion_service),
) -> OrgPromotionPolicyRead:
    return await service.update_org_policy(org_id, payload, actor=user)


@router.get(
    "/orgs/{org_id}/promotions",
    response_model=list[PromotionRequestRead],
)
async def list_promotions(
    org_id: UUID,
    user: CurrentUser,
    status_filter: str | None = Query(default=None, alias="status"),
    service: PromotionService = Depends(get_promotion_service),
) -> list[PromotionRequestRead]:
    return await service.list_for_org(org_id, status_filter=status_filter, actor=user)


@router.post(
    "/environments/{environment_id}/stage-promote",
    response_model=StagePromoteResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def stage_promote_environment(
    environment_id: UUID,
    payload: StagePromoteRequest,
    request: Request,
    user: CurrentUser,
    org: CurrentOrg,
    service: PromotionService = Depends(get_promotion_service),
) -> StagePromoteResponse:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return await service.request_promote(
        environment_id,
        payload,
        owner=user,
        correlation_id=correlation_id,
        org_id=org.org_id,
    )


@router.post(
    "/promotions/{promotion_id}/approve",
    response_model=StagePromoteResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def approve_promotion(
    promotion_id: UUID,
    payload: PromotionReviewRequest,
    request: Request,
    user: CurrentUser,
    service: PromotionService = Depends(get_promotion_service),
) -> StagePromoteResponse:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return await service.approve(
        promotion_id,
        payload,
        actor=user,
        correlation_id=correlation_id,
    )


@router.post(
    "/promotions/{promotion_id}/reject",
    response_model=PromotionRequestRead,
)
async def reject_promotion(
    promotion_id: UUID,
    payload: PromotionReviewRequest,
    user: CurrentUser,
    service: PromotionService = Depends(get_promotion_service),
) -> PromotionRequestRead:
    return await service.reject(promotion_id, payload, actor=user)

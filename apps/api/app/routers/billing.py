"""Stripe billing endpoints for organization Pro plans."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db_session
from app.core.logging import get_logger
from app.deps.auth import CurrentUser
from app.schemas.billing import CheckoutSessionRead, OrgPlanRead, PortalSessionRead
from app.services.billing import BillingService

logger = get_logger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


def get_billing_service(
    session: AsyncSession = Depends(get_db_session),
) -> BillingService:
    return BillingService(session)


@router.get("/orgs/{org_id}/plan", response_model=OrgPlanRead)
async def get_org_plan(
    org_id: UUID,
    user: CurrentUser,
    service: BillingService = Depends(get_billing_service),
) -> OrgPlanRead:
    summary = await service.plan_summary(user=user, org_id=org_id)
    return OrgPlanRead.model_validate(summary)


@router.post("/orgs/{org_id}/checkout", response_model=CheckoutSessionRead)
async def create_checkout(
    org_id: UUID,
    user: CurrentUser,
    service: BillingService = Depends(get_billing_service),
    session: AsyncSession = Depends(get_db_session),
) -> CheckoutSessionRead:
    url = await service.create_checkout_session(user=user, org_id=org_id)
    await session.commit()
    return CheckoutSessionRead(checkout_url=url)


@router.post("/orgs/{org_id}/portal", response_model=PortalSessionRead)
async def create_portal(
    org_id: UUID,
    user: CurrentUser,
    service: BillingService = Depends(get_billing_service),
) -> PortalSessionRead:
    url = await service.create_portal_session(user=user, org_id=org_id)
    return PortalSessionRead(portal_url=url)


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict[str, str]:
    settings = get_settings()
    payload = await request.body()
    secret = (settings.stripe_webhook_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "stripe_webhook_unconfigured", "message": "Webhook secret missing"},
        )
    try:
        import stripe
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "stripe_missing", "message": "stripe package is not installed"},
        ) from exc

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=stripe_signature or "",
            secret=secret,
        )
    except Exception as exc:  # noqa: BLE001 - Stripe raises multiple signature errors
        logger.warning("stripe_webhook_invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_signature", "message": "Invalid Stripe signature"},
        ) from exc

    service = BillingService(session, settings)
    # stripe Event may be a dict-like object
    event_dict = event if isinstance(event, dict) else dict(event)
    try:
        await service.handle_webhook_event(event_dict)
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - do not let webhook retries hide errors
        logger.exception(
            "stripe_webhook_event_failed",
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "stripe_webhook_event_failed", "message": "Webhook handling failed"},
        ) from exc
    return {"status": "ok"}

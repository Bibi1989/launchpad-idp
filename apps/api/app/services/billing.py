"""Stripe Checkout / Customer Portal for organization Pro plans."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.domain import OrgPlan, Organization, OrgRole, User
from app.services.orgs import OrganizationService, role_at_least
from app.services.plans import (
    PRO_MONTHLY_EUR,
    count_org_projects,
    count_org_workspaces,
    limits_for_plan,
)

logger = get_logger(__name__)


class BillingService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._orgs = OrganizationService(session, self._settings)

    def _require_stripe(self) -> Any:
        if not (self._settings.stripe_secret_key or "").strip():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "stripe_unconfigured",
                    "message": "Stripe is not configured (STRIPE_SECRET_KEY)",
                },
            )
        try:
            import stripe
        except ImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "stripe_missing",
                    "message": "stripe package is not installed",
                },
            ) from exc
        stripe.api_key = self._settings.stripe_secret_key
        return stripe

    async def plan_summary(self, *, user: User, org_id: UUID) -> dict[str, Any]:
        ctx = await self._orgs.resolve_context(user=user, org_id=org_id)
        org = ctx.organization
        limits = limits_for_plan(org.plan)
        return {
            "org_id": org.id,
            "plan": org.plan if isinstance(org.plan, OrgPlan) else OrgPlan(str(org.plan)),
            "max_projects": limits.max_projects,
            "max_workspaces": limits.max_workspaces,
            "project_count": await count_org_projects(self._session, org.id),
            "workspace_count": await count_org_workspaces(self._session, org.id),
            "pro_price_eur": PRO_MONTHLY_EUR,
            "stripe_customer_id": org.stripe_customer_id,
            "stripe_subscription_id": org.stripe_subscription_id,
            "plan_updated_at": org.plan_updated_at,
        }

    async def create_checkout_session(self, *, user: User, org_id: UUID) -> str:
        ctx = await self._orgs.resolve_context(user=user, org_id=org_id)
        if not role_at_least(ctx.role, OrgRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Admin role required to upgrade"},
            )
        price_id = (self._settings.stripe_price_id_pro or "").strip()
        if not price_id:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "stripe_price_unconfigured",
                    "message": "STRIPE_PRICE_ID_PRO is not set",
                },
            )
        stripe = self._require_stripe()
        org = ctx.organization
        customer_id = org.stripe_customer_id
        if not customer_id:
            customer = stripe.Customer.create(
                email=user.email,
                name=org.name,
                metadata={"org_id": str(org.id)},
            )
            customer_id = customer["id"]
            org.stripe_customer_id = customer_id
            await self._session.flush()

        app_url = (self._settings.public_app_url or "http://localhost:3000").rstrip("/")
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{app_url}/org?billing=success",
            cancel_url=f"{app_url}/org?billing=cancel",
            client_reference_id=str(org.id),
            metadata={"org_id": str(org.id)},
            subscription_data={"metadata": {"org_id": str(org.id)}},
        )
        url = session.get("url")
        if not url:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "stripe_checkout_failed", "message": "No checkout URL"},
            )
        return str(url)

    async def create_portal_session(self, *, user: User, org_id: UUID) -> str:
        ctx = await self._orgs.resolve_context(user=user, org_id=org_id)
        if not role_at_least(ctx.role, OrgRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Admin role required"},
            )
        if not ctx.organization.stripe_customer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "no_stripe_customer",
                    "message": "No Stripe customer for this organization",
                },
            )
        stripe = self._require_stripe()
        app_url = (self._settings.public_app_url or "http://localhost:3000").rstrip("/")
        session = stripe.billing_portal.Session.create(
            customer=ctx.organization.stripe_customer_id,
            return_url=f"{app_url}/org",
        )
        return str(session["url"])

    async def apply_subscription_status(
        self,
        *,
        org_id: UUID,
        subscription_id: str | None,
        status_value: str,
        customer_id: str | None = None,
    ) -> None:
        org = await self._session.get(Organization, org_id)
        if org is None:
            logger.warning("billing_org_missing", org_id=str(org_id))
            return
        active = status_value in {"active", "trialing"}
        org.plan = OrgPlan.PRO if active else OrgPlan.FREE
        org.plan_updated_at = datetime.now(UTC)
        if subscription_id:
            org.stripe_subscription_id = subscription_id if active else None
        if customer_id:
            org.stripe_customer_id = customer_id
        await self._session.flush()
        logger.info(
            "org_plan_updated",
            org_id=str(org_id),
            plan=org.plan.value,
            subscription_status=status_value,
        )

    async def handle_webhook_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type") or ""
        data_object = (event.get("data") or {}).get("object") or {}

        if event_type == "checkout.session.completed":
            org_raw = (data_object.get("metadata") or {}).get("org_id") or data_object.get(
                "client_reference_id"
            )
            if not org_raw:
                return
            org_id = UUID(str(org_raw))
            sub_id = data_object.get("subscription")
            customer_id = data_object.get("customer")
            await self.apply_subscription_status(
                org_id=org_id,
                subscription_id=str(sub_id) if sub_id else None,
                status_value="active",
                customer_id=str(customer_id) if customer_id else None,
            )
            return

        if event_type in {
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            metadata = data_object.get("metadata") or {}
            org_raw = metadata.get("org_id")
            if not org_raw:
                customer_id = data_object.get("customer")
                if customer_id:
                    result = await self._session.execute(
                        select(Organization).where(
                            Organization.stripe_customer_id == str(customer_id)
                        )
                    )
                    org = result.scalar_one_or_none()
                    if org is None:
                        return
                    org_id = org.id
                else:
                    return
            else:
                org_id = UUID(str(org_raw))
            status_value = "canceled" if event_type.endswith("deleted") else str(
                data_object.get("status") or "canceled"
            )
            await self.apply_subscription_status(
                org_id=org_id,
                subscription_id=str(data_object.get("id") or "") or None,
                status_value=status_value,
                customer_id=str(data_object.get("customer") or "") or None,
            )

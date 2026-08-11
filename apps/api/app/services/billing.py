"""Stripe Checkout / Customer Portal for organization Pro plans."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, sanitize_log_message
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
        try:
            limits = limits_for_plan(org.plan)
            plan_value = org.plan if isinstance(org.plan, OrgPlan) else OrgPlan(str(org.plan))
        except Exception:  # noqa: BLE001 - never break billing UI due to bad persisted enum
            logger.exception("billing_plan_summary_invalid_plan", org_id=str(org.id))
            limits = limits_for_plan(OrgPlan.FREE)
            plan_value = OrgPlan.FREE
        return {
            "org_id": org.id,
            "plan": plan_value,
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
        raw_id = (self._settings.stripe_price_id_pro or "").strip()
        if not raw_id:
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
            try:
                customer = stripe.Customer.create(
                    email=user.email,
                    name=org.name,
                    metadata={"org_id": str(org.id)},
                )
                customer_id = customer["id"]
                org.stripe_customer_id = customer_id
                await self._session.flush()
            except Exception as exc:  # noqa: BLE001 - surface stripe error cleanly
                safe_error = sanitize_log_message(str(exc))
                logger.exception(
                    "stripe_customer_create_failed",
                    org_id=str(org.id),
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "code": "stripe_customer_create_failed",
                        "message": "Stripe customer creation failed",
                        "details": {"stripe_error": safe_error},
                    },
                ) from exc

        if raw_id.startswith("price_"):
            price_id = raw_id
        elif raw_id.startswith("prod_"):
            # Some deployments store the product id instead of the price id.
            # Stripe checkout line_items require a price id, so resolve the
            # product's default price automatically.
            try:
                product = stripe.Product.retrieve(raw_id)
                default_price = (
                    product.get("default_price")
                    if hasattr(product, "get")
                    else getattr(product, "default_price", None)
                )
                if isinstance(default_price, dict):
                    price_id = default_price.get("id")
                else:
                    # Stripe typically returns the default_price as a `price_...` id string.
                    price_id = default_price

                if not price_id or not str(price_id).startswith("price_"):
                    # Fallback: resolve from active Prices attached to the product.
                    # This covers cases where the product exists but default_price is unset.
                    prices = stripe.Price.list(product=raw_id, active=True, limit=1)
                    first = (prices.get("data") or [None])[0] if hasattr(prices, "get") else None
                    if first is None and hasattr(prices, "data"):
                        first = prices.data[0] if prices.data else None
                    resolved = (
                        first.get("id")
                        if first is not None and hasattr(first, "get")
                        else getattr(first, "id", None)
                    )
                    price_id = resolved

                if not price_id:
                    raise ValueError(f"could not resolve price_ for product: default_price={default_price!r}")
                if not str(price_id).startswith("price_"):
                    raise ValueError(f"product default_price is invalid: {price_id!r}")
            except Exception as exc:  # noqa: BLE001
                safe_error = sanitize_log_message(str(exc))
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "stripe_price_resolution_failed",
                        "message": "STRIPE_PRICE_ID_PRO must be a price_ id or a prod_ id with a default price",
                        "details": {"stripe_error": safe_error, "input": raw_id},
                    },
                ) from exc
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "stripe_price_id_invalid",
                    "message": "STRIPE_PRICE_ID_PRO must start with price_ (recommended) or prod_",
                    "details": {"input": raw_id},
                },
            )

        app_url = (self._settings.public_app_url or "http://localhost:3000").rstrip("/")
        try:
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
        except Exception as exc:  # noqa: BLE001 - stripe API can raise many typed errors
            safe_error = sanitize_log_message(str(exc))
            logger.exception(
                "stripe_checkout_create_failed",
                org_id=str(org.id),
                price_id=price_id,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "stripe_checkout_create_failed",
                    "message": "Stripe checkout session creation failed",
                    "details": {"price_id": price_id, "stripe_error": safe_error},
                },
            ) from exc

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
        try:
            session = stripe.billing_portal.Session.create(
                customer=ctx.organization.stripe_customer_id,
                return_url=f"{app_url}/org",
            )
        except Exception as exc:  # noqa: BLE001
            safe_error = sanitize_log_message(str(exc))
            logger.exception(
                "stripe_portal_create_failed",
                org_id=str(org_id),
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "stripe_portal_create_failed",
                    "message": "Stripe portal failed to start",
                    "details": {"stripe_error": safe_error},
                },
            ) from exc
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

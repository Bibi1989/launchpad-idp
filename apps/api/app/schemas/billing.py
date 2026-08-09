"""Billing / Stripe schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.domain import OrgPlan
from app.services.plans import PRO_MONTHLY_EUR


class OrgPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    org_id: UUID
    plan: OrgPlan
    max_projects: int
    max_workspaces: int
    project_count: int
    workspace_count: int
    pro_price_eur: int = PRO_MONTHLY_EUR
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    plan_updated_at: datetime | None = None


class CheckoutSessionRead(BaseModel):
    checkout_url: str


class PortalSessionRead(BaseModel):
    portal_url: str

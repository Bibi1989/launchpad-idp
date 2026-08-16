"""Schemas for lifecycle stage promotion (preview → staging → production)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.domain import LifecycleStage, PromotionRequestStatus


class StagePromoteTarget(str, Enum):
    STAGING = "staging"
    PRODUCTION = "production"


class StagePromoteRequest(BaseModel):
    """Request promote from an environment into staging or production."""

    target_stage: StagePromoteTarget
    name: str | None = Field(default=None, min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    ttl_hours: int | None = Field(
        default=None,
        ge=1,
        le=720,
        description="TTL for staging only. Ignored for production (no TTL).",
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()


class PromotionReviewRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class PromotionRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    source_environment_id: UUID
    target_environment_id: UUID | None = None
    target_stage: str
    status: str
    requested_by: UUID
    reviewed_by: UUID | None = None
    review_note: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None
    completed_at: datetime | None = None
    # Enriched
    source_environment_name: str | None = None
    target_environment_name: str | None = None
    requires_approval: bool = False
    executed: bool = False


class StagePromoteResponse(BaseModel):
    """Either a pending approval request or the newly launched environment id."""

    promotion: PromotionRequestRead
    environment_id: UUID | None = None
    environment: dict | None = None


class OrgPromotionPolicyRead(BaseModel):
    staging_requires_approval: bool
    production_requires_approval: bool


class OrgPromotionPolicyUpdate(BaseModel):
    staging_requires_approval: bool | None = None
    production_requires_approval: bool | None = None


# Re-export for routers / tests
__all__ = [
    "LifecycleStage",
    "PromotionRequestStatus",
    "StagePromoteTarget",
    "StagePromoteRequest",
    "PromotionReviewRequest",
    "PromotionRequestRead",
    "StagePromoteResponse",
    "OrgPromotionPolicyRead",
    "OrgPromotionPolicyUpdate",
]

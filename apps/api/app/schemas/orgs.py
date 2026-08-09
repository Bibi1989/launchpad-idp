from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.domain import OrgPlan, OrgRole


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    slug: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("name must be at least 2 characters")
        return cleaned

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        return cleaned or None


class OrganizationUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("name must be at least 2 characters")
        return cleaned


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    role: OrgRole
    plan: OrgPlan = OrgPlan.FREE
    created_at: datetime | None = None


class OrgMemberAdd(BaseModel):
    email: EmailStr
    role: OrgRole = OrgRole.MEMBER


class OrgMemberUpdate(BaseModel):
    role: OrgRole


class OrgMemberRead(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    role: OrgRole
    org_id: UUID | None = None
    org_name: str | None = None


class OrgInviteCreate(BaseModel):
    email: EmailStr
    role: OrgRole = OrgRole.MEMBER


class OrgInviteRead(BaseModel):
    id: UUID
    org_id: UUID
    org_name: str | None = None
    email: str
    role: OrgRole
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    invite_url: str | None = None
    email_sent: bool = False
    email_error: str | None = None


class OrgInviteAccept(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class OrgSsoMappingCreate(BaseModel):
    group_name: str = Field(min_length=1, max_length=256)
    role: OrgRole = OrgRole.MEMBER

    @field_validator("group_name")
    @classmethod
    def normalize_group(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("group_name is required")
        return cleaned


class OrgSsoMappingRead(BaseModel):
    id: UUID
    org_id: UUID
    group_name: str
    role: OrgRole
    created_at: datetime


class OrgCostEnvironmentItem(BaseModel):
    environment_id: UUID
    name: str
    status: str
    provider: str | None = None
    is_local: bool
    cost_estimate_hourly: Decimal
    cost_accrued: Decimal


class OrgCostSummary(BaseModel):
    org_id: UUID
    soft_cost_cap: Decimal
    active_count: int
    cloud_environment_count: int
    cloud_accrued: Decimal
    local_accrued: Decimal
    total_accrued: Decimal
    soft_cost_cap_exceeded: bool
    environments: list[OrgCostEnvironmentItem]

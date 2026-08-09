"""Pydantic schemas for projects and project invites."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.domain import OrgRole


class ProjectCreate(BaseModel):
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
        cleaned = value.strip().lower().replace(" ", "-")
        return cleaned or None


class ProjectUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("name must be at least 2 characters")
        return cleaned


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    slug: str
    role: OrgRole | None = None
    workspace_count: int = 0
    created_at: datetime
    updated_at: datetime | None = None


class ProjectMemberRead(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    role: OrgRole


class ProjectMemberUpdate(BaseModel):
    role: OrgRole


class ProjectInviteCreate(BaseModel):
    email: EmailStr
    role: OrgRole = OrgRole.MEMBER


class ProjectInviteRead(BaseModel):
    id: UUID
    project_id: UUID
    project_name: str | None = None
    org_id: UUID | None = None
    email: str
    role: OrgRole
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    invite_url: str | None = None
    email_sent: bool = False
    email_error: str | None = None


class ProjectInviteAccept(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class ProjectInviteAcceptRead(BaseModel):
    project_id: UUID
    project_name: str
    org_id: UUID
    org_name: str
    role: OrgRole

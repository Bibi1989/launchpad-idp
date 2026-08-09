"""Pending invite inbox schemas (org + project)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.domain import OrgRole


class PendingInviteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: Literal["org", "project"]
    invite_id: UUID
    role: OrgRole
    org_id: UUID
    org_name: str
    project_id: UUID | None = None
    project_name: str | None = None
    invited_by: str | None = None
    expires_at: datetime
    created_at: datetime
    href: str

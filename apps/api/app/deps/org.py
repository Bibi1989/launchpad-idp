from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.models.domain import User
from app.services.orgs import OrgContext, OrganizationService


async def get_org_context(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    x_org_id: Annotated[str | None, Header(alias="X-Org-ID")] = None,
) -> OrgContext:
    service = OrganizationService(session)
    org_uuid: UUID | None = None
    if x_org_id:
        try:
            org_uuid = UUID(x_org_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_org_id", "message": "X-Org-ID must be a UUID"},
            ) from exc
    ctx = await service.resolve_context(user=user, org_id=org_uuid)
    await session.commit()
    return ctx


CurrentOrg = Annotated[OrgContext, Depends(get_org_context)]

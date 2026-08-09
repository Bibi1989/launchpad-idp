"""Authenticated invite inbox: list + accept by invite id."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.schemas.invites import PendingInviteRead
from app.schemas.orgs import OrgMemberRead
from app.schemas.projects import ProjectInviteAcceptRead
from app.services.orgs import OrganizationService
from app.services.projects import ProjectService

router = APIRouter(prefix="/invites", tags=["invites"])


def get_org_service(session: AsyncSession = Depends(get_db_session)) -> OrganizationService:
    return OrganizationService(session)


def get_project_service(
    session: AsyncSession = Depends(get_db_session),
) -> ProjectService:
    return ProjectService(session)


@router.get("/pending", response_model=list[PendingInviteRead])
async def list_pending_invites(
    user: CurrentUser,
    orgs: OrganizationService = Depends(get_org_service),
    projects: ProjectService = Depends(get_project_service),
) -> list[PendingInviteRead]:
    rows: list[PendingInviteRead] = []
    for invite in await orgs.list_pending_for_user(user):
        org = invite.organization
        invited_by = invite.invited_by
        rows.append(
            PendingInviteRead(
                kind="org",
                invite_id=invite.id,
                role=invite.role,
                org_id=invite.org_id,
                org_name=org.name if org else "Organization",
                invited_by=(invited_by.display_name or invited_by.email)
                if invited_by
                else None,
                expires_at=invite.expires_at,
                created_at=invite.created_at,
                href=f"/invite/accept/org/{invite.id}",
            )
        )
    for invite in await projects.list_pending_for_user(user):
        project = invite.project
        if project is None:
            continue
        org = project.organization
        invited_by = invite.invited_by
        rows.append(
            PendingInviteRead(
                kind="project",
                invite_id=invite.id,
                role=invite.role,
                org_id=project.org_id,
                org_name=org.name if org else "Organization",
                project_id=invite.project_id,
                project_name=project.name,
                invited_by=(invited_by.display_name or invited_by.email)
                if invited_by
                else None,
                expires_at=invite.expires_at,
                created_at=invite.created_at,
                href=f"/invite/accept/project/{invite.id}",
            )
        )
    rows.sort(key=lambda row: row.created_at, reverse=True)
    return rows


@router.post("/org/{invite_id}/accept", response_model=OrgMemberRead)
async def accept_org_invite_by_id(
    invite_id: UUID,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrgMemberRead:
    membership = await service.accept_invite_by_id(user=user, invite_id=invite_id)
    await session.commit()
    await session.refresh(membership, attribute_names=["user", "organization"])
    return OrgMemberRead(
        user_id=membership.user_id,
        email=membership.user.email,
        display_name=membership.user.display_name,
        role=membership.role,
        org_id=membership.org_id,
        org_name=membership.organization.name if membership.organization else None,
    )


@router.post("/project/{invite_id}/accept", response_model=ProjectInviteAcceptRead)
async def accept_project_invite_by_id(
    invite_id: UUID,
    user: CurrentUser,
    service: ProjectService = Depends(get_project_service),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectInviteAcceptRead:
    project, role = await service.accept_invite_by_id(user=user, invite_id=invite_id)
    await session.commit()
    org = project.organization
    return ProjectInviteAcceptRead(
        project_id=project.id,
        project_name=project.name,
        org_id=project.org_id,
        org_name=org.name if org else "Organization",
        role=role,
    )

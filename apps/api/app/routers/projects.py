"""Project CRUD and invite endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.deps.org import CurrentOrg
from app.models.domain import OrgRole, ProvisioningWorkspace
from app.schemas.projects import (
    ProjectCreate,
    ProjectInviteAccept,
    ProjectInviteAcceptRead,
    ProjectInviteCreate,
    ProjectInviteRead,
    ProjectMemberRead,
    ProjectMemberUpdate,
    ProjectRead,
    ProjectUpdate,
)
from app.services.orgs import OrganizationService
from app.services.projects import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


def get_project_service(
    session: AsyncSession = Depends(get_db_session),
) -> ProjectService:
    return ProjectService(session)


def get_org_service(
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationService:
    return OrganizationService(session)


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    user: CurrentUser,
    org: CurrentOrg,
    service: ProjectService = Depends(get_project_service),
    orgs: OrganizationService = Depends(get_org_service),
) -> list[ProjectRead]:
    ctx = await orgs.resolve_context(user=user, org_id=org.org_id)
    rows = await service.list_for_org(org=ctx)
    return [
        ProjectRead(
            id=project.id,
            org_id=project.org_id,
            name=project.name,
            slug=project.slug,
            role=role,
            workspace_count=count,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
        for project, role, count in rows
    ]


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    user: CurrentUser,
    org: CurrentOrg,
    service: ProjectService = Depends(get_project_service),
    orgs: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectRead:
    ctx = await orgs.resolve_context(user=user, org_id=org.org_id)
    project = await service.create_project(org=ctx, name=payload.name, slug=payload.slug)
    await session.commit()
    await session.refresh(project)
    return ProjectRead(
        id=project.id,
        org_id=project.org_id,
        name=project.name,
        slug=project.slug,
        role=OrgRole.OWNER,
        workspace_count=0,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.post("/invites/accept", response_model=ProjectInviteAcceptRead)
async def accept_project_invite(
    payload: ProjectInviteAccept,
    user: CurrentUser,
    service: ProjectService = Depends(get_project_service),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectInviteAcceptRead:
    project, role = await service.accept_invite(user=user, token=payload.token)
    await session.commit()
    org = project.organization
    return ProjectInviteAcceptRead(
        project_id=project.id,
        project_name=project.name,
        org_id=project.org_id,
        org_name=org.name if org else "Organization",
        role=role,
    )


@router.patch("/{project_id}", response_model=ProjectRead)
async def rename_project(
    project_id: UUID,
    payload: ProjectUpdate,
    user: CurrentUser,
    service: ProjectService = Depends(get_project_service),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectRead:
    project = await service.rename_project(
        project_id=project_id, actor=user, name=payload.name
    )
    await session.commit()
    await session.refresh(project)
    count_result = await session.execute(
        select(func.count())
        .select_from(ProvisioningWorkspace)
        .where(ProvisioningWorkspace.project_id == project.id)
    )
    membership = await service.get_membership(project_id=project.id, user_id=user.id)
    return ProjectRead(
        id=project.id,
        org_id=project.org_id,
        name=project.name,
        slug=project.slug,
        role=membership.role if membership else OrgRole.ADMIN,
        workspace_count=int(count_result.scalar_one()),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: UUID,
    user: CurrentUser,
    service: ProjectService = Depends(get_project_service),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectRead:
    project, role = await service.require_project_access(
        user=user, project_id=project_id, minimum=OrgRole.VIEWER
    )
    count_result = await session.execute(
        select(func.count())
        .select_from(ProvisioningWorkspace)
        .where(ProvisioningWorkspace.project_id == project.id)
    )
    return ProjectRead(
        id=project.id,
        org_id=project.org_id,
        name=project.name,
        slug=project.slug,
        role=role,
        workspace_count=int(count_result.scalar_one()),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("/{project_id}/members", response_model=list[ProjectMemberRead])
async def list_members(
    project_id: UUID,
    user: CurrentUser,
    service: ProjectService = Depends(get_project_service),
) -> list[ProjectMemberRead]:
    await service.require_project_access(
        user=user, project_id=project_id, minimum=OrgRole.VIEWER
    )
    members = await service.list_members(project_id=project_id)
    return [
        ProjectMemberRead(
            user_id=m.user_id,
            email=m.user.email,
            display_name=m.user.display_name,
            role=m.role,
        )
        for m in members
    ]


@router.patch(
    "/{project_id}/members/{member_user_id}",
    response_model=ProjectMemberRead,
)
async def update_member(
    project_id: UUID,
    member_user_id: UUID,
    payload: ProjectMemberUpdate,
    user: CurrentUser,
    service: ProjectService = Depends(get_project_service),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectMemberRead:
    membership = await service.update_member_role(
        project_id=project_id,
        actor=user,
        user_id=member_user_id,
        role=payload.role,
    )
    await session.commit()
    return ProjectMemberRead(
        user_id=membership.user_id,
        email=membership.user.email,
        display_name=membership.user.display_name,
        role=membership.role,
    )


@router.post(
    "/{project_id}/invites",
    response_model=ProjectInviteRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite(
    project_id: UUID,
    payload: ProjectInviteCreate,
    user: CurrentUser,
    service: ProjectService = Depends(get_project_service),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectInviteRead:
    created = await service.create_invite(
        project_id=project_id,
        actor=user,
        email=str(payload.email),
        role=payload.role,
    )
    await session.commit()
    invite = created.invite
    project = await service.get_project(project_id)
    return ProjectInviteRead(
        id=invite.id,
        project_id=invite.project_id,
        project_name=project.name if project else None,
        org_id=project.org_id if project else None,
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at,
        accepted_at=invite.accepted_at,
        revoked_at=invite.revoked_at,
        created_at=invite.created_at,
        invite_url=created.invite_url,
        email_sent=created.email_sent,
        email_error=created.email_error,
    )


@router.get("/{project_id}/invites", response_model=list[ProjectInviteRead])
async def list_invites(
    project_id: UUID,
    user: CurrentUser,
    service: ProjectService = Depends(get_project_service),
) -> list[ProjectInviteRead]:
    await service.require_project_access(
        user=user, project_id=project_id, minimum=OrgRole.ADMIN
    )
    project = await service.get_project(project_id)
    invites = await service.list_invites(project_id=project_id)
    return [
        ProjectInviteRead(
            id=inv.id,
            project_id=inv.project_id,
            project_name=project.name if project else None,
            org_id=project.org_id if project else None,
            email=inv.email,
            role=inv.role,
            expires_at=inv.expires_at,
            accepted_at=inv.accepted_at,
            revoked_at=inv.revoked_at,
            created_at=inv.created_at,
        )
        for inv in invites
    ]


@router.delete(
    "/{project_id}/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invite(
    project_id: UUID,
    invite_id: UUID,
    user: CurrentUser,
    service: ProjectService = Depends(get_project_service),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    await service.revoke_invite(project_id=project_id, invite_id=invite_id, actor=user)
    await session.commit()

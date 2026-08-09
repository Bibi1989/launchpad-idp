from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.models.domain import OrgRole
from app.schemas.orgs import (
    OrganizationCreate,
    OrganizationRead,
    OrganizationUpdate,
    OrgCostSummary,
    OrgInviteAccept,
    OrgInviteCreate,
    OrgInviteRead,
    OrgMemberAdd,
    OrgMemberRead,
    OrgMemberUpdate,
    OrgSsoMappingCreate,
    OrgSsoMappingRead,
)
from app.services.environment import EnvironmentService
from app.services.orgs import OrganizationService

router = APIRouter(prefix="/orgs", tags=["organizations"])


def get_org_service(session: AsyncSession = Depends(get_db_session)) -> OrganizationService:
    return OrganizationService(session)


def get_environment_service(
    session: AsyncSession = Depends(get_db_session),
) -> EnvironmentService:
    return EnvironmentService(session)


def _invite_read(
    *,
    invite,
    org_name: str | None = None,
    invite_url: str | None = None,
    email_sent: bool = False,
    email_error: str | None = None,
) -> OrgInviteRead:
    return OrgInviteRead(
        id=invite.id,
        org_id=invite.org_id,
        org_name=org_name,
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at,
        accepted_at=invite.accepted_at,
        revoked_at=invite.revoked_at,
        created_at=invite.created_at,
        invite_url=invite_url,
        email_sent=email_sent,
        email_error=email_error,
    )


@router.get("", response_model=list[OrganizationRead])
async def list_orgs(
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> list[OrganizationRead]:
    rows = await service.list_for_user(user)
    await session.commit()
    return [
        OrganizationRead(
            id=org.id,
            slug=org.slug,
            name=org.name,
            role=role,
            plan=getattr(org, "plan", None) or "free",
            created_at=org.created_at,
        )
        for org, role in rows
    ]


@router.post("", response_model=OrganizationRead, status_code=status.HTTP_201_CREATED)
async def create_org(
    payload: OrganizationCreate,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationRead:
    org = await service.create_org(user=user, name=payload.name, slug=payload.slug)
    await session.commit()
    return OrganizationRead(
        id=org.id,
        slug=org.slug,
        name=org.name,
        role=OrgRole.OWNER,
        plan=getattr(org, "plan", None) or "free",
        created_at=org.created_at,
    )


@router.post("/invites/accept", response_model=OrgMemberRead)
async def accept_invite(
    payload: OrgInviteAccept,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrgMemberRead:
    membership = await service.accept_invite(user=user, token=payload.token)
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


@router.get("/{org_id}/members", response_model=list[OrgMemberRead])
async def list_members(
    org_id: UUID,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> list[OrgMemberRead]:
    ctx = await service.resolve_context(user=user, org_id=org_id)
    members = await service.list_members(org_id=ctx.org_id)
    await session.commit()
    return [
        OrgMemberRead(
            user_id=m.user_id,
            email=m.user.email,
            display_name=m.user.display_name,
            role=m.role,
        )
        for m in members
    ]


@router.get("/{org_id}/costs", response_model=OrgCostSummary)
async def get_org_costs(
    org_id: UUID,
    user: CurrentUser,
    env_service: EnvironmentService = Depends(get_environment_service),
) -> OrgCostSummary:
    return await env_service.org_cost_summary(org_id, user)


@router.post(
    "/{org_id}/members",
    response_model=OrgMemberRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    org_id: UUID,
    payload: OrgMemberAdd,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrgMemberRead:
    ctx = await service.resolve_context(user=user, org_id=org_id)
    membership = await service.add_member(
        org_id=ctx.org_id,
        actor=ctx,
        email=str(payload.email),
        role=payload.role,
    )
    await session.commit()
    await session.refresh(membership, attribute_names=["user"])
    return OrgMemberRead(
        user_id=membership.user_id,
        email=membership.user.email,
        display_name=membership.user.display_name,
        role=membership.role,
    )


@router.patch("/{org_id}/members/{member_user_id}", response_model=OrgMemberRead)
async def update_member(
    org_id: UUID,
    member_user_id: UUID,
    payload: OrgMemberUpdate,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrgMemberRead:
    ctx = await service.resolve_context(user=user, org_id=org_id)
    membership = await service.update_member_role(
        org_id=ctx.org_id,
        actor=ctx,
        user_id=member_user_id,
        role=payload.role,
    )
    await session.commit()
    await session.refresh(membership, attribute_names=["user"])
    return OrgMemberRead(
        user_id=membership.user_id,
        email=membership.user.email,
        display_name=membership.user.display_name,
        role=membership.role,
    )


@router.delete("/{org_id}/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    org_id: UUID,
    member_user_id: UUID,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    ctx = await service.resolve_context(user=user, org_id=org_id)
    await service.remove_member(org_id=ctx.org_id, actor=ctx, user_id=member_user_id)
    await session.commit()


@router.get("/{org_id}/invites", response_model=list[OrgInviteRead])
async def list_invites(
    org_id: UUID,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> list[OrgInviteRead]:
    ctx = await service.resolve_context(user=user, org_id=org_id)
    invites = await service.list_invites(org_id=ctx.org_id)
    await session.commit()
    return [
        _invite_read(invite=invite, org_name=ctx.organization.name)
        for invite in invites
    ]


@router.post(
    "/{org_id}/invites",
    response_model=OrgInviteRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite(
    org_id: UUID,
    payload: OrgInviteCreate,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrgInviteRead:
    ctx = await service.resolve_context(user=user, org_id=org_id)
    created = await service.create_invite(
        org_id=ctx.org_id,
        actor=ctx,
        email=str(payload.email),
        role=payload.role,
    )
    await session.commit()
    return _invite_read(
        invite=created.invite,
        org_name=ctx.organization.name,
        invite_url=created.invite_url,
        email_sent=created.email_sent,
        email_error=created.email_error,
    )


@router.patch("/{org_id}", response_model=OrganizationRead)
async def rename_org(
    org_id: UUID,
    payload: OrganizationUpdate,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrganizationRead:
    ctx = await service.resolve_context(user=user, org_id=org_id)
    org = await service.rename_org(org_id=org_id, actor=ctx, name=payload.name)
    await session.commit()
    return OrganizationRead(
        id=org.id,
        slug=org.slug,
        name=org.name,
        role=ctx.role,
        plan=getattr(org, "plan", None) or "free",
        created_at=org.created_at,
    )


@router.delete("/{org_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    org_id: UUID,
    invite_id: UUID,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    ctx = await service.resolve_context(user=user, org_id=org_id)
    await service.revoke_invite(org_id=ctx.org_id, actor=ctx, invite_id=invite_id)
    await session.commit()


@router.get("/{org_id}/sso-mappings", response_model=list[OrgSsoMappingRead])
async def list_sso_mappings(
    org_id: UUID,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> list[OrgSsoMappingRead]:
    ctx = await service.resolve_context(user=user, org_id=org_id)
    rows = await service.list_sso_mappings(org_id=ctx.org_id)
    await session.commit()
    return [
        OrgSsoMappingRead(
            id=row.id,
            org_id=row.org_id,
            group_name=row.group_name,
            role=row.role,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post(
    "/{org_id}/sso-mappings",
    response_model=OrgSsoMappingRead,
    status_code=status.HTTP_201_CREATED,
)
async def upsert_sso_mapping(
    org_id: UUID,
    payload: OrgSsoMappingCreate,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> OrgSsoMappingRead:
    ctx = await service.resolve_context(user=user, org_id=org_id)
    row = await service.upsert_sso_mapping(
        org_id=ctx.org_id,
        actor=ctx,
        group_name=payload.group_name,
        role=payload.role,
    )
    await session.commit()
    return OrgSsoMappingRead(
        id=row.id,
        org_id=row.org_id,
        group_name=row.group_name,
        role=row.role,
        created_at=row.created_at,
    )


@router.delete("/{org_id}/sso-mappings/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sso_mapping(
    org_id: UUID,
    mapping_id: UUID,
    user: CurrentUser,
    service: OrganizationService = Depends(get_org_service),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    ctx = await service.resolve_context(user=user, org_id=org_id)
    await service.delete_sso_mapping(org_id=ctx.org_id, actor=ctx, mapping_id=mapping_id)
    await session.commit()

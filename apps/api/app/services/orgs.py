"""Organization RBAC helpers and service."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.domain import (
    OrgInvite,
    OrgMembership,
    OrgRole,
    OrgSsoRoleMapping,
    Organization,
    User,
)
from app.services.email import EmailService

logger = get_logger(__name__)

ROLE_RANK: dict[OrgRole, int] = {
    OrgRole.VIEWER: 0,
    OrgRole.MEMBER: 1,
    OrgRole.ADMIN: 2,
    OrgRole.OWNER: 3,
}


@dataclass(frozen=True, slots=True)
class OrgContext:
    organization: Organization
    membership: OrgMembership
    user: User

    @property
    def org_id(self) -> UUID:
        return self.organization.id

    @property
    def role(self) -> OrgRole:
        return self.membership.role


@dataclass(frozen=True, slots=True)
class CreatedInvite:
    invite: OrgInvite
    raw_token: str
    invite_url: str
    email_sent: bool


def role_at_least(role: OrgRole, minimum: OrgRole) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


def highest_role(roles: list[OrgRole]) -> OrgRole | None:
    if not roles:
        return None
    return max(roles, key=lambda role: ROLE_RANK[role])


def slugify_org(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower()).strip("-")
    return (cleaned or "org")[:48]


def hash_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class OrganizationService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._email = EmailService(self._settings)

    async def ensure_personal_org(self, user: User) -> Organization:
        existing = await self._session.execute(
            select(OrgMembership)
            .where(OrgMembership.user_id == user.id)
            .options(selectinload(OrgMembership.organization))
            .limit(1)
        )
        membership = existing.scalar_one_or_none()
        if membership is not None:
            return membership.organization

        base = slugify_org(user.email.split("@", 1)[0])
        slug = await self._unique_slug(base)
        org = Organization(slug=slug, name=f"{user.display_name}'s org")
        self._session.add(org)
        await self._session.flush()
        membership_row = OrgMembership(
            org_id=org.id,
            user_id=user.id,
            role=OrgRole.OWNER,
        )
        self._session.add(membership_row)
        await self._session.flush()
        await self._session.refresh(org)
        logger.info("personal_org_created", org_id=str(org.id), user_id=str(user.id), slug=slug)
        return org

    async def list_for_user(self, user: User) -> list[tuple[Organization, OrgRole]]:
        await self.ensure_personal_org(user)
        result = await self._session.execute(
            select(OrgMembership)
            .where(OrgMembership.user_id == user.id)
            .options(selectinload(OrgMembership.organization))
            .order_by(OrgMembership.created_at.asc())
        )
        rows = list(result.scalars().all())
        return [(row.organization, row.role) for row in rows]

    async def create_org(self, *, user: User, name: str, slug: str | None = None) -> Organization:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_name", "message": "Organization name is required"},
            )
        base = slugify_org(slug or cleaned_name)
        unique = await self._unique_slug(base)
        org = Organization(slug=unique, name=cleaned_name)
        self._session.add(org)
        await self._session.flush()
        self._session.add(
            OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER)
        )
        await self._session.flush()
        await self._session.refresh(org)
        return org

    async def get_membership(self, *, org_id: UUID, user_id: UUID) -> OrgMembership | None:
        result = await self._session.execute(
            select(OrgMembership)
            .where(OrgMembership.org_id == org_id, OrgMembership.user_id == user_id)
            .options(selectinload(OrgMembership.organization))
        )
        return result.scalar_one_or_none()

    async def resolve_context(
        self,
        *,
        user: User,
        org_id: UUID | None,
    ) -> OrgContext:
        await self.ensure_personal_org(user)
        if org_id is None:
            rows = await self.list_for_user(user)
            if not rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "org_not_found", "message": "No organization found"},
                )
            organization, _role = rows[0]
            membership = await self.get_membership(org_id=organization.id, user_id=user.id)
            assert membership is not None
            return OrgContext(organization=organization, membership=membership, user=user)

        membership = await self.get_membership(org_id=org_id, user_id=user.id)
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "org_not_found", "message": "Organization not found"},
            )
        return OrgContext(
            organization=membership.organization,
            membership=membership,
            user=user,
        )

    async def list_members(self, *, org_id: UUID) -> list[OrgMembership]:
        result = await self._session.execute(
            select(OrgMembership)
            .where(OrgMembership.org_id == org_id)
            .options(selectinload(OrgMembership.user))
            .order_by(OrgMembership.created_at.asc())
        )
        return list(result.scalars().all())

    async def add_member(
        self,
        *,
        org_id: UUID,
        actor: OrgContext,
        email: str,
        role: OrgRole,
    ) -> OrgMembership:
        if not role_at_least(actor.role, OrgRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Admin role required to add members"},
            )
        if role == OrgRole.OWNER and actor.role != OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Only owners can assign the owner role"},
            )
        from app.repositories.user import UserRepository

        user = await UserRepository(self._session).get_by_email(email)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "user_not_found",
                    "message": "No user with that email - create an invite instead",
                },
            )
        existing = await self.get_membership(org_id=org_id, user_id=user.id)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "already_member", "message": "User is already a member"},
            )
        membership = OrgMembership(org_id=org_id, user_id=user.id, role=role)
        self._session.add(membership)
        await self._session.flush()
        await self._session.refresh(membership)
        return membership

    async def update_member_role(
        self,
        *,
        org_id: UUID,
        actor: OrgContext,
        user_id: UUID,
        role: OrgRole,
    ) -> OrgMembership:
        if not role_at_least(actor.role, OrgRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Admin role required"},
            )
        if role == OrgRole.OWNER and actor.role != OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Only owners can assign the owner role"},
            )
        membership = await self.get_membership(org_id=org_id, user_id=user_id)
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "member_not_found", "message": "Member not found"},
            )
        if membership.role == OrgRole.OWNER and actor.role != OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Cannot change an owner membership"},
            )
        membership.role = role
        await self._session.flush()
        await self._session.refresh(membership)
        return membership

    async def remove_member(
        self,
        *,
        org_id: UUID,
        actor: OrgContext,
        user_id: UUID,
    ) -> None:
        if not role_at_least(actor.role, OrgRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Admin role required"},
            )
        membership = await self.get_membership(org_id=org_id, user_id=user_id)
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "member_not_found", "message": "Member not found"},
            )
        if membership.role == OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Cannot remove an organization owner"},
            )
        await self._session.delete(membership)
        await self._session.flush()

    async def create_invite(
        self,
        *,
        org_id: UUID,
        actor: OrgContext,
        email: str,
        role: OrgRole,
    ) -> CreatedInvite:
        if not role_at_least(actor.role, OrgRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Admin role required to invite members"},
            )
        if role == OrgRole.OWNER and actor.role != OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Only owners can invite as owner"},
            )

        cleaned_email = email.strip().lower()
        from app.repositories.user import UserRepository

        existing_user = await UserRepository(self._session).get_by_email(cleaned_email)
        if existing_user is not None:
            membership = await self.get_membership(org_id=org_id, user_id=existing_user.id)
            if membership is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "already_member", "message": "User is already a member"},
                )

        pending = await self._pending_invite_for_email(org_id=org_id, email=cleaned_email)
        if pending is not None:
            pending.revoked_at = datetime.now(UTC)

        raw_token = secrets.token_urlsafe(32)
        invite = OrgInvite(
            org_id=org_id,
            email=cleaned_email,
            role=role,
            token_hash=hash_invite_token(raw_token),
            invited_by_user_id=actor.user.id,
            expires_at=datetime.now(UTC)
            + timedelta(hours=self._settings.invite_ttl_hours),
        )
        self._session.add(invite)
        await self._session.flush()
        await self._session.refresh(invite)

        invite_url = f"{self._settings.invite_base_url.rstrip('/')}/{raw_token}"
        email_sent = self._email.send_org_invite(
            to_email=cleaned_email,
            org_name=actor.organization.name,
            role=role.value,
            invite_url=invite_url,
            invited_by=actor.user.display_name or actor.user.email,
        )
        logger.info(
            "org_invite_created",
            invite_id=str(invite.id),
            org_id=str(org_id),
            email=cleaned_email,
            email_sent=email_sent,
        )
        return CreatedInvite(
            invite=invite,
            raw_token=raw_token,
            invite_url=invite_url,
            email_sent=email_sent,
        )

    async def list_invites(self, *, org_id: UUID) -> list[OrgInvite]:
        result = await self._session.execute(
            select(OrgInvite)
            .where(
                OrgInvite.org_id == org_id,
                OrgInvite.accepted_at.is_(None),
                OrgInvite.revoked_at.is_(None),
            )
            .order_by(OrgInvite.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke_invite(
        self,
        *,
        org_id: UUID,
        actor: OrgContext,
        invite_id: UUID,
    ) -> None:
        if not role_at_least(actor.role, OrgRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Admin role required"},
            )
        invite = await self._session.get(OrgInvite, invite_id)
        if invite is None or invite.org_id != org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "invite_not_found", "message": "Invite not found"},
            )
        invite.revoked_at = datetime.now(UTC)
        await self._session.flush()

    async def accept_invite(self, *, user: User, token: str) -> OrgMembership:
        invite = await self._invite_by_token(token)
        if invite.email.lower() != user.email.lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "invite_email_mismatch",
                    "message": "Sign in with the invited email address to accept",
                },
            )
        existing = await self.get_membership(org_id=invite.org_id, user_id=user.id)
        if existing is not None:
            invite.accepted_at = datetime.now(UTC)
            await self._session.flush()
            return existing

        membership = OrgMembership(org_id=invite.org_id, user_id=user.id, role=invite.role)
        self._session.add(membership)
        invite.accepted_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(membership)
        return membership

    async def accept_pending_invites_for_user(self, user: User) -> int:
        """Auto-accept outstanding invites matching the user's email."""
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(OrgInvite).where(
                OrgInvite.email == user.email.lower(),
                OrgInvite.accepted_at.is_(None),
                OrgInvite.revoked_at.is_(None),
                OrgInvite.expires_at > now,
            )
        )
        invites = list(result.scalars().all())
        accepted = 0
        for invite in invites:
            existing = await self.get_membership(org_id=invite.org_id, user_id=user.id)
            if existing is None:
                self._session.add(
                    OrgMembership(org_id=invite.org_id, user_id=user.id, role=invite.role)
                )
                accepted += 1
            invite.accepted_at = now
        if invites:
            await self._session.flush()
        return accepted

    async def list_sso_mappings(self, *, org_id: UUID) -> list[OrgSsoRoleMapping]:
        result = await self._session.execute(
            select(OrgSsoRoleMapping)
            .where(OrgSsoRoleMapping.org_id == org_id)
            .order_by(OrgSsoRoleMapping.group_name.asc())
        )
        return list(result.scalars().all())

    async def upsert_sso_mapping(
        self,
        *,
        org_id: UUID,
        actor: OrgContext,
        group_name: str,
        role: OrgRole,
    ) -> OrgSsoRoleMapping:
        if not role_at_least(actor.role, OrgRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Admin role required"},
            )
        if role == OrgRole.OWNER and actor.role != OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Only owners can map groups to owner"},
            )
        cleaned = group_name.strip()
        result = await self._session.execute(
            select(OrgSsoRoleMapping).where(
                OrgSsoRoleMapping.org_id == org_id,
                OrgSsoRoleMapping.group_name == cleaned,
            )
        )
        mapping = result.scalar_one_or_none()
        if mapping is None:
            mapping = OrgSsoRoleMapping(org_id=org_id, group_name=cleaned, role=role)
            self._session.add(mapping)
        else:
            mapping.role = role
        await self._session.flush()
        await self._session.refresh(mapping)
        return mapping

    async def delete_sso_mapping(
        self,
        *,
        org_id: UUID,
        actor: OrgContext,
        mapping_id: UUID,
    ) -> None:
        if not role_at_least(actor.role, OrgRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Admin role required"},
            )
        mapping = await self._session.get(OrgSsoRoleMapping, mapping_id)
        if mapping is None or mapping.org_id != org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "mapping_not_found", "message": "SSO mapping not found"},
            )
        await self._session.delete(mapping)
        await self._session.flush()

    async def sync_sso_group_memberships(
        self,
        *,
        user: User,
        groups: list[str],
    ) -> int:
        """Upsert org memberships from IdP groups + per-org / global maps."""
        normalized = {g.strip() for g in groups if g and g.strip()}
        changed = 0

        result = await self._session.execute(select(OrgSsoRoleMapping))
        mappings = list(result.scalars().all())
        by_org: dict[UUID, list[OrgRole]] = {}
        for mapping in mappings:
            if mapping.group_name in normalized:
                by_org.setdefault(mapping.org_id, []).append(mapping.role)

        for org_id, roles in by_org.items():
            target = highest_role(roles)
            if target is None:
                continue
            changed += await self._upsert_sso_membership(user=user, org_id=org_id, role=target)

        global_map = self._settings.oidc_group_role_map or {}
        default_slug = (self._settings.oidc_default_org_slug or "").strip()
        if global_map and default_slug:
            org_result = await self._session.execute(
                select(Organization).where(Organization.slug == default_slug)
            )
            org = org_result.scalar_one_or_none()
            if org is not None:
                mapped_roles: list[OrgRole] = []
                for group_name, role_value in global_map.items():
                    if group_name not in normalized:
                        continue
                    try:
                        mapped_roles.append(OrgRole(str(role_value).lower()))
                    except ValueError:
                        logger.warning(
                            "oidc_group_role_map_invalid",
                            group=group_name,
                            role=role_value,
                        )
                target = highest_role(mapped_roles)
                if target is not None:
                    changed += await self._upsert_sso_membership(
                        user=user,
                        org_id=org.id,
                        role=target,
                    )

        if changed:
            await self._session.flush()
        return changed

    async def _upsert_sso_membership(
        self,
        *,
        user: User,
        org_id: UUID,
        role: OrgRole,
    ) -> int:
        existing = await self.get_membership(org_id=org_id, user_id=user.id)
        if existing is None:
            self._session.add(OrgMembership(org_id=org_id, user_id=user.id, role=role))
            logger.info(
                "sso_membership_created",
                user_id=str(user.id),
                org_id=str(org_id),
                role=role.value,
            )
            return 1
        if existing.role == OrgRole.OWNER:
            return 0
        if ROLE_RANK[role] > ROLE_RANK[existing.role]:
            existing.role = role
            logger.info(
                "sso_membership_promoted",
                user_id=str(user.id),
                org_id=str(org_id),
                role=role.value,
            )
            return 1
        return 0

    def require_role(self, ctx: OrgContext, minimum: OrgRole) -> None:
        if not role_at_least(ctx.role, minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "forbidden",
                    "message": f"Requires {minimum.value} role or higher",
                },
            )

    def can_mutate_resource(self, ctx: OrgContext, *, resource_owner_id: UUID) -> bool:
        if role_at_least(ctx.role, OrgRole.ADMIN):
            return True
        if role_at_least(ctx.role, OrgRole.MEMBER) and resource_owner_id == ctx.user.id:
            return True
        return False

    async def _pending_invite_for_email(
        self,
        *,
        org_id: UUID,
        email: str,
    ) -> OrgInvite | None:
        result = await self._session.execute(
            select(OrgInvite).where(
                OrgInvite.org_id == org_id,
                OrgInvite.email == email,
                OrgInvite.accepted_at.is_(None),
                OrgInvite.revoked_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def _invite_by_token(self, token: str) -> OrgInvite:
        token_hash = hash_invite_token(token.strip())
        result = await self._session.execute(
            select(OrgInvite)
            .where(OrgInvite.token_hash == token_hash)
            .options(selectinload(OrgInvite.organization))
        )
        invite = result.scalar_one_or_none()
        if invite is None or invite.revoked_at is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "invite_not_found", "message": "Invite not found"},
            )
        if invite.accepted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "invite_already_accepted", "message": "Invite already accepted"},
            )
        expires = invite.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"code": "invite_expired", "message": "Invite has expired"},
            )
        return invite

    async def _unique_slug(self, base: str) -> str:
        candidate = base
        for _ in range(20):
            result = await self._session.execute(
                select(Organization.id).where(Organization.slug == candidate)
            )
            if result.scalar_one_or_none() is None:
                return candidate
            candidate = f"{base}-{secrets.token_hex(2)}"[:64]
        return f"{base}-{secrets.token_hex(4)}"[:64]

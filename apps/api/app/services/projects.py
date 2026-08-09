"""Projects under organizations: CRUD, memberships, invites, plan limits."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.domain import (
    OrgRole,
    Organization,
    Project,
    ProjectInvite,
    ProjectMembership,
    ProvisioningWorkspace,
    User,
)
from app.services.email import EmailService
from app.services.orgs import (
    OrganizationService,
    OrgContext,
    hash_invite_token,
    role_at_least,
    slugify_org,
)
from app.services.plans import assert_can_create_project

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CreatedProjectInvite:
    invite: ProjectInvite
    raw_token: str
    invite_url: str
    email_sent: bool
    email_error: str | None = None


def slugify_project(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower()).strip("-")
    return (cleaned or "project")[:48]


class ProjectService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._email = EmailService(self._settings)
        self._orgs = OrganizationService(session, self._settings)

    async def list_for_org(self, *, org: OrgContext) -> list[tuple[Project, OrgRole | None, int]]:
        """List projects the user belongs to (project membership required)."""
        result = await self._session.execute(
            select(Project)
            .where(Project.org_id == org.org_id)
            .order_by(Project.created_at.asc())
        )
        projects = list(result.scalars().all())
        out: list[tuple[Project, OrgRole | None, int]] = []
        for project in projects:
            membership = await self.get_membership(
                project_id=project.id, user_id=org.user.id
            )
            if membership is None:
                continue
            count_result = await self._session.execute(
                select(func.count())
                .select_from(ProvisioningWorkspace)
                .where(ProvisioningWorkspace.project_id == project.id)
            )
            out.append((project, membership.role, int(count_result.scalar_one())))
        return out

    async def get_project(self, project_id: UUID) -> Project | None:
        result = await self._session.execute(
            select(Project)
            .where(Project.id == project_id)
            .options(selectinload(Project.organization))
        )
        return result.scalar_one_or_none()

    async def get_membership(
        self, *, project_id: UUID, user_id: UUID
    ) -> ProjectMembership | None:
        result = await self._session.execute(
            select(ProjectMembership).where(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def require_project_access(
        self,
        *,
        user: User,
        project_id: UUID,
        minimum: OrgRole = OrgRole.VIEWER,
    ) -> tuple[Project, OrgRole]:
        project = await self.get_project(project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "project_not_found", "message": "Project not found"},
            )
        # Must belong to the org, then must have an explicit project membership.
        await self._orgs.resolve_context(user=user, org_id=project.org_id)
        membership = await self.get_membership(project_id=project_id, user_id=user.id)
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Not a project member"},
            )
        role = membership.role
        if not role_at_least(role, minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": f"{minimum.value} role required"},
            )
        return project, role

    async def ensure_default_project(
        self, *, org: Organization, actor: User
    ) -> Project:
        result = await self._session.execute(
            select(Project).where(Project.org_id == org.id, Project.slug == "default")
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
        return await self._create_project_row(
            org=org,
            name="Default",
            slug="default",
            actor=actor,
            skip_limit=True,
            # Only the actor is a member; others join via project invite.
            seed_org_admins=False,
        )

    async def create_project(
        self,
        *,
        org: OrgContext,
        name: str,
        slug: str | None,
    ) -> Project:
        if not role_at_least(org.role, OrgRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "forbidden",
                    "message": "Admin or owner role required to create projects",
                },
            )
        await assert_can_create_project(self._session, org.organization)
        base_slug = slugify_project(slug or name)
        unique_slug = await self._unique_slug(org.org_id, base_slug)
        return await self._create_project_row(
            org=org.organization,
            name=name,
            slug=unique_slug,
            actor=org.user,
            skip_limit=True,
            seed_org_admins=False,
        )

    async def _create_project_row(
        self,
        *,
        org: Organization,
        name: str,
        slug: str,
        actor: User,
        skip_limit: bool,
        seed_org_admins: bool,
    ) -> Project:
        if not skip_limit:
            await assert_can_create_project(self._session, org)
        project = Project(
            org_id=org.id,
            name=name,
            slug=slug,
            created_by_user_id=actor.id,
        )
        self._session.add(project)
        await self._session.flush()

        self._session.add(
            ProjectMembership(
                project_id=project.id,
                user_id=actor.id,
                role=OrgRole.OWNER,
            )
        )
        if seed_org_admins:
            from app.models.domain import OrgMembership

            members = await self._session.execute(
                select(OrgMembership).where(
                    OrgMembership.org_id == org.id,
                    OrgMembership.user_id != actor.id,
                )
            )
            for membership in members.scalars().all():
                self._session.add(
                    ProjectMembership(
                        project_id=project.id,
                        user_id=membership.user_id,
                        role=membership.role,
                    )
                )
        await self._session.flush()
        await self._session.refresh(project)
        logger.info(
            "project_created",
            project_id=str(project.id),
            org_id=str(org.id),
            slug=slug,
        )
        return project

    async def _unique_slug(self, org_id: UUID, base: str) -> str:
        candidate = base[:48] or "project"
        suffix = 0
        while True:
            check = candidate if suffix == 0 else f"{candidate[:40]}-{suffix}"
            result = await self._session.execute(
                select(Project.id).where(Project.org_id == org_id, Project.slug == check)
            )
            if result.scalar_one_or_none() is None:
                return check
            suffix += 1

    async def list_members(self, *, project_id: UUID) -> list[ProjectMembership]:
        result = await self._session.execute(
            select(ProjectMembership)
            .where(ProjectMembership.project_id == project_id)
            .options(selectinload(ProjectMembership.user))
            .order_by(ProjectMembership.created_at.asc())
        )
        return list(result.scalars().all())

    async def create_invite(
        self,
        *,
        project_id: UUID,
        actor: User,
        email: str,
        role: OrgRole,
    ) -> CreatedProjectInvite:
        project, actor_role = await self.require_project_access(
            user=actor, project_id=project_id, minimum=OrgRole.ADMIN
        )
        if role == OrgRole.OWNER and actor_role != OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Only owners can invite as owner"},
            )
        cleaned = email.strip().lower()
        from app.repositories.user import UserRepository

        existing_user = await UserRepository(self._session).get_by_email(cleaned)
        if existing_user is not None:
            membership = await self.get_membership(
                project_id=project_id, user_id=existing_user.id
            )
            if membership is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "already_member", "message": "User is already a member"},
                )

        pending = await self._pending_invite_for_email(project_id=project_id, email=cleaned)
        if pending is not None:
            pending.revoked_at = datetime.now(UTC)

        raw_token = secrets.token_urlsafe(32)
        invite = ProjectInvite(
            project_id=project_id,
            email=cleaned,
            role=role,
            token_hash=hash_invite_token(raw_token),
            invited_by_user_id=actor.id,
            expires_at=datetime.now(UTC)
            + timedelta(hours=self._settings.invite_ttl_hours),
        )
        self._session.add(invite)
        await self._session.flush()
        await self._session.refresh(invite)

        base = self._settings.invite_base_url.rstrip("/")
        # Prefer /invite/project/{token}; fall back to path under invite base.
        if base.endswith("/invite"):
            invite_url = f"{base}/project/{raw_token}"
        else:
            invite_url = f"{base}/invite/project/{raw_token}"

        org = project.organization
        if org is None:
            org_row = await self._session.get(Organization, project.org_id)
            org_name = org_row.name if org_row else "Organization"
        else:
            org_name = org.name

        email_sent, email_error = self._email.send_project_invite(
            to_email=cleaned,
            org_name=org_name,
            project_name=project.name,
            role=role.value,
            invite_url=invite_url,
            invited_by=actor.display_name or actor.email,
        )
        return CreatedProjectInvite(
            invite=invite,
            raw_token=raw_token,
            invite_url=invite_url,
            email_sent=email_sent,
            email_error=email_error,
        )

    async def rename_project(
        self,
        *,
        project_id: UUID,
        actor: User,
        name: str,
    ) -> Project:
        project, _role = await self.require_project_access(
            user=actor, project_id=project_id, minimum=OrgRole.ADMIN
        )
        project.name = name
        await self._session.flush()
        await self._session.refresh(project)
        return project

    async def _pending_invite_for_email(
        self, *, project_id: UUID, email: str
    ) -> ProjectInvite | None:
        result = await self._session.execute(
            select(ProjectInvite).where(
                ProjectInvite.project_id == project_id,
                ProjectInvite.email == email,
                ProjectInvite.accepted_at.is_(None),
                ProjectInvite.revoked_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_invites(self, *, project_id: UUID) -> list[ProjectInvite]:
        result = await self._session.execute(
            select(ProjectInvite)
            .where(
                ProjectInvite.project_id == project_id,
                ProjectInvite.revoked_at.is_(None),
            )
            .order_by(ProjectInvite.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke_invite(
        self, *, project_id: UUID, invite_id: UUID, actor: User
    ) -> None:
        await self.require_project_access(
            user=actor, project_id=project_id, minimum=OrgRole.ADMIN
        )
        invite = await self._session.get(ProjectInvite, invite_id)
        if invite is None or invite.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "invite_not_found", "message": "Invite not found"},
            )
        invite.revoked_at = datetime.now(UTC)
        await self._session.flush()

    async def list_pending_for_user(self, user: User) -> list[ProjectInvite]:
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(ProjectInvite)
            .where(
                ProjectInvite.email == user.email.lower(),
                ProjectInvite.accepted_at.is_(None),
                ProjectInvite.revoked_at.is_(None),
                ProjectInvite.expires_at > now,
            )
            .options(
                selectinload(ProjectInvite.project).selectinload(Project.organization),
                selectinload(ProjectInvite.invited_by),
            )
            .order_by(ProjectInvite.created_at.desc())
        )
        return list(result.scalars().all())

    async def accept_invite(self, *, user: User, token: str) -> tuple[Project, OrgRole]:
        token_hash = hash_invite_token(token.strip())
        result = await self._session.execute(
            select(ProjectInvite)
            .where(ProjectInvite.token_hash == token_hash)
            .options(
                selectinload(ProjectInvite.project).selectinload(Project.organization)
            )
        )
        invite = result.scalar_one_or_none()
        if invite is None or invite.revoked_at is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "invite_not_found", "message": "Invite not found"},
            )
        return await self._accept_project_invite(user=user, invite=invite)

    async def accept_invite_by_id(
        self, *, user: User, invite_id: UUID
    ) -> tuple[Project, OrgRole]:
        result = await self._session.execute(
            select(ProjectInvite)
            .where(ProjectInvite.id == invite_id)
            .options(
                selectinload(ProjectInvite.project).selectinload(Project.organization)
            )
        )
        invite = result.scalar_one_or_none()
        if invite is None or invite.revoked_at is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "invite_not_found", "message": "Invite not found"},
            )
        return await self._accept_project_invite(user=user, invite=invite)

    async def _accept_project_invite(
        self, *, user: User, invite: ProjectInvite
    ) -> tuple[Project, OrgRole]:
        if user.email.strip().lower() != invite.email.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "email_mismatch",
                    "message": "Signed-in email does not match the invite",
                },
            )

        project = invite.project
        existing = await self.get_membership(project_id=project.id, user_id=user.id)
        if existing is not None:
            if invite.accepted_at is None:
                invite.accepted_at = datetime.now(UTC)
                await self._session.flush()
            return project, existing.role

        if invite.accepted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "invite_used", "message": "Invite already accepted"},
            )
        expires = invite.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"code": "invite_expired", "message": "Invite expired"},
            )

        org_membership = await self._orgs.get_membership(
            org_id=project.org_id, user_id=user.id
        )
        if org_membership is None:
            from app.models.domain import OrgMembership

            self._session.add(
                OrgMembership(
                    org_id=project.org_id,
                    user_id=user.id,
                    role=OrgRole.MEMBER,
                )
            )

        self._session.add(
            ProjectMembership(
                project_id=project.id,
                user_id=user.id,
                role=invite.role,
            )
        )
        invite.accepted_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(project)
        return project, invite.role

    async def update_member_role(
        self,
        *,
        project_id: UUID,
        actor: User,
        user_id: UUID,
        role: OrgRole,
    ) -> ProjectMembership:
        _project, actor_role = await self.require_project_access(
            user=actor, project_id=project_id, minimum=OrgRole.ADMIN
        )
        if role == OrgRole.OWNER and actor_role != OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Only owners can assign owner"},
            )
        membership = await self.get_membership(project_id=project_id, user_id=user_id)
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "member_not_found", "message": "Member not found"},
            )
        if membership.role == OrgRole.OWNER and actor_role != OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Cannot change an owner membership"},
            )
        membership.role = role
        await self._session.flush()
        await self._session.refresh(membership, attribute_names=["user"])
        return membership

    async def resolve_project_for_workspace(
        self,
        *,
        org: OrgContext,
        project_id: UUID | None,
    ) -> Project:
        if project_id is not None:
            project, _role = await self.require_project_access(
                user=org.user, project_id=project_id, minimum=OrgRole.MEMBER
            )
            if project.org_id != org.org_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "project_org_mismatch",
                        "message": "Project does not belong to the active organization",
                    },
                )
            return project
        return await self.ensure_default_project(org=org.organization, actor=org.user)


# Keep slugify_org import used for consistency in callers that need org slugs.
_ = slugify_org

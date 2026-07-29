from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import (
    DEV_USER_DISPLAY_NAME,
    DEV_USER_EMAIL,
    DEV_USER_PASSWORD,
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.domain import User
from app.repositories.user import UserRepository
from app.schemas.auth import OrgSummary, TokenResponse, UserLogin, UserRead, UserRegister
from app.services.orgs import OrganizationService

logger = get_logger(__name__)


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._users = UserRepository(session)
        self._orgs = OrganizationService(session)

    async def register(self, payload: UserRegister) -> TokenResponse:
        existing = await self._users.get_by_email(payload.email)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "email_taken",
                    "message": "An account with this email already exists",
                },
            )

        user = await self._users.create(
            email=str(payload.email),
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
        )
        org = await self._orgs.ensure_personal_org(user)
        await self._orgs.accept_pending_invites_for_user(user)
        await self._session.commit()
        await self._session.refresh(user)
        logger.info("user_registered", user_id=str(user.id), email=user.email)
        return await self._token_response(user, org_id=org.id, org_role="owner")

    async def login(self, payload: UserLogin) -> TokenResponse:
        user = await self._users.get_by_email(str(payload.email))
        if (
            user is None
            or not user.password_hash
            or not verify_password(payload.password, user.password_hash)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "invalid_credentials", "message": "Invalid email or password"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        org = await self._orgs.ensure_personal_org(user)
        await self._orgs.accept_pending_invites_for_user(user)
        await self._session.commit()
        logger.info("user_login", user_id=str(user.id), email=user.email)
        membership = await self._orgs.get_membership(org_id=org.id, user_id=user.id)
        role = membership.role.value if membership else "owner"
        return await self._token_response(user, org_id=org.id, org_role=role)

    async def ensure_dev_user(self) -> User:
        user = await self._users.get_by_email(DEV_USER_EMAIL)
        if user is not None:
            await self._orgs.ensure_personal_org(user)
            await self._session.commit()
            return user
        user = await self._users.create(
            email=DEV_USER_EMAIL,
            password_hash=hash_password(DEV_USER_PASSWORD),
            display_name=DEV_USER_DISPLAY_NAME,
        )
        await self._orgs.ensure_personal_org(user)
        await self._session.commit()
        await self._session.refresh(user)
        logger.info("dev_user_created", user_id=str(user.id))
        return user

    async def dev_login(self) -> TokenResponse:
        if not self._settings.auth_dev_login_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "dev_login_disabled",
                    "message": "Dev login is disabled",
                },
            )
        user = await self.ensure_dev_user()
        org = await self._orgs.ensure_personal_org(user)
        await self._session.commit()
        logger.info("dev_login", user_id=str(user.id))
        return await self._token_response(user, org_id=org.id, org_role="owner")

    async def upsert_oidc_user(
        self,
        *,
        issuer: str,
        subject: str,
        email: str,
        display_name: str,
        groups: list[str] | None = None,
    ) -> TokenResponse:
        user = await self._users.get_by_oidc(issuer=issuer, subject=subject)
        if user is None:
            existing_email = await self._users.get_by_email(email)
            if existing_email is not None:
                user = existing_email
                user.oidc_issuer = issuer
                user.oidc_sub = subject
                if not user.display_name:
                    user.display_name = display_name
                await self._session.flush()
            else:
                user = await self._users.create(
                    email=email,
                    password_hash=None,
                    display_name=display_name or email.split("@", 1)[0],
                    oidc_issuer=issuer,
                    oidc_sub=subject,
                )
        org = await self._orgs.ensure_personal_org(user)
        await self._orgs.accept_pending_invites_for_user(user)
        await self._orgs.sync_sso_group_memberships(user=user, groups=list(groups or []))
        await self._session.commit()
        await self._session.refresh(user)
        membership = await self._orgs.get_membership(org_id=org.id, user_id=user.id)
        role = membership.role.value if membership else "owner"
        return await self._token_response(user, org_id=org.id, org_role=role)

    async def _token_response(
        self,
        user: User,
        *,
        org_id: UUID | None = None,
        org_role: str | None = None,
    ) -> TokenResponse:
        token = create_access_token(
            user_id=user.id,
            email=user.email,
            org_id=org_id,
            org_role=org_role,
            settings=self._settings,
        )
        orgs = await self._orgs.list_for_user(user)
        return TokenResponse(
            access_token=token,
            user=UserRead.model_validate(user),
            orgs=[
                OrgSummary(
                    id=str(org.id),
                    slug=org.slug,
                    name=org.name,
                    role=role.value,
                )
                for org, role in orgs
            ],
            active_org_id=str(org_id) if org_id else (str(orgs[0][0].id) if orgs else None),
        )

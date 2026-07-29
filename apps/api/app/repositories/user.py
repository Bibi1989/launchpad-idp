from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        normalized = email.strip().lower()
        result = await self._session.execute(select(User).where(User.email == normalized))
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        email: str,
        password_hash: str | None,
        display_name: str,
        oidc_issuer: str | None = None,
        oidc_sub: str | None = None,
    ) -> User:
        user = User(
            email=email.strip().lower(),
            password_hash=password_hash,
            display_name=display_name.strip(),
            oidc_issuer=oidc_issuer,
            oidc_sub=oidc_sub,
        )
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def get_by_oidc(self, *, issuer: str, subject: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.oidc_issuer == issuer, User.oidc_sub == subject)
        )
        return result.scalar_one_or_none()

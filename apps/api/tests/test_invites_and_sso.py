"""Org invite email flow and SSO group→role mapping."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db_session
from app.core.security import hash_password
from app.main import create_app
from app.models.domain import Base, OrgRole, User
from app.services.orgs import OrganizationService, highest_role
from app.services.oidc import OidcService


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def test_highest_role() -> None:
    assert highest_role([OrgRole.MEMBER, OrgRole.ADMIN]) == OrgRole.ADMIN
    assert highest_role([]) is None


def test_oidc_extract_groups() -> None:
    settings = MagicMock()
    settings.oidc_group_claim = "groups"
    service = OidcService(settings=settings)
    assert service._extract_groups({"groups": ["a", "b"]}) == ["a", "b"]
    assert service._extract_groups({"groups": "x, y"}) == ["x", "y"]
    assert service._extract_groups({}) == []


@pytest.mark.asyncio
async def test_invite_create_accept_and_sso_mapping(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "invite-owner@example.com",
            "password": "password123",
            "display_name": "Owner",
        },
    )
    assert owner_resp.status_code == 201
    owner_token = owner_resp.json()["access_token"]
    assert owner_resp.json()["needs_org_setup"] is True
    org_resp = await client.post(
        "/api/v1/orgs",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"name": "Invite Org"},
    )
    assert org_resp.status_code == 201
    org_id = org_resp.json()["id"]

    with patch(
        "app.services.orgs.EmailService.send_org_invite",
        return_value=(False, "email not configured"),
    ):
        invite_resp = await client.post(
            f"/api/v1/orgs/{org_id}/invites",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"email": "invitee@example.com", "role": "member"},
        )
    assert invite_resp.status_code == 201
    body = invite_resp.json()
    assert body["email"] == "invitee@example.com"
    assert body["invite_url"]
    assert body["email_sent"] is False
    token = body["invite_url"].rstrip("/").split("/")[-1]

    invitee_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "invitee@example.com",
            "password": "password123",
            "display_name": "Invitee",
        },
    )
    assert invitee_resp.status_code == 201
    invitee_token = invitee_resp.json()["access_token"]
    # Invites stay pending until accepted (in-app inbox / email link).
    pending = await client.get(
        "/api/v1/invites/pending",
        headers={"Authorization": f"Bearer {invitee_token}"},
    )
    assert pending.status_code == 200
    assert any(row["invite_id"] for row in pending.json() if row["kind"] == "org")

    accepted = await client.post(
        "/api/v1/orgs/invites/accept",
        headers={"Authorization": f"Bearer {invitee_token}"},
        json={"token": token},
    )
    assert accepted.status_code == 200
    assert accepted.json()["org_id"] == org_id

    # Create SSO mapping and sync via service
    mapping = await client.post(
        f"/api/v1/orgs/{org_id}/sso-mappings",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"group_name": "launchpad-admins", "role": "admin"},
    )
    assert mapping.status_code == 201
    assert mapping.json()["group_name"] == "launchpad-admins"

    listed = await client.get(
        f"/api/v1/orgs/{org_id}/sso-mappings",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    async with session_factory() as session:
        # Fresh user synced via groups
        user = User(
            email="sso-user@example.com",
            password_hash=hash_password("password123"),
            display_name="SSO User",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        service = OrganizationService(session)
        await service.ensure_personal_org(user)
        changed = await service.sync_sso_group_memberships(
            user=user,
            groups=["launchpad-admins"],
        )
        await session.commit()
        assert changed >= 1
        membership = await service.get_membership(org_id=__import__("uuid").UUID(org_id), user_id=user.id)
        assert membership is not None
        assert membership.role == OrgRole.ADMIN

    # Explicit accept still works for a new invite
    with patch(
        "app.services.orgs.EmailService.send_org_invite",
        return_value=(True, None),
    ):
        second = await client.post(
            f"/api/v1/orgs/{org_id}/invites",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"email": "late@example.com", "role": "viewer"},
        )
    assert second.status_code == 201
    assert second.json()["email_sent"] is True
    late_token = second.json()["invite_url"].rstrip("/").split("/")[-1]

    late = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "late@example.com",
            "password": "password123",
            "display_name": "Late",
        },
    )
    late_auth = late.json()["access_token"]
    again = await client.post(
        "/api/v1/orgs/invites/accept",
        headers={"Authorization": f"Bearer {late_auth}"},
        json={"token": late_token},
    )
    assert again.status_code == 200
    assert again.json()["email"] == "late@example.com"
    # Idempotent re-accept
    again2 = await client.post(
        "/api/v1/orgs/invites/accept",
        headers={"Authorization": f"Bearer {late_auth}"},
        json={"token": late_token},
    )
    assert again2.status_code == 200

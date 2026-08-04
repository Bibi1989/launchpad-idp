"""Organization RBAC coverage."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db_session
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.domain import Base, OrgRole, User
from app.services.orgs import OrganizationService, role_at_least


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


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def test_role_rank() -> None:
    assert role_at_least(OrgRole.ADMIN, OrgRole.MEMBER)
    assert not role_at_least(OrgRole.VIEWER, OrgRole.MEMBER)


@pytest.mark.asyncio
async def test_register_creates_personal_org(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@example.com",
            "password": "password123",
            "display_name": "Owner",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["orgs"]
    assert body["orgs"][0]["role"] == "owner"
    assert body["active_org_id"] == body["orgs"][0]["id"]


@pytest.mark.asyncio
async def test_list_and_add_org_member(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner2@example.com",
            "password": "password123",
            "display_name": "Owner",
        },
    )
    owner_token = owner_resp.json()["access_token"]
    org_id = owner_resp.json()["active_org_id"]

    async with session_factory() as session:
        member = User(
            email="member@example.com",
            password_hash=hash_password("password123"),
            display_name="Member",
        )
        session.add(member)
        await session.commit()

    listed = await client.get(
        "/api/v1/orgs",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert listed.status_code == 200
    assert any(item["id"] == org_id for item in listed.json())

    added = await client.post(
        f"/api/v1/orgs/{org_id}/members",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "member@example.com", "role": "member"},
    )
    assert added.status_code == 201
    assert added.json()["role"] == "member"
    assert added.json()["email"] == "member@example.com"

    members = await client.get(
        f"/api/v1/orgs/{org_id}/members",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert members.status_code == 200
    assert len(members.json()) == 2


@pytest.mark.asyncio
async def test_ensure_personal_org_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = User(
            email="solo@example.com",
            password_hash=hash_password("password123"),
            display_name="Solo",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        service = OrganizationService(session)
        org_a = await service.ensure_personal_org(user)
        org_b = await service.ensure_personal_org(user)
        await session.commit()
        assert org_a.id == org_b.id


@pytest.mark.asyncio
async def test_org_role_reads_lowercase_db_value(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Migration seeds role='owner'; ORM must not expect 'OWNER'."""
    from uuid import uuid4

    from sqlalchemy import select, text

    from app.models.domain import OrgMembership, Organization

    async with session_factory() as session:
        user = User(
            email="legacy@example.com",
            password_hash=hash_password("password123"),
            display_name="Legacy",
        )
        session.add(user)
        await session.flush()
        org = Organization(slug="legacy-org", name="Legacy org")
        session.add(org)
        await session.flush()
        membership_id = uuid4()
        await session.execute(
            text(
                "INSERT INTO org_memberships (id, org_id, user_id, role) "
                "VALUES (:id, :org_id, :user_id, :role)"
            ),
            {
                "id": membership_id.hex,
                "org_id": org.id.hex,
                "user_id": user.id.hex,
                "role": "owner",
            },
        )
        await session.commit()

        result = await session.execute(
            select(OrgMembership).where(OrgMembership.user_id == user.id)
        )
        membership = result.scalar_one()
        assert membership.role == OrgRole.OWNER


@pytest.mark.asyncio
async def test_org_cost_summary(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal
    from uuid import UUID

    from app.models.domain import EnvironmentStatus
    from app.repositories.environment import EnvironmentRepository

    owner_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "cost-owner@example.com",
            "password": "password123",
            "display_name": "Cost Owner",
        },
    )
    assert owner_resp.status_code == 201
    token = owner_resp.json()["access_token"]
    org_id = owner_resp.json()["active_org_id"]
    owner_id = UUID(owner_resp.json()["user"]["id"])

    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        local = await repo.create(
            owner_id=owner_id,
            org_id=UUID(org_id),
            name="cost-local",
            git_branch="main",
            git_repo_url="https://github.com/acme/local.git",
            namespace_name="launchpad-env-cost-local",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0"),
            provider="local",
        )
        await repo.update_status(local, EnvironmentStatus.RUNNING)
        cloud = await repo.create(
            owner_id=owner_id,
            org_id=UUID(org_id),
            name="cost-cloud",
            git_branch="main",
            git_repo_url="https://github.com/acme/cloud.git",
            namespace_name="launchpad-env-cost-cloud",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("1.0000"),
            provider="gcp",
        )
        await repo.update_status(cloud, EnvironmentStatus.RUNNING)
        cloud.created_at = datetime.now(UTC) - timedelta(hours=2)
        await session.commit()

    response = await client.get(
        f"/api/v1/orgs/{org_id}/costs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["org_id"] == org_id
    assert body["active_count"] == 2
    assert body["cloud_environment_count"] == 1
    assert Decimal(body["cloud_accrued"]) > 0
    assert Decimal(body["local_accrued"]) == 0
    assert body["soft_cost_cap_exceeded"] is False
    assert len(body["environments"]) == 2

    stranger = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "cost-stranger@example.com",
            "password": "password123",
            "display_name": "Stranger",
        },
    )
    denied = await client.get(
        f"/api/v1/orgs/{org_id}/costs",
        headers={"Authorization": f"Bearer {stranger.json()['access_token']}"},
    )
    assert denied.status_code == 404

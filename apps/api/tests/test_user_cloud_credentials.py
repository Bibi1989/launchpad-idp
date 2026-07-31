"""Account cloud credential vault."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db_session
from app.main import create_app
from app.models.domain import Base
from app.schemas.cloud import CloudCredentials
from app.schemas.user_credentials import UserCloudCredentialsUpdate
from app.services.user_credentials import UserCloudCredentialsService


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


@pytest.mark.asyncio
async def test_user_cloud_credentials_api_put(client: AsyncClient) -> None:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "creds@example.com",
            "password": "password123",
            "display_name": "Creds User",
        },
    )
    assert register.status_code == 201
    token = register.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.put(
        "/api/v1/users/me/cloud-credentials",
        headers=headers,
        json={
            "credentials": {
                "aws_role_arn": "arn:aws:iam::123456789012:role/Launchpad",
            }
        },
    )
    assert resp.status_code == 200
    assert resp.json()["has_aws"] is True
    assert "Launchpad" in (resp.json().get("aws_label") or "")

    get_resp = await client.get("/api/v1/users/me/cloud-credentials", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["has_aws"] is True
    assert "aws_secret" not in get_resp.text.lower()


@pytest.mark.asyncio
async def test_user_cloud_credentials_service(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        from app.models.domain import User
        from app.core.security import hash_password

        user = User(
            email="vault@example.com",
            password_hash=hash_password("password123"),
            display_name="Vault",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        service = UserCloudCredentialsService(session)
        status = await service.upsert(
            user.id,
            UserCloudCredentialsUpdate(
                credentials=CloudCredentials(
                    gcp_sa_key_json='{"type":"service_account","client_email":"a@b.iam.gserviceaccount.com"}',
                    aws_access_key_id="AKIAEXAMPLE",
                    aws_secret_access_key="secret",
                ),
            ),
        )
        assert status.has_gcp is True
        assert status.has_aws is True
        assert status.gcp_label == "Service account JSON"

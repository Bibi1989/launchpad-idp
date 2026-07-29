from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db_session
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.domain import Base, User


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
async def test_register_login_and_me(client: AsyncClient) -> None:
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "password123",
            "display_name": "Alice",
        },
    )
    assert register.status_code == 201
    token = register.json()["access_token"]
    assert register.json()["user"]["email"] == "alice@example.com"

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me.status_code == 200
    assert me.json()["user"]["display_name"] == "Alice"
    assert me.json()["orgs"]

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "password123"},
    )
    assert login.status_code == 200
    assert login.json()["access_token"]


@pytest.mark.asyncio
async def test_dev_login(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/dev-login")
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "dev@launchpad.local"


@pytest.mark.asyncio
async def test_auth_config(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/config")
    assert response.status_code == 200
    assert "dev_login_enabled" in response.json()


@pytest.mark.asyncio
async def test_duplicate_register_conflict(client: AsyncClient) -> None:
    payload = {
        "email": "bob@example.com",
        "password": "password123",
        "display_name": "Bob",
    }
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201
    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_invalid_login(client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(
            User(
                email="carol@example.com",
                password_hash=hash_password("password123"),
                display_name="Carol",
            )
        )
        await session.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "carol@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401


def test_access_token_roundtrip() -> None:
    from uuid import uuid4

    user_id = uuid4()
    token = create_access_token(user_id=user_id, email="x@example.com")
    from app.core.security import decode_access_token

    payload = decode_access_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["email"] == "x@example.com"

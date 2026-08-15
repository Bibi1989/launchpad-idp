"""Destroyed environments must free their unique name for relaunch."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.domain import Base, EnvironmentStatus, User
from app.repositories.environment import EnvironmentRepository


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_destroy_releases_name_for_reuse(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = User(
            email="reuse@example.com",
            password_hash=hash_password("password123"),
            display_name="Reuse",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        repo = EnvironmentRepository(session)
        first = await repo.create(
            owner_id=user.id,
            name="demo-app",
            git_branch="main",
            git_repo_url="https://github.com/acme/demo.git",
            namespace_name="launchpad-env-demo",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0"),
        )
        await repo.update_status(first, EnvironmentStatus.RUNNING)
        await repo.update_status(first, EnvironmentStatus.DESTROYED)
        await session.commit()

        assert first.status == EnvironmentStatus.DESTROYED
        assert "--destroyed-" in first.name
        assert first.namespace_name.startswith("destroyed-")
        assert await repo.get_by_name("demo-app") is None

        second = await repo.create(
            owner_id=user.id,
            name="demo-app",
            git_branch="main",
            git_repo_url="https://github.com/acme/demo.git",
            namespace_name="launchpad-env-demo-2",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0"),
        )
        await session.commit()
        assert second.name == "demo-app"
        assert second.id != first.id


@pytest.mark.asyncio
async def test_teardown_pending_releases_name_for_reuse(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = User(
            email="teardown-reuse@example.com",
            password_hash=hash_password("password123"),
            display_name="Reuse",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        repo = EnvironmentRepository(session)
        first = await repo.create(
            owner_id=user.id,
            name="demo-app",
            git_branch="main",
            git_repo_url="https://github.com/acme/demo.git",
            namespace_name="launchpad-env-demo",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0"),
        )
        await repo.update_status(first, EnvironmentStatus.RUNNING)
        await repo.update_status(first, EnvironmentStatus.TEARDOWN_PENDING)
        await session.commit()

        assert first.status == EnvironmentStatus.TEARDOWN_PENDING
        assert "--destroyed-" in first.name
        assert await repo.get_by_name("demo-app") is None
        listed = await repo.list_for_owner(user.id)
        assert all(row.id != first.id for row in listed)

        second = await repo.create(
            owner_id=user.id,
            name="demo-app",
            git_branch="main",
            git_repo_url="https://github.com/acme/demo.git",
            namespace_name="launchpad-env-demo-2",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0"),
        )
        await session.commit()
        assert second.name == "demo-app"

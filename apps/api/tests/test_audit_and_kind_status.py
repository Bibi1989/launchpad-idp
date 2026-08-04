"""Audit log list API and Kind readiness probe coverage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db_session
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.domain import AuditAction, AuditStatus, Base, User
from app.repositories.environment import EnvironmentRepository
from app.services.audit import AuditService
from app.services.kind_cluster import probe_kind_cluster


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def test_user(session_factory: async_sessionmaker[AsyncSession]) -> User:
    async with session_factory() as session:
        user = User(
            email="owner@example.com",
            password_hash=hash_password("password123"),
            display_name="Owner",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def other_user(session_factory: async_sessionmaker[AsyncSession]) -> User:
    async with session_factory() as session:
        user = User(
            email="other@example.com",
            password_hash=hash_password("password123"),
            display_name="Other",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


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


@pytest.mark.asyncio
async def test_list_environment_audits_owner_isolation(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    other_user: User,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="audit-env",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-audit",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        audits = AuditService(session)
        await audits.record(
            action=AuditAction.PROVISION_INITIATED,
            actor_id=str(test_user.id),
            status=AuditStatus.PENDING,
            environment_id=environment.id,
            detail="queued for provision",
        )
        await session.commit()
        env_id = environment.id

    owner_response = await client.get(
        f"/api/v1/environments/{env_id}/audits",
        headers=auth_header(test_user),
    )
    assert owner_response.status_code == 200
    body = owner_response.json()
    assert len(body) == 1
    assert body[0]["action"] == "PROVISION_INITIATED"
    assert body[0]["status"] == "PENDING"
    assert body[0]["detail"] == "queued for provision"
    assert body[0]["environment_id"] == str(env_id)

    stranger = await client.get(
        f"/api/v1/environments/{env_id}/audits",
        headers=auth_header(other_user),
    )
    assert stranger.status_code == 404

    unauth = await client.get(f"/api/v1/environments/{env_id}/audits")
    assert unauth.status_code == 401


@pytest.mark.asyncio
async def test_audit_list_for_environment_orders_newest_first() -> None:
    session = AsyncMock()
    older = MagicMock()
    newer = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [newer, older]
    session.execute = AsyncMock(return_value=result)

    service = AuditService(session)
    rows = await service.list_for_environment(uuid4(), limit=10)
    assert rows == [newer, older]
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_kind_cluster_tools_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIND_AUTO_MANAGE", "true")
    with (
        patch("app.services.kind_cluster.get_settings") as settings_mock,
        patch("app.services.kind_cluster.shutil.which", return_value=None),
    ):
        settings = settings_mock.return_value
        settings.kind_auto_manage = True
        settings.kind_cluster_name = "launchpad"
        payload = await probe_kind_cluster()

    assert payload["status"] == "tools_missing"
    assert payload["can_launch"] is False
    assert payload["kind_installed"] is False
    assert payload["kubectl_installed"] is False


@pytest.mark.asyncio
async def test_probe_kind_cluster_absent_with_auto_manage() -> None:
    async def fake_communicate() -> tuple[bytes, bytes]:
        return (b"other-cluster\n", b"")

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(side_effect=fake_communicate)

    def which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    with (
        patch("app.services.kind_cluster.get_settings") as settings_mock,
        patch("app.services.kind_cluster.shutil.which", side_effect=which),
        patch(
            "app.services.kind_cluster.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=proc,
        ),
    ):
        settings = settings_mock.return_value
        settings.kind_auto_manage = True
        settings.kind_cluster_name = "launchpad"
        payload = await probe_kind_cluster()

    assert payload["status"] == "absent"
    assert payload["cluster_exists"] is False
    assert payload["can_launch"] is True
    assert "automatically" in str(payload["message"])


@pytest.mark.asyncio
async def test_probe_kind_cluster_ready() -> None:
    # k3d cluster list --no-headers → "NAME SERVERS AGENTS ..." (first token is name).
    list_proc = MagicMock()
    list_proc.returncode = 0
    list_proc.communicate = AsyncMock(return_value=(b"launchpad 1/1 0/0 true\n", b""))

    # kubectl get nodes -o jsonpath → node Ready condition status.
    nodes_proc = MagicMock()
    nodes_proc.returncode = 0
    nodes_proc.communicate = AsyncMock(return_value=(b"True", b""))

    def which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    with (
        patch("app.services.kind_cluster.get_settings") as settings_mock,
        patch("app.services.kind_cluster.shutil.which", side_effect=which),
        patch(
            "app.services.kind_cluster.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=[list_proc, nodes_proc],
        ),
    ):
        settings = settings_mock.return_value
        settings.kind_auto_manage = True
        settings.kind_cluster_name = "launchpad"
        settings.local_k8s_engine = "k3s"
        settings.local_cluster_tool = "k3d"
        payload = await probe_kind_cluster()

    assert payload["status"] == "ready"
    assert payload["engine"] == "k3s"
    assert payload["context"] == "k3d-launchpad"
    assert payload["api_reachable"] is True
    assert payload["can_launch"] is True


@pytest.mark.asyncio
async def test_preview_kind_status_endpoint(
    client: AsyncClient,
    test_user: User,
) -> None:
    fake = {
        "status": "ready",
        "cluster": "launchpad",
        "context": "kind-launchpad",
        "kind_installed": True,
        "kubectl_installed": True,
        "cluster_exists": True,
        "api_reachable": True,
        "auto_manage": True,
        "message": "ok",
        "can_launch": True,
    }
    with patch(
        "app.routers.api.probe_kind_cluster",
        new_callable=AsyncMock,
        return_value=fake,
    ):
        response = await client.get(
            "/api/v1/preview/kind/status",
            headers=auth_header(test_user),
        )

    assert response.status_code == 200
    assert response.json()["can_launch"] is True
    assert response.json()["status"] == "ready"

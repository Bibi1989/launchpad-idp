"""Tests for workspace linked app repo (GitOps CD Option A/B)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db_session
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.domain import Base, ProvisioningWorkspace, User
from app.services.github_service import render_app_cd_workflow


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
            email="link-owner@example.com",
            password_hash=hash_password("password123"),
            display_name="Link Owner",
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
    return {"Authorization": f"Bearer {create_access_token(user_id=user.id, email=user.email)}"}


@pytest.mark.asyncio
async def test_linked_app_repo_webhook_default(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
) -> None:
    from app.services.orgs import OrganizationService

    workspace_id = uuid4()
    root = tmp_path / str(workspace_id)
    root.mkdir()

    async with session_factory() as session:
        personal = await OrganizationService(session).ensure_personal_org(test_user)
        session.add(
            ProvisioningWorkspace(
                id=workspace_id,
                owner_id=test_user.id,
                org_id=personal.id,
                name="link-ws",
                engine="terraform",
                provider="gcp",
                root_dir=str(root),
                status="ready",
                wizard_config_json=json.dumps({"name": "link-ws"}),
            )
        )
        await session.commit()

    headers = auth_header(test_user)
    empty = await client.get(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-app-repo",
        headers=headers,
    )
    assert empty.status_code == 200
    assert empty.json()["linked"] is None

    saved = await client.put(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-app-repo",
        headers=headers,
        json={
            "installation_id": 42,
            "full_name": "acme/app",
            "git_branch": "develop",
            "cd_mode": "webhook",
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["linked"]["full_name"] == "acme/app"
    assert body["linked"]["git_branch"] == "develop"
    assert body["linked"]["cd_mode"] == "webhook"
    assert body["linked"]["git_repo_url"] == "https://github.com/acme/app.git"
    assert body["workflow_path"] is None

    async with session_factory() as session:
        row = await session.get(ProvisioningWorkspace, workspace_id)
        assert row is not None
        snap = json.loads(row.wizard_config_json or "{}")
        assert snap["git_repo_url"] == "https://github.com/acme/app.git"
        assert snap["git_branch"] == "develop"
        assert snap["linked_app_repo"]["cd_mode"] == "webhook"

    cleared = await client.put(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-app-repo",
        headers=headers,
        json={"clear": True},
    )
    assert cleared.status_code == 200
    assert cleared.json()["linked"] is None


@pytest.mark.asyncio
async def test_linked_app_repo_github_actions_writes_workflow(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings
    from app.services.orgs import OrganizationService

    monkeypatch.setenv("WEBHOOK_SECRET", "test-cd-secret")
    get_settings.cache_clear()

    workspace_id = uuid4()
    root = tmp_path / str(workspace_id)
    root.mkdir()

    async with session_factory() as session:
        personal = await OrganizationService(session).ensure_personal_org(test_user)
        session.add(
            ProvisioningWorkspace(
                id=workspace_id,
                owner_id=test_user.id,
                org_id=personal.id,
                name="actions-ws",
                engine="terraform",
                provider="gcp",
                root_dir=str(root),
                status="ready",
                wizard_config_json="{}",
            )
        )
        await session.commit()

    with patch(
        "app.services.github_service.GitHubProvisioningService.ensure_app_cd_workflow",
        return_value=".github/workflows/launchpad-app-cd.yml",
    ) as ensure:
        res = await client.put(
            f"/api/v1/provisioning/workspaces/{workspace_id}/linked-app-repo",
            headers=auth_header(test_user),
            json={
                "installation_id": 7,
                "full_name": "acme/web",
                "git_branch": "main",
                "cd_mode": "github_actions",
            },
        )
    assert res.status_code == 200, res.text
    ensure.assert_called_once()
    assert res.json()["workflow_path"] == ".github/workflows/launchpad-app-cd.yml"
    assert res.json()["linked"]["cd_mode"] == "github_actions"

    get_settings.cache_clear()


def test_render_app_cd_workflow_tracks_branch() -> None:
    yml = render_app_cd_workflow(
        branch="release",
        control_plane_url="https://api.example.com",
    )
    assert 'branches: ["release"]' in yml
    assert "/api/v1/webhooks/github-actions-cd" in yml
    assert "LAUNCHPAD_CD_SECRET" in yml

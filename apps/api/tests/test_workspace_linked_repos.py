"""Tests for multi-repo linked repositories on a workspace."""

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
            email="multi-link@example.com",
            password_hash=hash_password("password123"),
            display_name="Multi Link",
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


async def _make_workspace(
    session_factory: async_sessionmaker[AsyncSession],
    user: User,
    tmp_path: Path,
    snapshot: dict | None = None,
):
    from app.services.orgs import OrganizationService

    workspace_id = uuid4()
    root = tmp_path / str(workspace_id)
    root.mkdir()
    async with session_factory() as session:
        personal = await OrganizationService(session).ensure_personal_org(user)
        session.add(
            ProvisioningWorkspace(
                id=workspace_id,
                owner_id=user.id,
                org_id=personal.id,
                name="multi-ws",
                engine="terraform",
                provider="gcp",
                root_dir=str(root),
                status="ready",
                wizard_config_json=json.dumps(snapshot or {"name": "multi-ws"}),
            )
        )
        await session.commit()
    return workspace_id


@pytest.mark.asyncio
async def test_set_and_get_multiple_gitlab_repos(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
) -> None:
    workspace_id = await _make_workspace(session_factory, test_user, tmp_path)
    headers = auth_header(test_user)

    empty = await client.get(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-repos", headers=headers
    )
    assert empty.status_code == 200
    assert empty.json()["repos"] == []

    saved = await client.put(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-repos",
        headers=headers,
        json={
            "repos": [
                {"kind": "gitlab", "git_repo_url": "https://gitlab.com/acme/orders.git", "git_branch": "main"},
                {"kind": "gitlab", "git_repo_url": "https://gitlab.com/acme/billing.git", "git_branch": "dev"},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert len(body["repos"]) == 2
    # Primary drives the legacy single-repo fields.
    assert body["primary_git_repo_url"] == "https://gitlab.com/acme/orders.git"

    async with session_factory() as session:
        row = await session.get(ProvisioningWorkspace, workspace_id)
        snap = json.loads(row.wizard_config_json or "{}")
        assert snap["git_repo_url"] == "https://gitlab.com/acme/orders.git"
        assert snap["git_branch"] == "main"
        assert len(snap["linked_repos"]) == 2

    # Round-trips.
    again = await client.get(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-repos", headers=headers
    )
    assert [r["git_repo_url"] for r in again.json()["repos"]] == [
        "https://gitlab.com/acme/orders.git",
        "https://gitlab.com/acme/billing.git",
    ]


@pytest.mark.asyncio
async def test_github_primary_sets_linked_app_repo_and_clears(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
) -> None:
    workspace_id = await _make_workspace(session_factory, test_user, tmp_path)
    headers = auth_header(test_user)

    saved = await client.put(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-repos",
        headers=headers,
        json={
            "repos": [
                {
                    "kind": "github",
                    "git_repo_url": "https://github.com/acme/api.git",
                    "git_branch": "main",
                    "full_name": "acme/api",
                    "installation_id": 11,
                    "cd_mode": "webhook",
                },
            ]
        },
    )
    assert saved.status_code == 200, saved.text

    async with session_factory() as session:
        row = await session.get(ProvisioningWorkspace, workspace_id)
        snap = json.loads(row.wizard_config_json or "{}")
        assert snap["linked_app_repo"]["full_name"] == "acme/api"

    # Legacy single-repo endpoint still reflects the primary.
    legacy = await client.get(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-app-repo", headers=headers
    )
    assert legacy.json()["linked"]["full_name"] == "acme/api"

    cleared = await client.put(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-repos",
        headers=headers,
        json={"repos": []},
    )
    assert cleared.status_code == 200
    assert cleared.json()["repos"] == []


@pytest.mark.asyncio
async def test_github_actions_installs_workflow_per_repo(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("WEBHOOK_SECRET", "cd-secret")
    get_settings.cache_clear()

    workspace_id = await _make_workspace(session_factory, test_user, tmp_path)
    headers = auth_header(test_user)

    with patch(
        "app.services.github_service.GitHubProvisioningService.ensure_app_cd_workflow",
        return_value=".github/workflows/launchpad-app-cd.yml",
    ) as ensure:
        res = await client.put(
            f"/api/v1/provisioning/workspaces/{workspace_id}/linked-repos",
            headers=headers,
            json={
                "repos": [
                    {
                        "kind": "github",
                        "git_repo_url": "https://github.com/acme/a.git",
                        "git_branch": "main",
                        "full_name": "acme/a",
                        "installation_id": 1,
                        "cd_mode": "github_actions",
                    },
                    {
                        "kind": "github",
                        "git_repo_url": "https://github.com/acme/b.git",
                        "git_branch": "main",
                        "full_name": "acme/b",
                        "installation_id": 1,
                        "cd_mode": "github_actions",
                    },
                ]
            },
        )
    assert res.status_code == 200, res.text
    # A workflow is installed for each github repo (all scoped to this workspace).
    assert ensure.call_count == 2
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_legacy_single_link_surfaces_as_list(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
) -> None:
    # A workspace created before multi-repo (only git_repo_url in snapshot).
    workspace_id = await _make_workspace(
        session_factory,
        test_user,
        tmp_path,
        snapshot={"name": "legacy", "git_repo_url": "https://gitlab.com/x/y.git", "git_branch": "trunk"},
    )
    headers = auth_header(test_user)
    res = await client.get(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-repos", headers=headers
    )
    assert res.status_code == 200
    repos = res.json()["repos"]
    assert len(repos) == 1
    assert repos[0]["git_repo_url"] == "https://gitlab.com/x/y.git"
    assert repos[0]["git_branch"] == "trunk"


@pytest.mark.asyncio
async def test_frontend_is_primary_by_default_and_user_can_override(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
) -> None:
    workspace_id = await _make_workspace(session_factory, test_user, tmp_path)
    headers = auth_header(test_user)

    # Backend added first, frontend second -> frontend should be primary by default.
    saved = await client.put(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-repos",
        headers=headers,
        json={
            "repos": [
                {"kind": "gitlab", "git_repo_url": "https://github.com/acme/virtual-office-backend.git", "git_branch": "main"},
                {"kind": "gitlab", "git_repo_url": "https://github.com/acme/virtual-office-frontend.git", "git_branch": "main"},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    repos = saved.json()["repos"]
    assert repos[0]["git_repo_url"].endswith("virtual-office-frontend.git")
    assert repos[0]["primary"] is True
    assert repos[1]["primary"] is False
    assert saved.json()["primary_git_repo_url"].endswith("virtual-office-frontend.git")

    # User overrides: mark the backend primary explicitly.
    override = await client.put(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-repos",
        headers=headers,
        json={
            "repos": [
                {"kind": "gitlab", "git_repo_url": "https://github.com/acme/virtual-office-frontend.git", "git_branch": "main", "primary": False},
                {"kind": "gitlab", "git_repo_url": "https://github.com/acme/virtual-office-backend.git", "git_branch": "main", "primary": True},
            ]
        },
    )
    assert override.status_code == 200, override.text
    repos = override.json()["repos"]
    assert repos[0]["git_repo_url"].endswith("virtual-office-backend.git")
    assert repos[0]["primary"] is True


@pytest.mark.asyncio
async def test_no_frontend_keeps_first_as_primary(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
) -> None:
    workspace_id = await _make_workspace(session_factory, test_user, tmp_path)
    headers = auth_header(test_user)
    saved = await client.put(
        f"/api/v1/provisioning/workspaces/{workspace_id}/linked-repos",
        headers=headers,
        json={
            "repos": [
                {"kind": "gitlab", "git_repo_url": "https://github.com/acme/orders-api.git", "git_branch": "main"},
                {"kind": "gitlab", "git_repo_url": "https://github.com/acme/billing.git", "git_branch": "main"},
            ]
        },
    )
    assert saved.status_code == 200
    assert saved.json()["repos"][0]["git_repo_url"].endswith("orders-api.git")
    assert saved.json()["repos"][0]["primary"] is True

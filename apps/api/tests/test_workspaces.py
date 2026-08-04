from __future__ import annotations

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
            email="ws-owner@example.com",
            password_hash=hash_password("password123"),
            display_name="WS Owner",
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
async def test_list_and_destroy_workspace(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
) -> None:
    from app.services.orgs import OrganizationService

    workspace_id = uuid4()
    root = tmp_path / str(workspace_id)
    root.mkdir()
    (root / "main.tf").write_text("resource \"null_resource\" \"x\" {}", encoding="utf-8")

    async with session_factory() as session:
        personal = await OrganizationService(session).ensure_personal_org(test_user)
        session.add(
            ProvisioningWorkspace(
                id=workspace_id,
                owner_id=test_user.id,
                org_id=personal.id,
                name="demo-ws",
                engine="terraform",
                provider="gcp",
                root_dir=str(root),
                status="ready",
            )
        )
        await session.commit()

    listed = await client.get("/api/v1/provisioning/workspaces", headers=auth_header(test_user))
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["name"] == "demo-ws"

    with patch("app.services.iac_generator.IaCGenerator.destroy_workspace", return_value=True):
        deleted = await client.delete(
            f"/api/v1/provisioning/workspaces/{workspace_id}",
            headers=auth_header(test_user),
        )
    assert deleted.status_code == 204

    listed_after = await client.get(
        "/api/v1/provisioning/workspaces",
        headers=auth_header(test_user),
    )
    assert listed_after.status_code == 200
    assert listed_after.json() == []


@pytest.mark.asyncio
async def test_destroy_workspace_removes_catalog_service(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
) -> None:
    from app.models.domain import CatalogService
    from app.services.orgs import OrganizationService

    workspace_id = uuid4()
    service_id = uuid4()
    root = tmp_path / str(workspace_id)
    root.mkdir()

    async with session_factory() as session:
        personal = await OrganizationService(session).ensure_personal_org(test_user)
        session.add(
            ProvisioningWorkspace(
                id=workspace_id,
                owner_id=test_user.id,
                org_id=personal.id,
                name="svc-linked-ws",
                engine="terraform",
                provider="local",
                root_dir=str(root),
                status="ready",
            )
        )
        session.add(
            CatalogService(
                id=service_id,
                owner_id=test_user.id,
                org_id=personal.id,
                workspace_id=workspace_id,
                name="linked-svc",
                description="from golden path",
                service_owner=test_user.email,
                tier="tier-2",
                slo_target="99.5",
                template_id="fastapi-api",
                template_version="1.0.0",
                compliance_score=80,
                scorecard_json='{"score":80,"gate":70,"passed":true,"items":[]}',
            )
        )
        await session.commit()

    with patch("app.services.iac_generator.IaCGenerator.destroy_workspace", return_value=True):
        deleted = await client.delete(
            f"/api/v1/provisioning/workspaces/{workspace_id}",
            headers=auth_header(test_user),
        )
    assert deleted.status_code == 204

    async with session_factory() as session:
        remaining = await session.get(CatalogService, service_id)
        assert remaining is None


@pytest.mark.asyncio
async def test_delete_catalog_service_endpoint(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    from app.models.domain import CatalogService
    from app.services.orgs import OrganizationService

    service_id = uuid4()
    async with session_factory() as session:
        personal = await OrganizationService(session).ensure_personal_org(test_user)
        session.add(
            CatalogService(
                id=service_id,
                owner_id=test_user.id,
                org_id=personal.id,
                workspace_id=None,
                name="solo-svc",
                description="",
                service_owner=test_user.email,
                tier="tier-2",
                slo_target="99.5",
                template_id="fastapi-api",
                template_version="1.0.0",
                compliance_score=70,
                scorecard_json='{"score":70,"gate":70,"passed":true,"items":[]}',
            )
        )
        await session.commit()

    deleted = await client.delete(
        f"/api/v1/catalog/services/{service_id}",
        headers=auth_header(test_user),
    )
    assert deleted.status_code == 204

    listed = await client.get("/api/v1/catalog/services", headers=auth_header(test_user))
    assert listed.status_code == 200
    assert listed.json() == []


@pytest.mark.asyncio
async def test_get_workspace_when_files_missing_on_disk(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    workspace_id = uuid4()
    missing_root = f"/tmp/launchpad-workspaces/missing-{workspace_id}"
    durable = tmp_path / "workspaces"
    monkeypatch.setenv("IAC_WORKSPACE_ROOT", str(durable))
    get_settings.cache_clear()

    async with session_factory() as session:
        session.add(
            ProvisioningWorkspace(
                id=workspace_id,
                owner_id=test_user.id,
                name="orphaned-ws",
                engine="terraform",
                provider="gcp",
                root_dir=missing_root,
                status="ready",
            )
        )
        await session.commit()

    response = await client.get(
        f"/api/v1/provisioning/workspaces/{workspace_id}",
        headers=auth_header(test_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace_id)
    assert body["files"] == []
    assert body["artifact_mode"] == "iac_only"

    files_response = await client.get(
        f"/api/v1/provisioning/workspaces/{workspace_id}/files/tree",
        headers=auth_header(test_user),
    )
    assert files_response.status_code == 200
    assert len(files_response.json()) > 0
    restored = await client.get(
        f"/api/v1/provisioning/workspaces/{workspace_id}",
        headers=auth_header(test_user),
    )
    assert restored.status_code == 200
    restored_root = Path(restored.json()["root_dir"])
    assert restored_root.is_dir()
    assert durable.resolve() in restored_root.resolve().parents
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_restore_local_workspace_from_wizard_snapshot(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from app.core.config import get_settings
    from app.core.secrets import encrypt_secret

    workspace_id = uuid4()
    missing_root = tmp_path / "gone" / "django"
    durable = tmp_path / "durable"
    monkeypatch.setenv("IAC_WORKSPACE_ROOT", str(durable))
    get_settings.cache_clear()

    snapshot = {
        "name": "django",
        "iac_engine": "terraform",
        "cloud": {
            "provider": "local",
            "resources": {"cluster_name": "launchpad"},
        },
        "run_init": True,
        "artifact_mode": "manifest_only",
        "kubernetes_packaging": "raw_manifests",
        "kubernetes_options": {},
        "cost_optimization": {},
        "container_scaffold": {},
        "dependencies": {},
    }

    async with session_factory() as session:
        session.add(
            ProvisioningWorkspace(
                id=workspace_id,
                owner_id=test_user.id,
                name="django",
                engine="terraform",
                provider="local",
                root_dir=str(missing_root),
                status="ready",
                encrypted_credentials=encrypt_secret("{}"),
                wizard_config_json=json.dumps(snapshot),
            )
        )
        await session.commit()

    with patch(
        "app.services.provisioning.ensure_kind_cluster",
        return_value={"status": "ready"},
    ):
        restore = await client.post(
            f"/api/v1/provisioning/workspaces/{workspace_id}/restore-files",
            headers=auth_header(test_user),
        )
    assert restore.status_code == 200
    body = restore.json()
    assert body["files"]
    assert Path(body["root_dir"]).is_dir()
    assert any("infra/k8s" in f or f.endswith(".yaml") for f in body["files"])
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_update_workspace_recreates_missing_workspace_dir(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    workspace_id = uuid4()
    missing_root = f"/tmp/launchpad-workspaces/missing-{workspace_id}"

    async with session_factory() as session:
        session.add(
            ProvisioningWorkspace(
                id=workspace_id,
                owner_id=test_user.id,
                name="orphaned-update-ws",
                engine="terraform",
                provider="gcp",
                root_dir=missing_root,
                status="ready",
            )
        )
        await session.commit()

    payload = {
        "name": "orphaned-update-ws",
        "iac_engine": "terraform",
        "cloud": {
            "provider": "gcp",
            "resources": {
                "project_id": "demo-proj",
                "vpc": True,
                "subnets": True,
                "gke": False,
                "artifact_registry": False,
                "secret_backend": "secret_manager",
                "cloud_run": False,
                "cloud_functions": False,
                "region": "us-central1",
            },
        },
        "credentials": {},
        "run_init": True,
        "artifact_mode": "iac_only",
        "kubernetes_packaging": "none",
    }
    response = await client.put(
        f"/api/v1/provisioning/workspaces/{workspace_id}",
        headers=auth_header(test_user),
        json=payload,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace_id)
    assert body["files"]
    assert Path(missing_root).is_dir()

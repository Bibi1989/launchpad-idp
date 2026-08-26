"""HTTP tests for plugin validate / register endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db_session
from app.main import create_app
from app.models.domain import Base


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


async def _auth_org(client: AsyncClient) -> dict[str, str]:
    register = await client.post(
        "/api/v1/auth/register",
        json={"email": "plugins@example.com", "password": "password123", "display_name": "Plug"},
    )
    assert register.status_code == 201
    token = register.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    org = await client.post("/api/v1/orgs", headers=headers, json={"name": "Plugin Org"})
    assert org.status_code == 201
    headers["X-Org-ID"] = org.json()["id"]
    return headers


def _good_manifest() -> dict:
    return {
        "id": "digitalocean-droplet",
        "label": "DigitalOcean Droplets",
        "version": "1.0.0",
        "category": "cloud-provider",
        "description": "Provision droplets with Terraform.",
        "runner": {"type": "terraform", "bundlePath": "digitalocean"},
        "capabilities": {"serviceType": "vm", "supportsTtl": True},
        "credentialsSchema": {
            "type": "object",
            "required": ["token"],
            "properties": {"token": {"type": "string", "title": "API Token"}},
        },
        "deploymentConfigSchema": {
            "type": "object",
            "properties": {"region": {"type": "string"}},
        },
    }


@pytest.mark.asyncio
async def test_validate_rejects_bad_json_schema(client: AsyncClient) -> None:
    headers = await _auth_org(client)
    resp = await client.post(
        "/api/v1/plugins/validate",
        headers=headers,
        json={"manifest": {"label": "X", "credentialsSchema": {"type": "not-a-type"}}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    locs = {e["loc"] for e in body["errors"]}
    assert any("credentialsSchema" in loc or "credentials_schema" in loc for loc in locs)


@pytest.mark.asyncio
async def test_validate_accepts_full_manifest(client: AsyncClient) -> None:
    headers = await _auth_org(client)
    resp = await client.post(
        "/api/v1/plugins/validate",
        headers=headers,
        json={"manifest": _good_manifest()},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["errors"] == []
    assert body["manifest"]["id"] == "digitalocean-droplet"


@pytest.mark.asyncio
async def test_register_persists_and_get_round_trips(client: AsyncClient) -> None:
    headers = await _auth_org(client)
    created = await client.post(
        "/api/v1/plugins/register",
        headers=headers,
        json={"manifest": _good_manifest()},
    )
    assert created.status_code == 201
    assert created.json()["id"] == "digitalocean-droplet"

    fetched = await client.get("/api/v1/plugins/digitalocean-droplet", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["manifest"]["label"] == "DigitalOcean Droplets"

    listed = await client.get("/api/v1/cloud-providers/plugins", headers=headers)
    assert listed.status_code == 200
    assert any(item["id"] == "digitalocean-droplet" for item in listed.json())


@pytest.mark.asyncio
async def test_register_user_owned_plugin_is_usable_in_catalog(client: AsyncClient) -> None:
    headers = await _auth_org(client)
    created = await client.post(
        "/api/v1/plugins/register",
        headers=headers,
        json={"manifest": _good_manifest(), "owner": "user", "visibility": "public"},
    )
    assert created.status_code == 201
    assert created.json()["owner"] == "user"
    assert created.json()["visibility"] == "public"

    fetched = await client.get("/api/v1/plugins/digitalocean-droplet", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["owner"] == "user"
    assert fetched.json()["can_edit"] is True

    catalog = await client.get("/api/v1/cloud-providers", headers=headers)
    assert catalog.status_code == 200
    match = next(item for item in catalog.json() if item["id"] == "digitalocean-droplet")
    assert match["owner"] == "user"
    assert match["source"] == "manifest"

    other = await client.post(
        "/api/v1/auth/register",
        json={"email": "plugins-other@example.com", "password": "password123", "display_name": "Other"},
    )
    assert other.status_code == 201
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    other_org = await client.post("/api/v1/orgs", headers=other_headers, json={"name": "Other Org"})
    assert other_org.status_code == 201
    other_headers["X-Org-ID"] = other_org.json()["id"]
    other_catalog = await client.get("/api/v1/cloud-providers", headers=other_headers)
    assert other_catalog.status_code == 200
    assert any(item["id"] == "digitalocean-droplet" for item in other_catalog.json())


@pytest.mark.asyncio
async def test_register_returns_field_errors(client: AsyncClient) -> None:
    headers = await _auth_org(client)
    resp = await client.post(
        "/api/v1/plugins/register",
        headers=headers,
        json={"manifest": {"version": "nope"}},
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "invalid_manifest"
    assert "errors" in (error.get("details") or {})
    locs = {e["loc"] for e in error["details"]["errors"]}
    assert "version" in locs or "label" in locs


@pytest.mark.asyncio
async def test_legacy_cloud_providers_plugins_alias(client: AsyncClient) -> None:
    headers = await _auth_org(client)
    resp = await client.post(
        "/api/v1/cloud-providers/plugins/validate",
        headers=headers,
        json={"manifest": _good_manifest()},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True

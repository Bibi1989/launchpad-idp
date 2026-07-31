from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.routers.well_known import clear_jwks_cache
from pkg.auth.oidc import reset_key_manager


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_well_known_openid_configuration(client: AsyncClient) -> None:
    resp = await client.get("/.well-known/openid-configuration")
    assert resp.status_code == 200
    data = resp.json()

    assert data["issuer"] == "https://api.launchpad.yourdomain.com"
    assert data["jwks_uri"] == "https://api.launchpad.yourdomain.com/.well-known/jwks.json"
    assert data["response_types_supported"] == ["id_token"]
    assert data["subject_types_supported"] == ["public"]
    assert data["id_token_signing_alg_values_supported"] == ["RS256"]


@pytest.mark.asyncio
async def test_well_known_jwks_json(client: AsyncClient) -> None:
    reset_key_manager()
    clear_jwks_cache()

    resp = await client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    assert "public, max-age=" in resp.headers.get("Cache-Control", "")

    data = resp.json()
    assert "keys" in data
    assert len(data["keys"]) >= 1

    key = data["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert key["use"] == "sig"
    assert "n" in key
    assert "e" in key
    assert "kid" in key

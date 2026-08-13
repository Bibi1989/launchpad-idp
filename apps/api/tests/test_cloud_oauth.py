"""Interactive cloud OAuth vault helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.secrets import (
    _materialize_interactive_oauth_env,
    has_azure_auth,
    has_gcp_auth,
)
from app.schemas.cloud import CloudCredentials
from app.schemas.cloud_oauth import CloudOAuthProviderName, CloudOAuthStartRequest
from app.services.cloud_oauth import CloudOAuthError, CloudOAuthService


def test_has_auth_includes_oauth_tokens() -> None:
    assert has_gcp_auth(CloudCredentials(gcp_oauth_token_json='{"provider":"gcp"}'))
    assert has_azure_auth(CloudCredentials(azure_oauth_token_json='{"provider":"azure"}'))


def test_materialize_gcp_authorized_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.secrets._oidc_workdir", lambda _ws: tmp_path)
    monkeypatch.setattr(
        "app.core.secrets.get_settings",
        lambda: type(
            "S",
            (),
            {
                "gcp_oauth_client_id": "client.apps.googleusercontent.com",
                "gcp_oauth_client_secret": "secret",
            },
        )(),
    )
    monkeypatch.setattr(
        "app.core.secrets._mint_gcp_user_access_token",
        lambda **_kwargs: "ya29.minted-cloud-platform",
    )
    token = {
        "provider": "gcp",
        "access_token": "ya29.access",
        "refresh_token": "1//refresh",
        "token_type": "Bearer",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "claims": {"client_id": "client.apps.googleusercontent.com"},
    }
    env = {"GCP_OAUTH_TOKEN_JSON": json.dumps(token)}
    _materialize_interactive_oauth_env(env, workspace_id="ws-oauth")
    assert "GOOGLE_APPLICATION_CREDENTIALS" in env
    adc = json.loads(Path(env["GOOGLE_APPLICATION_CREDENTIALS"]).read_text(encoding="utf-8"))
    assert adc["type"] == "authorized_user"
    assert adc["refresh_token"] == "1//refresh"
    assert env.get("CLOUDSDK_AUTH_ACCESS_TOKEN") == "ya29.minted-cloud-platform"
    assert "GCP_OAUTH_TOKEN_JSON" not in env


def test_cloud_oauth_capabilities_respect_config() -> None:
    session = MagicMock()
    settings = MagicMock()
    settings.gcp_oauth_client_id = "gcp-client"
    settings.azure_oauth_client_id = None
    service = CloudOAuthService(session, settings=settings)
    caps = service.capabilities()
    assert caps.gcp is True
    assert caps.aws is True
    assert caps.azure is False


@pytest.mark.asyncio
async def test_cloud_oauth_start_requires_aws_start_url() -> None:
    session = MagicMock()
    settings = MagicMock()
    settings.gcp_oauth_client_id = None
    settings.azure_oauth_client_id = None
    settings.cloud_oauth_timeout_seconds = 30
    service = CloudOAuthService(session, settings=settings)
    with pytest.raises(CloudOAuthError) as exc:
        await service.start(
            user_id=__import__("uuid").uuid4(),
            payload=CloudOAuthStartRequest(
                provider=CloudOAuthProviderName.AWS,
                aws_region="us-east-1",
            ),
        )
    assert exc.value.code == "aws_start_url_required"

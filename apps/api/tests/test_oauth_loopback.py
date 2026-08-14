"""OAuth loopback + PKCE unit tests (no live IdP calls)."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from pkg.auth.oauth_loopback import (
    CloudOAuthProvider,
    CloudTokenSet,
    GcpOAuthProvider,
    LoopbackServer,
    OAuthLoopbackClient,
    OAuthTimeoutError,
    code_challenge_s256,
    generate_pkce,
    generate_state,
)
from pkg.auth.oauth_loopback.models import expires_at_from_expires_in
from pkg.auth.oauth_loopback.pkce import generate_code_verifier
from pkg.auth.oauth_loopback.provider import normalize_oauth_token_response
from pkg.auth.oauth_loopback.providers.aws import AwsSsoOAuthProvider
from pkg.auth.oauth_loopback.providers.azure import AzureOAuthProvider


def test_pkce_s256_matches_sha256_base64url() -> None:
    pair = generate_pkce()
    assert 43 <= len(pair.verifier) <= 128
    assert pair.method == "S256"
    digest = hashlib.sha256(pair.verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    assert pair.challenge == expected
    assert pair.challenge == code_challenge_s256(pair.verifier)


def test_generate_code_verifier_bounds() -> None:
    with pytest.raises(ValueError):
        generate_code_verifier(16)
    assert len(generate_state()) >= 32


def test_gcp_authorize_url_contains_pkce_and_state() -> None:
    provider = GcpOAuthProvider(client_id="gcp-client.apps.googleusercontent.com")
    pkce = generate_pkce()
    url = provider.build_authorize_url(
        redirect_uri="http://127.0.0.1:8765/callback",
        state="abc",
        pkce=pkce,
    )
    parsed = urlparse(url)
    assert parsed.netloc == "accounts.google.com"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["gcp-client.apps.googleusercontent.com"]
    assert qs["response_type"] == ["code"]
    assert qs["code_challenge"] == [pkce.challenge]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["state"] == ["abc"]
    assert "cloud-platform" in qs["scope"][0]
    assert "compute" in qs["scope"][0]
    assert "include_granted_scopes" not in qs


def test_gcp_token_requires_cloud_platform_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    from pkg.auth.oauth_loopback.providers.gcp import GcpOAuthProvider

    provider = GcpOAuthProvider(client_id="gcp-client.apps.googleusercontent.com")
    token = CloudTokenSet(
        provider=CloudOAuthProvider.GCP,
        access_token="ya29.x",
        refresh_token="1//r",
        token_type="Bearer",
        scope="openid https://www.googleapis.com/auth/userinfo.email",
        expires_at=datetime.now(timezone.utc),
        claims={},
    )
    monkeypatch.setattr(provider, "_lookup_token_scopes", lambda _t: token.scope)
    with pytest.raises(ValueError, match="cloud-platform"):
        provider._ensure_cloud_scopes(token)

    ok = token.model_copy(
        update={"scope": "openid https://www.googleapis.com/auth/cloud-platform"}
    )
    assert provider._ensure_cloud_scopes(ok).scope is not None


def test_azure_authorize_url_tenant_and_pkce() -> None:
    provider = AzureOAuthProvider(client_id="azure-app-id", tenant_id="contoso.onmicrosoft.com")
    pkce = generate_pkce()
    url = provider.build_authorize_url(
        redirect_uri="http://127.0.0.1:9999/callback",
        state="st",
        pkce=pkce,
    )
    assert "login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/authorize" in url
    qs = parse_qs(urlparse(url).query)
    assert qs["code_challenge_method"] == ["S256"]
    assert "offline_access" in qs["scope"][0]


def test_aws_authorize_requires_prepare() -> None:
    provider = AwsSsoOAuthProvider(
        start_url="https://my-sso.awsapps.com/start",
        region="us-east-1",
    )
    with pytest.raises(RuntimeError):
        provider.build_authorize_url(
            redirect_uri="http://127.0.0.1:1/callback",
            state="s",
            pkce=generate_pkce(),
        )


def test_normalize_token_set_identity_hints() -> None:
    def b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    id_token = f"{b64({'alg': 'none'})}.{b64({'sub': 'user-1', 'email': 'a@b.co'})}.x"
    ts = normalize_oauth_token_response(
        CloudOAuthProvider.GCP,
        {
            "access_token": "atok",
            "refresh_token": "rtok",
            "id_token": id_token,
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "openid email",
        },
    )
    assert ts.subject == "user-1"
    assert ts.email == "a@b.co"
    assert ts.can_refresh is True
    assert ts.is_expired is False
    assert ts.seconds_until_expiry() is not None


def test_cloud_token_set_expiry() -> None:
    past = expires_at_from_expires_in(-10)
    ts = CloudTokenSet(
        provider=CloudOAuthProvider.AZURE,
        access_token="x",
        expires_at=past,
    )
    assert ts.is_expired is True


def test_loopback_captures_code_and_shuts_down() -> None:
    server = LoopbackServer(timeout_seconds=5, expected_state="good")
    server.start()

    def _hit() -> None:
        httpx.get(f"{server.redirect_uri}?code=authcode&state=good", timeout=5.0)

    threading.Thread(target=_hit, daemon=True).start()
    result = server.wait()
    assert result.code == "authcode"
    assert result.state == "good"


def test_loopback_timeout() -> None:
    server = LoopbackServer(timeout_seconds=0.2, expected_state="x")
    server.start()
    with pytest.raises(OAuthTimeoutError):
        server.wait()


def test_login_with_mock_provider() -> None:
    captured: dict[str, str] = {}

    class FakeProvider(GcpOAuthProvider):
        def build_authorize_url(self, *, redirect_uri: str, state: str, pkce):  # type: ignore[no-untyped-def]
            captured["state"] = state
            captured["redirect_uri"] = redirect_uri
            return f"https://example.test/auth?state={state}"

        def exchange_code(self, *, code: str, redirect_uri: str, pkce):  # type: ignore[no-untyped-def]
            assert code == "c1"
            assert redirect_uri == captured["redirect_uri"]
            return CloudTokenSet(
                provider=CloudOAuthProvider.GCP,
                access_token="access",
                refresh_token="refresh",
                expires_at=datetime.now(timezone.utc),
            )

    provider = FakeProvider(client_id="cid")
    client = OAuthLoopbackClient(provider, open_browser=False, timeout_seconds=5)

    def _drive_callback() -> None:
        for _ in range(50):
            if "redirect_uri" in captured and "state" in captured:
                httpx.get(
                    f"{captured['redirect_uri']}?code=c1&state={captured['state']}",
                    timeout=5.0,
                )
                return
            threading.Event().wait(0.05)

    threading.Thread(target=_drive_callback, daemon=True).start()
    tokens = client.login()
    assert tokens.access_token == "access"
    assert tokens.provider == CloudOAuthProvider.GCP

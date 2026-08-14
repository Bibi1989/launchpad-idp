"""Provider protocol and shared token-exchange helpers."""

from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from pkg.auth.oauth_loopback.models import (
    CloudOAuthProvider,
    CloudTokenSet,
    OAuthTokenExchangeError,
    expires_at_from_expires_in,
)
from pkg.auth.oauth_loopback.pkce import PkcePair

logger = structlog.get_logger(__name__)


def decode_jwt_claims_unverified(token: str) -> dict[str, Any]:
    """Decode JWT payload without signature verification (identity hints only)."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        pad = "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(payload + pad)
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


class CloudOAuthProviderBase(ABC):
    """Extensible provider: authorize URL + token (and optional refresh) exchange."""

    provider: CloudOAuthProvider

    @abstractmethod
    def build_authorize_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        pkce: PkcePair,
    ) -> str:
        """Return the browser authorization URL including PKCE + state."""

    @abstractmethod
    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        pkce: PkcePair,
    ) -> CloudTokenSet:
        """Exchange authorization code for a normalized token set."""

    def refresh(self, token_set: CloudTokenSet) -> CloudTokenSet:
        """Refresh access token when the provider supports it."""
        raise NotImplementedError(f"{self.provider.value} refresh is not implemented")

    def prepare(self, *, redirect_uri: str) -> None:
        """Optional hook before listen (e.g. AWS RegisterClient)."""

    def cleanup(self) -> None:
        """Optional teardown after login attempt."""


def post_form_token(
    token_url: str,
    data: dict[str, str],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST ``application/x-www-form-urlencoded`` to a token endpoint."""
    req_headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        **(headers or {}),
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(token_url, content=urlencode(data), headers=req_headers)
    return _parse_token_response(resp, token_url)


def post_json_token(
    token_url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST JSON body to a token endpoint (AWS SSO OIDC CreateToken)."""
    req_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **(headers or {}),
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(token_url, json=payload, headers=req_headers)
    return _parse_token_response(resp, token_url)


def _parse_token_response(resp: httpx.Response, token_url: str) -> dict[str, Any]:
    try:
        body: Any = resp.json()
    except Exception:  # noqa: BLE001
        body = {"raw": resp.text[:500]}

    if resp.status_code >= 400:
        err = body if isinstance(body, dict) else {}
        message = (
            err.get("error_description")
            or err.get("error")
            or err.get("message")
            or f"HTTP {resp.status_code} from token endpoint"
        )
        logger.warning(
            "oauth_token_exchange_failed",
            status_code=resp.status_code,
            token_host=httpx.URL(token_url).host,
            error=str(err.get("error") or ""),
        )
        raise OAuthTokenExchangeError(str(message))

    if not isinstance(body, dict):
        raise OAuthTokenExchangeError("Token endpoint returned non-object JSON")
    return body


def normalize_oauth_token_response(
    provider: CloudOAuthProvider,
    body: dict[str, Any],
    *,
    extra_claims: dict[str, Any] | None = None,
) -> CloudTokenSet:
    """Map standard OAuth token JSON into ``CloudTokenSet``."""
    access = body.get("access_token") or body.get("accessToken")
    if not access or not isinstance(access, str):
        raise OAuthTokenExchangeError("Token response missing access_token")

    refresh = body.get("refresh_token") or body.get("refreshToken")
    id_token = body.get("id_token") or body.get("idToken")
    token_type = body.get("token_type") or body.get("tokenType") or "Bearer"
    expires_in = body.get("expires_in") or body.get("expiresIn")
    scope = body.get("scope")
    if isinstance(scope, list):
        scope = " ".join(str(s) for s in scope)

    claims: dict[str, Any] = dict(extra_claims or {})
    subject: str | None = None
    email: str | None = None
    if isinstance(id_token, str) and id_token:
        id_claims = decode_jwt_claims_unverified(id_token)
        claims["id_token_claims"] = {
            k: id_claims[k]
            for k in ("iss", "aud", "sub", "email", "name", "preferred_username", "oid", "tid")
            if k in id_claims
        }
        sub = id_claims.get("sub")
        subject = str(sub) if sub is not None else None
        mail = id_claims.get("email") or id_claims.get("preferred_username")
        email = str(mail) if mail is not None else None

    return CloudTokenSet(
        provider=provider,
        access_token=access,
        token_type=str(token_type),
        expires_at=expires_at_from_expires_in(int(expires_in) if expires_in is not None else None),
        refresh_token=str(refresh) if refresh else None,
        id_token=str(id_token) if id_token else None,
        scope=str(scope) if scope else None,
        subject=subject,
        email=email,
        claims=claims,
    )

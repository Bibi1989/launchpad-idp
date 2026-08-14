"""AWS IAM Identity Center (SSO OIDC) authorization-code + PKCE provider."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import structlog

from pkg.auth.oauth_loopback.models import CloudOAuthProvider, CloudTokenSet, OAuthTokenExchangeError
from pkg.auth.oauth_loopback.pkce import PkcePair
from pkg.auth.oauth_loopback.provider import (
    CloudOAuthProviderBase,
    normalize_oauth_token_response,
    post_json_token,
)

logger = structlog.get_logger(__name__)

DEFAULT_AWS_SCOPES = (
    "openid",
    "sso:account:access",
)


def oidc_base_url(region: str) -> str:
    return f"https://oidc.{region}.amazonaws.com"


class AwsSsoOAuthProvider(CloudOAuthProviderBase):
    """IAM Identity Center public client via RegisterClient + CreateToken (PKCE).

    Mirrors AWS CLI v2.22+ browser login: dynamic public client registration
    against the regional SSO OIDC endpoint, then authorization-code + PKCE.
    """

    provider = CloudOAuthProvider.AWS

    def __init__(
        self,
        *,
        start_url: str,
        region: str,
        client_name: str = "launchpad",
        scopes: tuple[str, ...] | list[str] | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        authorization_endpoint: str | None = None,
        token_endpoint: str | None = None,
    ) -> None:
        if not start_url.strip():
            raise ValueError("AWS start_url is required (IAM Identity Center portal URL)")
        if not region.strip():
            raise ValueError("AWS region is required")
        self.start_url = start_url.strip().rstrip("/")
        self.region = region.strip()
        self.client_name = client_name
        self.scopes = tuple(scopes) if scopes else DEFAULT_AWS_SCOPES
        self._client_id = (client_id or "").strip() or None
        self._client_secret = (client_secret or "").strip() or None
        self._authorization_endpoint = (authorization_endpoint or "").strip() or None
        self._token_endpoint = (token_endpoint or "").strip() or None
        self._registered = False

    def prepare(self, *, redirect_uri: str) -> None:
        if self._client_id and self._client_secret and self._authorization_endpoint and self._token_endpoint:
            return
        self._register_client(redirect_uri=redirect_uri)

    def _register_client(self, *, redirect_uri: str) -> None:
        """Call SSO OIDC RegisterClient for authorization_code + refresh_token."""
        url = f"{oidc_base_url(self.region)}/client/register"
        payload: dict[str, Any] = {
            "clientName": self.client_name,
            "clientType": "public",
            "scopes": list(self.scopes),
            "grantTypes": ["authorization_code", "refresh_token"],
            "redirectUris": [redirect_uri],
            "issuerUrl": self.start_url,
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        try:
            body: Any = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise OAuthTokenExchangeError(
                f"AWS RegisterClient returned non-JSON ({resp.status_code})"
            ) from exc
        if resp.status_code >= 400 or not isinstance(body, dict):
            message = (
                (body.get("error_description") if isinstance(body, dict) else None)
                or (body.get("error") if isinstance(body, dict) else None)
                or f"RegisterClient HTTP {resp.status_code}"
            )
            raise OAuthTokenExchangeError(str(message))

        self._client_id = str(body["clientId"])
        self._client_secret = str(body["clientSecret"])
        self._authorization_endpoint = str(
            body.get("authorizationEndpoint")
            or f"{self.start_url}/authorize"
        )
        self._token_endpoint = str(
            body.get("tokenEndpoint") or f"{oidc_base_url(self.region)}/token"
        )
        self._registered = True
        logger.info(
            "aws_sso_client_registered",
            region=self.region,
            start_host=urlparse(self.start_url).hostname,
        )

    def build_authorize_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        pkce: PkcePair,
    ) -> str:
        if not self._authorization_endpoint or not self._client_id:
            raise RuntimeError("Call prepare() before build_authorize_url()")
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": pkce.challenge,
            "code_challenge_method": pkce.method,
            "scopes": ",".join(self.scopes),
        }
        # Some Identity Center portals also accept space-delimited ``scope``.
        params["scope"] = " ".join(self.scopes)
        return f"{self._authorization_endpoint}?{urlencode(params)}"

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        pkce: PkcePair,
    ) -> CloudTokenSet:
        if not self._token_endpoint or not self._client_id or not self._client_secret:
            raise RuntimeError("Call prepare() before exchange_code()")
        payload = {
            "clientId": self._client_id,
            "clientSecret": self._client_secret,
            "grantType": "authorization_code",
            "code": code,
            "redirectUri": redirect_uri,
            "codeVerifier": pkce.verifier,
        }
        body = post_json_token(self._token_endpoint, payload)
        return normalize_oauth_token_response(
            self.provider,
            body,
            extra_claims={
                "start_url": self.start_url,
                "region": self.region,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "token_endpoint": self._token_endpoint,
            },
        )

    def refresh(self, token_set: CloudTokenSet) -> CloudTokenSet:
        if not token_set.refresh_token:
            raise ValueError("No refresh_token available")
        client_id = str(token_set.claims.get("client_id") or self._client_id or "")
        client_secret = str(token_set.claims.get("client_secret") or self._client_secret or "")
        token_endpoint = str(
            token_set.claims.get("token_endpoint")
            or self._token_endpoint
            or f"{oidc_base_url(self.region)}/token"
        )
        if not client_id or not client_secret:
            raise ValueError("AWS refresh requires registered client_id/client_secret")
        payload = {
            "clientId": client_id,
            "clientSecret": client_secret,
            "grantType": "refresh_token",
            "refreshToken": token_set.refresh_token,
        }
        body = post_json_token(token_endpoint, payload)
        if "refreshToken" not in body and "refresh_token" not in body:
            body = {**body, "refreshToken": token_set.refresh_token}
        return normalize_oauth_token_response(
            self.provider,
            body,
            extra_claims={
                **{k: v for k, v in token_set.claims.items() if k != "client_secret"},
                "start_url": self.start_url,
                "region": self.region,
                "client_id": client_id,
                "client_secret": client_secret,
                "token_endpoint": token_endpoint,
            },
        )

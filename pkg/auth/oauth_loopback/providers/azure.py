"""Microsoft Entra ID (Azure) public-client loopback provider."""

from __future__ import annotations

from urllib.parse import urlencode

from pkg.auth.oauth_loopback.models import CloudOAuthProvider, CloudTokenSet
from pkg.auth.oauth_loopback.pkce import PkcePair
from pkg.auth.oauth_loopback.provider import (
    CloudOAuthProviderBase,
    normalize_oauth_token_response,
    post_form_token,
)

DEFAULT_AZURE_SCOPES = (
    "openid",
    "profile",
    "offline_access",
    "https://management.azure.com/user_impersonation",
)


class AzureOAuthProvider(CloudOAuthProviderBase):
    """Entra ID authorization-code + PKCE for public (desktop/CLI) clients."""

    provider = CloudOAuthProvider.AZURE

    def __init__(
        self,
        *,
        client_id: str,
        tenant_id: str = "common",
        scopes: tuple[str, ...] | list[str] | None = None,
        authorize_url: str | None = None,
        token_url: str | None = None,
    ) -> None:
        if not client_id.strip():
            raise ValueError("Azure client_id is required")
        tenant = (tenant_id or "common").strip()
        self.client_id = client_id.strip()
        self.tenant_id = tenant
        self.scopes = tuple(scopes) if scopes else DEFAULT_AZURE_SCOPES
        base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
        self.authorize_url = authorize_url or f"{base}/authorize"
        self.token_url = token_url or f"{base}/token"

    def build_authorize_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        pkce: PkcePair,
    ) -> str:
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": pkce.challenge,
            "code_challenge_method": pkce.method,
        }
        return f"{self.authorize_url}?{urlencode(params)}"

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        pkce: PkcePair,
    ) -> CloudTokenSet:
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": pkce.verifier,
            "scope": " ".join(self.scopes),
        }
        body = post_form_token(self.token_url, data)
        return normalize_oauth_token_response(
            self.provider,
            body,
            extra_claims={
                "client_id": self.client_id,
                "tenant_id": self.tenant_id,
            },
        )

    def refresh(self, token_set: CloudTokenSet) -> CloudTokenSet:
        if not token_set.refresh_token:
            raise ValueError("No refresh_token available")
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": token_set.refresh_token,
            "scope": " ".join(self.scopes),
        }
        body = post_form_token(self.token_url, data)
        if "refresh_token" not in body and token_set.refresh_token:
            body = {**body, "refresh_token": token_set.refresh_token}
        return normalize_oauth_token_response(
            self.provider,
            body,
            extra_claims={
                **token_set.claims,
                "client_id": self.client_id,
                "tenant_id": self.tenant_id,
            },
        )

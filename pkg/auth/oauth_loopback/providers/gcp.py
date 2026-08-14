"""Google Cloud / Google OAuth 2.0 desktop (loopback) provider."""

from __future__ import annotations

from urllib.parse import urlencode

from pkg.auth.oauth_loopback.models import CloudOAuthProvider, CloudTokenSet
from pkg.auth.oauth_loopback.pkce import PkcePair
from pkg.auth.oauth_loopback.provider import (
    CloudOAuthProviderBase,
    normalize_oauth_token_response,
    post_form_token,
)

# https://developers.google.com/identity/protocols/oauth2/native-app
# cloud-platform is required for gcloud compute (VM create). A prior consent that
# only granted email/profile yields "insufficient authentication scopes".
GCP_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GCP_TOKEN_URL = "https://oauth2.googleapis.com/token"
GCP_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

GCP_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
GCP_COMPUTE_SCOPE = "https://www.googleapis.com/auth/compute"

DEFAULT_GCP_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    GCP_CLOUD_PLATFORM_SCOPE,
    GCP_COMPUTE_SCOPE,
)


def gcp_token_has_cloud_platform(scope: str | None) -> bool:
    """True when the granted scope string includes cloud-platform (or compute)."""
    parts = [p.strip() for p in (scope or "").replace(",", " ").split() if p.strip()]
    if not parts:
        return False
    return any(
        p == GCP_CLOUD_PLATFORM_SCOPE
        or p.endswith("/auth/cloud-platform")
        or p == GCP_COMPUTE_SCOPE
        or p.endswith("/auth/compute")
        for p in parts
    )


class GcpOAuthProvider(CloudOAuthProviderBase):
    """Desktop OAuth client for Google Cloud user credentials."""

    provider = CloudOAuthProvider.GCP

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str | None = None,
        scopes: tuple[str, ...] | list[str] | None = None,
        access_type: str = "offline",
        prompt: str = "consent",
        token_url: str = GCP_TOKEN_URL,
        authorize_url: str = GCP_AUTHORIZE_URL,
    ) -> None:
        if not client_id.strip():
            raise ValueError("GCP client_id is required")
        self.client_id = client_id.strip()
        self.client_secret = (client_secret or "").strip() or None
        self.scopes = tuple(scopes) if scopes else DEFAULT_GCP_SCOPES
        self.access_type = access_type
        self.prompt = prompt
        self.token_url = token_url
        self.authorize_url = authorize_url

    def build_authorize_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        pkce: PkcePair,
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": pkce.challenge,
            "code_challenge_method": pkce.method,
            "access_type": self.access_type,
            # Do not use include_granted_scopes: prior email-only consent can
            # leave compute tokens without cloud-platform.
        }
        if self.prompt:
            params["prompt"] = self.prompt
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
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        body = post_form_token(self.token_url, data)
        token_set = normalize_oauth_token_response(
            self.provider,
            body,
            extra_claims={
                "client_id": self.client_id,
                "requested_scopes": " ".join(self.scopes),
            },
        )
        return self._ensure_cloud_scopes(token_set)

    def refresh(self, token_set: CloudTokenSet) -> CloudTokenSet:
        if not token_set.refresh_token:
            raise ValueError("No refresh_token available")
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": token_set.refresh_token,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        body = post_form_token(self.token_url, data)
        # Google often omits refresh_token on refresh responses.
        if "refresh_token" not in body and token_set.refresh_token:
            body = {**body, "refresh_token": token_set.refresh_token}
        refreshed = normalize_oauth_token_response(
            self.provider,
            body,
            extra_claims={**token_set.claims, "client_id": self.client_id},
        )
        return self._ensure_cloud_scopes(refreshed)

    def _ensure_cloud_scopes(self, token_set: CloudTokenSet) -> CloudTokenSet:
        scope = token_set.scope
        if not gcp_token_has_cloud_platform(scope):
            scope = self._lookup_token_scopes(token_set.access_token) or scope
        if not gcp_token_has_cloud_platform(scope):
            raise ValueError(
                "Google did not grant cloud-platform/compute scopes. In Google Cloud "
                "Console → APIs & Services → OAuth consent screen, add "
                f"{GCP_CLOUD_PLATFORM_SCOPE}, then revoke Launchpad access at "
                "https://myaccount.google.com/permissions and Connect GCP again."
            )
        if scope and scope != token_set.scope:
            return token_set.model_copy(update={"scope": scope})
        return token_set

    def _lookup_token_scopes(self, access_token: str) -> str | None:
        import httpx

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(
                    GCP_TOKENINFO_URL,
                    params={"access_token": access_token},
                )
            if resp.status_code >= 400:
                return None
            payload = resp.json()
        except Exception:
            return None
        raw = payload.get("scope")
        if isinstance(raw, list):
            return " ".join(str(s) for s in raw)
        return str(raw) if raw else None

"""OIDC Authorization Code helpers (discovery + token exchange)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, status

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OidcClaims:
    issuer: str
    subject: str
    email: str
    display_name: str
    groups: tuple[str, ...] = ()


class OidcService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def require_enabled(self) -> None:
        cfg = self._settings
        if not cfg.oidc_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "oidc_disabled", "message": "OIDC login is disabled"},
            )
        if not cfg.oidc_issuer_url or not cfg.oidc_client_id or not cfg.oidc_client_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "oidc_misconfigured",
                    "message": "OIDC_ISSUER_URL, OIDC_CLIENT_ID, and OIDC_CLIENT_SECRET are required",
                },
            )

    def create_state(self) -> str:
        cfg = self._settings
        now = datetime.now(UTC)
        payload = {
            "typ": "oidc_state",
            "nonce": secrets.token_urlsafe(16),
            "iat": now,
            "exp": now + timedelta(minutes=10),
            "iss": "launchpad-idp",
        }
        return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)

    def verify_state(self, state: str) -> None:
        cfg = self._settings
        try:
            payload = jwt.decode(
                state,
                cfg.jwt_secret,
                algorithms=[cfg.jwt_algorithm],
                issuer="launchpad-idp",
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_state", "message": "Invalid or expired OIDC state"},
            ) from exc
        if payload.get("typ") != "oidc_state":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_state", "message": "Invalid OIDC state type"},
            )

    async def discovery(self) -> dict[str, Any]:
        self.require_enabled()
        issuer = (self._settings.oidc_issuer_url or "").rstrip("/")
        url = f"{issuer}/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "oidc_discovery_failed", "message": "OIDC discovery failed"},
            )
        return response.json()

    async def authorization_url(self) -> tuple[str, str]:
        meta = await self.discovery()
        state = self.create_state()
        scopes = (self._settings.oidc_scopes or "openid profile email").strip()
        params = {
            "response_type": "code",
            "client_id": self._settings.oidc_client_id,
            "redirect_uri": self._settings.oidc_redirect_uri,
            "scope": scopes,
            "state": state,
        }
        auth_endpoint = meta.get("authorization_endpoint")
        if not auth_endpoint:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "oidc_discovery_failed",
                    "message": "authorization_endpoint missing from OIDC discovery",
                },
            )
        return f"{auth_endpoint}?{urlencode(params)}", state

    async def exchange_code(self, *, code: str, state: str) -> OidcClaims:
        self.verify_state(state)
        meta = await self.discovery()
        token_endpoint = meta.get("token_endpoint")
        if not token_endpoint:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "oidc_discovery_failed",
                    "message": "token_endpoint missing from OIDC discovery",
                },
            )
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._settings.oidc_redirect_uri,
            "client_id": self._settings.oidc_client_id,
            "client_secret": self._settings.oidc_client_secret,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(token_endpoint, data=data)
        if response.status_code >= 400:
            logger.warning("oidc_token_exchange_failed", status=response.status_code)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "oidc_exchange_failed", "message": "OIDC token exchange failed"},
            )
        payload = response.json()
        id_token = payload.get("id_token")
        if not id_token or not isinstance(id_token, str):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "oidc_exchange_failed", "message": "id_token missing from token response"},
            )
        # MVP: decode without JWKS verification when discovery lacks jwks (still validates structure).
        # Prefer JWKS when available.
        claims = await self._decode_id_token(id_token, meta)
        email = str(claims.get("email") or "").strip().lower()
        subject = str(claims.get("sub") or "").strip()
        issuer = str(claims.get("iss") or self._settings.oidc_issuer_url or "").rstrip("/")
        if not email or not subject:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "oidc_claims_missing",
                    "message": "OIDC id_token must include email and sub",
                },
            )
        display_name = str(
            claims.get("name")
            or claims.get("preferred_username")
            or email.split("@", 1)[0]
        )
        groups = self._extract_groups(claims)
        return OidcClaims(
            issuer=issuer,
            subject=subject,
            email=email,
            display_name=display_name,
            groups=tuple(groups),
        )

    def _extract_groups(self, claims: dict[str, Any]) -> list[str]:
        claim_name = (self._settings.oidc_group_claim or "groups").strip() or "groups"
        raw = claims.get(claim_name)
        if raw is None and claim_name != "groups":
            raw = claims.get("groups")
        if raw is None:
            return []
        if isinstance(raw, str):
            return [part.strip() for part in raw.split(",") if part.strip()]
        if isinstance(raw, (list, tuple)):
            return [str(item).strip() for item in raw if str(item).strip()]
        return []

    async def _decode_id_token(self, id_token: str, meta: dict[str, Any]) -> dict[str, Any]:
        jwks_uri = meta.get("jwks_uri")
        if not jwks_uri:
            # Unsafe fallback for local/dev IdPs without JWKS - still requires iss match.
            claims = jwt.decode(
                id_token,
                options={"verify_signature": False, "verify_aud": False},
            )
            expected_iss = (self._settings.oidc_issuer_url or "").rstrip("/")
            if str(claims.get("iss") or "").rstrip("/") != expected_iss:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"code": "oidc_invalid_issuer", "message": "id_token issuer mismatch"},
                )
            return claims

        async with httpx.AsyncClient(timeout=15.0) as client:
            jwks_response = await client.get(jwks_uri)
        if jwks_response.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "oidc_jwks_failed", "message": "Failed to fetch OIDC JWKS"},
            )
        jwks = jwks_response.json()
        try:
            header = jwt.get_unverified_header(id_token)
            kid = header.get("kid")
            key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
            if key is None and jwks.get("keys"):
                key = jwks["keys"][0]
            if key is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"code": "oidc_jwks_failed", "message": "No matching JWKS key"},
                )
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
            return jwt.decode(
                id_token,
                key=public_key,
                algorithms=[header.get("alg", "RS256")],
                audience=self._settings.oidc_client_id,
                issuer=(self._settings.oidc_issuer_url or "").rstrip("/"),
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("oidc_id_token_invalid", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "oidc_invalid_token", "message": "Invalid OIDC id_token"},
            ) from exc

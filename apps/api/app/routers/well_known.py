from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from pkg.auth.oidc.key_manager import get_key_manager

router = APIRouter(tags=["well-known"])

_jwks_cache: dict[str, Any] | None = None
_jwks_cache_timestamp: float = 0.0
CACHE_TTL_SECONDS = 300.0  # 5 minutes in-memory cache TTL for JWKS endpoint


def clear_jwks_cache() -> None:
    """Clear in-memory JWKS cache (useful when rotating keys)."""
    global _jwks_cache, _jwks_cache_timestamp
    _jwks_cache = None
    _jwks_cache_timestamp = 0.0


@router.get("/.well-known/openid-configuration")
async def openid_configuration() -> dict[str, Any]:
    """Public OIDC discovery endpoint for GCP Workload Identity Federation & AWS STS."""
    settings = get_settings()
    issuer = settings.launchpad_oidc_issuer_url.rstrip("/")
    return {
        "issuer": issuer,
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "response_types_supported": ["id_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid"],
        "claims_supported": [
            "iss",
            "sub",
            "aud",
            "exp",
            "iat",
            "nbf",
            "jti",
            "workspace_id",
            "org_id",
            "environment",
        ],
    }


@router.get("/.well-known/jwks.json")
async def jwks_json() -> Response:
    """Public JWKS endpoint returning active RSA public keys for token verification."""
    global _jwks_cache, _jwks_cache_timestamp

    now = time.time()
    if _jwks_cache is not None and (now - _jwks_cache_timestamp) < CACHE_TTL_SECONDS:
        return JSONResponse(
            content=_jwks_cache,
            headers={
                "Cache-Control": f"public, max-age={int(CACHE_TTL_SECONDS)}",
            },
        )

    settings = get_settings()
    key_manager = get_key_manager(
        primary_kid=settings.launchpad_oidc_key_id,
        private_key_pem=settings.launchpad_oidc_private_key,
        private_key_path=settings.launchpad_oidc_private_key_path,
    )
    jwks_data = key_manager.get_jwks()

    _jwks_cache = jwks_data
    _jwks_cache_timestamp = now

    return JSONResponse(
        content=jwks_data,
        headers={
            "Cache-Control": f"public, max-age={int(CACHE_TTL_SECONDS)}",
        },
    )

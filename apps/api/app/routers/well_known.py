from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pkg.auth.oidc.key_manager import get_key_manager

from app.core.config import get_settings
from app.services.agent_install import build_agent_bundle, render_install_script

router = APIRouter(tags=["well-known"])


@router.get("/install.sh", response_class=PlainTextResponse)
async def agent_install_script(request: Request) -> PlainTextResponse:
    """Public host installer for the hybrid agent: ``curl ... | TOKEN=lp_x sh``."""
    return PlainTextResponse(
        render_install_script(request_base_url=str(request.base_url)),
        media_type="text/x-shellscript",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/agent/bundle.tar.gz")
async def agent_source_bundle() -> Response:
    """Agent Docker build context, so a host can build the image with no registry."""
    try:
        data = build_agent_bundle()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "agent_source_unavailable", "message": str(exc)},
        ) from exc
    return Response(
        content=data,
        media_type="application/gzip",
        headers={"Cache-Control": "no-store"},
    )


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

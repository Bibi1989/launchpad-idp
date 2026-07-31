from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
import structlog

from pkg.auth.oidc.key_manager import OidcKeyManager, get_key_manager

logger = structlog.get_logger(__name__)

DEFAULT_ISSUER = "https://api.launchpad.yourdomain.com"
GCP_AUDIENCE = "https://iam.googleapis.com"
AWS_AUDIENCE = "https://sts.amazonaws.com"
MAX_TTL_SECONDS = 900  # 15 minutes max


@dataclass
class TokenRequest:
    org_id: str
    workspace_id: str
    env_type: str = "production"
    provider: str = "gcp"  # gcp | aws | custom
    custom_aud: str | None = None
    ttl_seconds: int = 900  # Default 15 minutes


class OidcTokenEngine:
    """Core OIDC Token Generation Engine for Launchpad Execution Sandboxes.

    Generates dynamic, short-lived JWTs signed with RS256 per workspace execution.
    """

    def __init__(
        self,
        key_manager: OidcKeyManager | None = None,
        issuer_url: str = DEFAULT_ISSUER,
    ) -> None:
        self.key_manager = key_manager or get_key_manager()
        self.issuer_url = issuer_url.rstrip("/")

    def generate_token(self, request: TokenRequest) -> str:
        """Generate a short-lived OIDC ID token for a workspace execution session."""
        now = int(time.time())
        ttl = min(max(60, request.ttl_seconds), MAX_TTL_SECONDS)
        exp = now + ttl

        # Determine target audience based on cloud provider or custom audience
        if request.custom_aud:
            aud = request.custom_aud
        elif request.provider.lower() == "aws":
            aud = AWS_AUDIENCE
        else:
            aud = GCP_AUDIENCE

        sub = f"organization:{request.org_id}:workspace:{request.workspace_id}:environment:{request.env_type}"
        jti = str(uuid.uuid4())

        payload: dict[str, Any] = {
            "iss": self.issuer_url,
            "sub": sub,
            "aud": aud,
            "exp": exp,
            "nbf": now,
            "iat": now,
            "jti": jti,
            "workspace_id": request.workspace_id,
            "org_id": request.org_id,
            "environment": request.env_type,
            "provider": request.provider.lower(),
        }

        keypair = self.key_manager.get_keypair()
        pem_bytes = keypair.private_bytes_pem()

        token = jwt.encode(
            payload,
            pem_bytes,
            algorithm="RS256",
            headers={"kid": keypair.kid, "typ": "JWT"},
        )

        # Audit Logging (fingerprint/jti without logging full secret token value)
        logger.info(
            "oidc_token_issued",
            jti=jti,
            org_id=request.org_id,
            workspace_id=request.workspace_id,
            env_type=request.env_type,
            provider=request.provider.lower(),
            aud=aud,
            sub=sub,
            exp=exp,
            ttl_seconds=ttl,
            kid=keypair.kid,
        )

        return token

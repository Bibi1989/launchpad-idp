"""pkg/auth/oidc package - Keyless OIDC Token Generation & Key Management Suite."""

from pkg.auth.oidc.key_manager import OidcKeyManager, get_key_manager, reset_key_manager
from pkg.auth.oidc.token_engine import (
    AWS_AUDIENCE,
    DEFAULT_ISSUER,
    GCP_AUDIENCE,
    MAX_TTL_SECONDS,
    OidcTokenEngine,
    TokenRequest,
)

__all__ = [
    "AWS_AUDIENCE",
    "DEFAULT_ISSUER",
    "GCP_AUDIENCE",
    "MAX_TTL_SECONDS",
    "OidcKeyManager",
    "OidcTokenEngine",
    "TokenRequest",
    "get_key_manager",
    "reset_key_manager",
]

"""Multi-cloud OAuth 2.0 loopback (RFC 8252) + PKCE (RFC 7636).

Architecture
------------
Generic pieces live here under ``pkg/auth/oauth_loopback`` (shared with the
API and future CLI/agent). Provider-specific endpoints live in
``providers/``. Control-plane vault wiring (encrypt into
``CloudCredentials``) belongs in ``apps/api/app/services/``, not here.
"""

from pkg.auth.oauth_loopback.browser import open_authorize_url
from pkg.auth.oauth_loopback.client import OAuthLoopbackClient, login_with_provider
from pkg.auth.oauth_loopback.loopback import DEFAULT_TIMEOUT_SECONDS, LoopbackServer
from pkg.auth.oauth_loopback.models import (
    AuthCodeResult,
    CloudOAuthProvider,
    CloudTokenSet,
    OAuthLoopbackError,
    OAuthProviderDeniedError,
    OAuthStateMismatchError,
    OAuthTimeoutError,
    OAuthTokenExchangeError,
)
from pkg.auth.oauth_loopback.pkce import (
    PkcePair,
    code_challenge_s256,
    generate_code_verifier,
    generate_pkce,
    generate_state,
)
from pkg.auth.oauth_loopback.provider import CloudOAuthProviderBase
from pkg.auth.oauth_loopback.providers import (
    AwsSsoOAuthProvider,
    AzureOAuthProvider,
    GcpOAuthProvider,
)

__all__ = [
    "AuthCodeResult",
    "AwsSsoOAuthProvider",
    "AzureOAuthProvider",
    "CloudOAuthProvider",
    "CloudOAuthProviderBase",
    "CloudTokenSet",
    "DEFAULT_TIMEOUT_SECONDS",
    "GcpOAuthProvider",
    "LoopbackServer",
    "OAuthLoopbackClient",
    "OAuthLoopbackError",
    "OAuthProviderDeniedError",
    "OAuthStateMismatchError",
    "OAuthTimeoutError",
    "OAuthTokenExchangeError",
    "PkcePair",
    "code_challenge_s256",
    "generate_code_verifier",
    "generate_pkce",
    "generate_state",
    "login_with_provider",
    "open_authorize_url",
]

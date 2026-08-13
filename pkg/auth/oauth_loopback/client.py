"""Orchestrate RFC 8252 loopback login for any ``CloudOAuthProviderBase``."""

from __future__ import annotations

import structlog

from pkg.auth.oauth_loopback.browser import open_authorize_url
from pkg.auth.oauth_loopback.loopback import DEFAULT_TIMEOUT_SECONDS, LoopbackServer
from pkg.auth.oauth_loopback.models import (
    CloudTokenSet,
    OAuthProviderDeniedError,
    OAuthStateMismatchError,
)
from pkg.auth.oauth_loopback.pkce import generate_pkce, generate_state
from pkg.auth.oauth_loopback.provider import CloudOAuthProviderBase

logger = structlog.get_logger(__name__)


class OAuthLoopbackClient:
    """Stack-agnostic authorization-code + PKCE loopback client."""

    def __init__(
        self,
        provider: CloudOAuthProviderBase,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        port: int = 0,
        callback_path: str = "/callback",
        open_browser: bool = True,
    ) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.port = port
        self.callback_path = callback_path
        self.open_browser = open_browser

    def login(self) -> CloudTokenSet:
        """Run full browser login: listen → authorize → exchange → teardown."""
        pkce = generate_pkce()
        state = generate_state()
        server = LoopbackServer(
            port=self.port,
            path=self.callback_path,
            timeout_seconds=self.timeout_seconds,
            expected_state=state,
        )
        server.start()
        redirect_uri = server.redirect_uri

        try:
            self.provider.prepare(redirect_uri=redirect_uri)
            authorize_url = self.provider.build_authorize_url(
                redirect_uri=redirect_uri,
                state=state,
                pkce=pkce,
            )
            logger.info(
                "oauth_loopback_login_started",
                provider=self.provider.provider.value,
                redirect_uri=redirect_uri,
            )
            if self.open_browser:
                opened = open_authorize_url(authorize_url)
                if not opened:
                    logger.warning(
                        "oauth_browser_not_opened",
                        authorize_url=authorize_url,
                        hint="Open the authorize URL manually in a browser",
                    )
            else:
                logger.info("oauth_authorize_url", url=authorize_url)

            result = server.wait()
            if result.error:
                raise OAuthProviderDeniedError(
                    result.error_description or result.error,
                    error=result.error,
                )
            if result.state != state:
                raise OAuthStateMismatchError()
            if not result.code:
                raise OAuthProviderDeniedError("Missing authorization code", error="missing_code")

            token_set = self.provider.exchange_code(
                code=result.code,
                redirect_uri=redirect_uri,
                pkce=pkce,
            )
            logger.info(
                "oauth_loopback_login_succeeded",
                provider=token_set.provider.value,
                subject=token_set.subject,
                email=token_set.email,
                expires_at=token_set.expires_at.isoformat() if token_set.expires_at else None,
                can_refresh=token_set.can_refresh,
            )
            return token_set
        finally:
            server.shutdown()
            self.provider.cleanup()


def login_with_provider(
    provider: CloudOAuthProviderBase,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    port: int = 0,
    open_browser: bool = True,
) -> CloudTokenSet:
    """Convenience wrapper around ``OAuthLoopbackClient.login``."""
    return OAuthLoopbackClient(
        provider,
        timeout_seconds=timeout_seconds,
        port=port,
        open_browser=open_browser,
    ).login()

"""Shared types for multi-cloud OAuth loopback login."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CloudOAuthProvider(str, Enum):
    """Interactive cloud identity providers (user login, not keyless WIF)."""

    GCP = "gcp"
    AWS = "aws"
    AZURE = "azure"


class OAuthLoopbackError(Exception):
    """Base error for loopback OAuth failures."""

    def __init__(self, message: str, *, code: str = "oauth_error") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OAuthTimeoutError(OAuthLoopbackError):
    def __init__(self, message: str = "Timed out waiting for OAuth callback") -> None:
        super().__init__(message, code="timeout")


class OAuthStateMismatchError(OAuthLoopbackError):
    def __init__(self, message: str = "OAuth state mismatch") -> None:
        super().__init__(message, code="state_mismatch")


class OAuthProviderDeniedError(OAuthLoopbackError):
    def __init__(self, message: str, *, error: str | None = None) -> None:
        super().__init__(message, code=error or "access_denied")


class OAuthTokenExchangeError(OAuthLoopbackError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="token_exchange_failed")


class AuthCodeResult(BaseModel):
    """Captured authorization response from the loopback redirect."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None


class CloudTokenSet(BaseModel):
    """Provider-normalized token payload for uniform inspection."""

    model_config = ConfigDict(extra="forbid")

    provider: CloudOAuthProvider
    access_token: str
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    refresh_token: str | None = None
    id_token: str | None = None
    scope: str | None = None
    # Identity hints (best-effort; never required for token use)
    subject: str | None = None
    email: str | None = None
    # Provider-specific extras (account ids, start URL, region, tenant, etc.)
    claims: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expires_at", mode="before")
    @classmethod
    def _ensure_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def can_refresh(self) -> bool:
        return bool(self.refresh_token)

    def seconds_until_expiry(self) -> float | None:
        if self.expires_at is None:
            return None
        return (self.expires_at - datetime.now(timezone.utc)).total_seconds()


def expires_at_from_expires_in(expires_in: int | None) -> datetime | None:
    if expires_in is None:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

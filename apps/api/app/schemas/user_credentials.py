"""User account cloud credential vault (encrypted at rest)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.cloud import CloudCredentials


class UserCloudCredentialsStatus(BaseModel):
    """Safe summary — never includes secret values."""

    has_gcp: bool = False
    has_aws: bool = False
    has_azure: bool = False
    has_cloudflare: bool = False
    gcp_label: str | None = None
    aws_label: str | None = None
    azure_label: str | None = None
    cloudflare_label: str | None = None
    updated_at: datetime | None = None


class UserCloudCredentialsUpdate(BaseModel):
    """Partial update — empty fields leave existing secrets unchanged."""

    credentials: CloudCredentials = Field(default_factory=CloudCredentials)
    clear_gcp: bool = False
    clear_aws: bool = False
    clear_azure: bool = False
    clear_cloudflare: bool = False

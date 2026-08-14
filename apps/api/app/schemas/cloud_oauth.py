"""Interactive cloud OAuth (Settings → Connect Google / AWS / Azure)."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class CloudOAuthProviderName(str, Enum):
    GCP = "gcp"
    AWS = "aws"
    AZURE = "azure"


class CloudOAuthStartRequest(BaseModel):
    provider: CloudOAuthProviderName
    # AWS IAM Identity Center
    aws_start_url: str | None = Field(default=None, max_length=512)
    aws_region: str | None = Field(default=None, max_length=64)
    aws_account_id: str | None = Field(default=None, max_length=32)
    aws_role_name: str | None = Field(default=None, max_length=128)
    # Azure Entra
    azure_tenant_id: str | None = Field(default=None, max_length=128)
    azure_subscription_id: str | None = Field(default=None, max_length=64)


class CloudOAuthSessionStatus(BaseModel):
    session_id: str
    provider: CloudOAuthProviderName
    status: Literal["pending", "succeeded", "failed"]
    message: str | None = None
    email: str | None = None
    label: str | None = None


class CloudOAuthCapabilities(BaseModel):
    """Which Connect buttons the API can offer (configured OAuth clients)."""

    gcp: bool = False
    aws: bool = True  # dynamic RegisterClient; only needs start URL + region from user
    azure: bool = False
    note: str = (
        "Interactive login opens a browser on the API host "
        "(use when Launchpad API runs on your machine)."
    )

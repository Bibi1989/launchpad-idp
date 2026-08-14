"""User account cloud credential vault (encrypted at rest)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.cloud import CloudCredentials


class UserCloudCredentialsStatus(BaseModel):
    """Safe summary - never includes secret values."""

    has_gcp: bool = False
    has_aws: bool = False
    has_azure: bool = False
    has_cloudflare: bool = False
    has_gcp_sa: bool = False
    has_gcp_oauth: bool = False
    gcp_label: str | None = None
    aws_label: str | None = None
    azure_label: str | None = None
    cloudflare_label: str | None = None
    gcp_project_id: str | None = None
    gcp_region: str | None = None
    aws_region: str | None = None
    azure_location: str | None = None
    updated_at: datetime | None = None


class UserCloudCredentialsUpdate(BaseModel):
    """Partial update - empty fields leave existing secrets unchanged."""

    credentials: CloudCredentials = Field(default_factory=CloudCredentials)
    clear_gcp: bool = False
    clear_aws: bool = False
    clear_azure: bool = False
    clear_cloudflare: bool = False


class CloudNetworkOption(BaseModel):
    """A VPC / VPC network the user can attach a preview to."""

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    cidr: str | None = None
    is_default: bool = False
    region: str | None = None


class CloudNetworkListResponse(BaseModel):
    provider: str
    region: str | None = None
    networks: list[CloudNetworkOption] = Field(default_factory=list)


class CloudSecurityGroupOption(BaseModel):
    """An AWS security group the user can attach to a preview EC2 instance."""

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    vpc_id: str | None = None
    description: str | None = None
    region: str | None = None


class CloudSecurityGroupListResponse(BaseModel):
    provider: str
    region: str | None = None
    vpc_id: str | None = None
    security_groups: list[CloudSecurityGroupOption] = Field(default_factory=list)

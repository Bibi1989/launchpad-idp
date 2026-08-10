"""Schemas for org-scoped Slack and Jira integrations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class SlackIntegrationStatus(BaseModel):
    connected: bool = False
    notify_ready: bool = True
    notify_failed: bool = True
    notify_ttl_warning: bool = True
    notify_cost_cap: bool = True
    project_ids: list[UUID] = Field(default_factory=list)
    webhook_configured: bool = False
    updated_at: datetime | None = None


class SlackIntegrationUpdate(BaseModel):
    webhook_url: str | None = Field(default=None, max_length=2048)
    notify_ready: bool | None = None
    notify_failed: bool | None = None
    notify_ttl_warning: bool | None = None
    notify_cost_cap: bool | None = None
    project_ids: list[UUID] | None = None
    clear_webhook: bool = False

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        if not trimmed.startswith("https://hooks.slack.com/"):
            raise ValueError("webhook_url must be a Slack Incoming Webhook URL")
        return trimmed


class JiraIntegrationStatus(BaseModel):
    connected: bool = False
    site_url: str | None = None
    email: str | None = None
    project_key: str | None = None
    issue_type: str = "Bug"
    auto_create_on_failure: bool = False
    token_configured: bool = False
    updated_at: datetime | None = None


class JiraIntegrationUpdate(BaseModel):
    site_url: str | None = Field(default=None, max_length=512)
    email: str | None = Field(default=None, max_length=256)
    api_token: str | None = Field(default=None, max_length=512)
    project_key: str | None = Field(default=None, max_length=64)
    issue_type: str | None = Field(default=None, max_length=64)
    auto_create_on_failure: bool | None = None
    clear: bool = False

    @field_validator("site_url")
    @classmethod
    def validate_site_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip().rstrip("/")
        if not trimmed:
            return None
        if not (trimmed.startswith("https://") or trimmed.startswith("http://")):
            raise ValueError("site_url must start with https://")
        return trimmed

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        if "@" not in trimmed:
            raise ValueError("email must be a valid Atlassian account email")
        return trimmed

    @field_validator("project_key")
    @classmethod
    def validate_project_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip().upper()
        return trimmed or None

    @field_validator("api_token")
    @classmethod
    def validate_api_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class JiraIssueCreateRequest(BaseModel):
    summary: str | None = Field(default=None, max_length=255)
    link_only_key: str | None = Field(default=None, max_length=64)

    @field_validator("link_only_key")
    @classmethod
    def validate_link_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip().upper()
        return trimmed or None


class JiraIssueRead(BaseModel):
    issue_key: str
    issue_url: str
    created: bool = True

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


ServiceTier = Literal["critical", "tier-1", "tier-2", "tier-3"]


class GoldenPathTemplateRead(BaseModel):
    id: str
    version: str
    title: str
    description: str
    icon: str
    stack: str
    frameworks: list[str]
    docker_images: list[str] = Field(default_factory=list)
    default_tier: ServiceTier
    default_slo: str
    listen_port: int
    tags: list[str]
    includes_dockerfile: bool
    includes_k8s: bool
    includes_cicd: bool
    includes_iac: bool
    enable_postgres: bool = False
    enable_redis: bool = False


class ScorecardItem(BaseModel):
    id: str
    title: str
    passed: bool
    points: int
    max_points: int
    detail: str


class ServiceScorecard(BaseModel):
    score: int = Field(ge=0, le=100)
    gate: int = 70
    passed: bool
    items: list[ScorecardItem]


class CatalogServiceCreate(BaseModel):
    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    description: str = Field(default="", max_length=500)
    template_id: str = Field(min_length=1, max_length=64)
    owner: str = Field(min_length=1, max_length=128, description="Team or email owning the service")
    tier: ServiceTier = "tier-2"
    slo_target: str = Field(default="99.5", max_length=16)
    runbook_url: str | None = Field(default=None, max_length=512)
    on_call: str | None = Field(default=None, max_length=128)
    vcs_provider: Literal["none", "github", "gitlab"] = "github"
    create_github_repo: bool = False
    github_installation_id: int | None = Field(default=None, ge=1)
    github_organization: str | None = Field(default=None, max_length=100)
    github_private: bool = True
    gitlab_project_name: str | None = Field(default=None, max_length=128)
    gitlab_private: bool = True
    enforce_scorecard_gate: bool = True
    trigger_initial_preview: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower().replace("_", "-")
        return value

    @field_validator("owner", "on_call")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class CatalogServiceUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    owner: str | None = Field(default=None, min_length=1, max_length=128)
    tier: ServiceTier | None = None
    slo_target: str | None = Field(default=None, max_length=16)
    runbook_url: str | None = Field(default=None, max_length=512)
    on_call: str | None = Field(default=None, max_length=128)

    @field_validator("owner", "on_call")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class CatalogServiceRead(BaseModel):
    id: UUID
    name: str
    description: str
    owner: str
    tier: ServiceTier
    slo_target: str
    runbook_url: str | None
    on_call: str | None
    template_id: str
    template_version: str
    repository_url: str | None
    workspace_id: UUID | None
    compliance_score: int
    scorecard: ServiceScorecard
    org_id: UUID | None
    initial_preview_id: UUID | None = None
    initial_preview_url: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

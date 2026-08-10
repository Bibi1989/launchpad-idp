"""API schemas for repository import → detect → save workspace."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from pkg.detector.models import DetectedService, DetectionResult, ProjectLayout


class RepoImportCreateRequest(BaseModel):
    git_repo_url: str = Field(min_length=8, max_length=512)
    git_branch: str = Field(default="main", min_length=1, max_length=256)
    # Optional installation token is resolved server-side from GitHub App when omitted.
    use_github_app_token: bool = True
    # When multiple App installs exist (personal + org), client must pass the chosen one.
    github_installation_id: int | None = Field(default=None, ge=1)

    @field_validator("git_repo_url")
    @classmethod
    def normalize_repo_url(cls, value: str) -> str:
        cleaned = value.strip()
        lower = cleaned.lower()
        if not (
            lower.startswith("https://")
            or lower.startswith("http://")
            or lower.startswith("git@")
            or lower.startswith("ssh://")
        ):
            raise ValueError("git_repo_url must be an http(s), git@, or ssh URL")
        if any(ch in cleaned for ch in (" ", "\n", "\r", "\t")):
            raise ValueError("git_repo_url contains invalid characters")
        return cleaned

    @field_validator("git_branch")
    @classmethod
    def normalize_branch(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(ch in cleaned for ch in (" ", "..", "\\")):
            raise ValueError("git_branch contains invalid characters")
        return cleaned


class ServiceOverride(BaseModel):
    id: str
    enabled: bool = True
    port: int | None = Field(default=None, ge=1, le=65535)
    is_preview_target: bool = False
    name: str | None = Field(default=None, max_length=64)


class RepoImportSaveRequest(BaseModel):
    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    services: list[ServiceOverride] = Field(default_factory=list)
    ensure_local_cluster: bool = True
    runtime_mode: str = Field(default="kubernetes", max_length=32)
    iac_engine: str = Field(default="terraform", max_length=32)
    enable_iac: bool = True
    enable_cicd: bool = False
    cicd_platform: str = Field(default="github", max_length=16)
    project_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("runtime_mode")
    @classmethod
    def normalize_runtime_mode(cls, value: str) -> str:
        cleaned = value.strip().lower()
        allowed = {"kubernetes", "docker_compose", "running_instance"}
        if cleaned not in allowed:
            raise ValueError(f"runtime_mode must be one of {sorted(allowed)}")
        return cleaned

    @field_validator("iac_engine")
    @classmethod
    def normalize_iac_engine(cls, value: str) -> str:
        cleaned = value.strip().lower()
        allowed = {"terraform", "opentofu", "pulumi", "ansible"}
        if cleaned not in allowed:
            raise ValueError(f"iac_engine must be one of {sorted(allowed)}")
        return cleaned

    @field_validator("cicd_platform")
    @classmethod
    def normalize_cicd_platform(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"github", "gitlab"}:
            raise ValueError("cicd_platform must be github or gitlab")
        return cleaned


class RepoImportSessionRead(BaseModel):
    import_id: str
    git_repo_url: str
    git_branch: str
    commit_sha: str
    layout: ProjectLayout
    detection: DetectionResult
    services: list[DetectedService]
    created_at: datetime | None = None


class RepoImportSaveResult(BaseModel):
    workspace_id: UUID
    name: str
    root_dir: str
    files: list[str]
    preview_service: str | None = None
    cluster_ready: bool = False
    message: str = ""

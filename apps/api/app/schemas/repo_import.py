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


class EnvVarOverride(BaseModel):
    """User-configured value for a key from ``.env.example`` (or injected datastore URL)."""

    key: str = Field(min_length=1, max_length=128)
    value: str = Field(default="", max_length=4096)


class DatastoreImportConfig(BaseModel):
    """Per-datastore placement chosen during import."""

    kind: str = Field(min_length=2, max_length=32)
    # in_cluster | external | skip
    placement: str = Field(default="in_cluster", max_length=32)
    connection_url: str | None = Field(default=None, max_length=2048)

    @field_validator("kind")
    @classmethod
    def normalize_kind(cls, value: str) -> str:
        cleaned = value.strip().lower()
        allowed = {"postgres", "mysql", "mariadb", "mongodb", "redis"}
        if cleaned not in allowed:
            raise ValueError(f"kind must be one of {sorted(allowed)}")
        return cleaned

    @field_validator("placement")
    @classmethod
    def normalize_placement(cls, value: str) -> str:
        cleaned = value.strip().lower()
        allowed = {"in_cluster", "external", "skip"}
        if cleaned not in allowed:
            raise ValueError(f"placement must be one of {sorted(allowed)}")
        return cleaned


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
    env_vars: list[EnvVarOverride] = Field(default_factory=list, max_length=200)
    datastores: list[DatastoreImportConfig] = Field(default_factory=list, max_length=10)
    # running_instance process plan (ignored unless runtime_mode=running_instance)
    process_strategy: str = Field(default="docker", max_length=32)
    reverse_proxy: str = Field(default="none", max_length=32)

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

    @field_validator("process_strategy")
    @classmethod
    def normalize_process_strategy(cls, value: str) -> str:
        cleaned = value.strip().lower()
        allowed = {"docker", "systemd", "pm2"}
        if cleaned not in allowed:
            raise ValueError(f"process_strategy must be one of {sorted(allowed)}")
        return cleaned

    @field_validator("reverse_proxy")
    @classmethod
    def normalize_reverse_proxy(cls, value: str) -> str:
        cleaned = value.strip().lower()
        allowed = {"none", "nginx", "caddy"}
        if cleaned not in allowed:
            raise ValueError(f"reverse_proxy must be one of {sorted(allowed)}")
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
    # kind → {in_cluster, external} suggested connection strings
    datastore_suggestions: dict[str, dict[str, str]] = Field(default_factory=dict)


class RepoImportSaveResult(BaseModel):
    workspace_id: UUID
    name: str
    root_dir: str
    files: list[str]
    preview_service: str | None = None
    cluster_ready: bool = False
    message: str = ""

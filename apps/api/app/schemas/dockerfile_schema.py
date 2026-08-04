"""Dockerfile scan, AI security review, push, and registry build schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProjectStack(str, Enum):
    REACT_VITE = "react_vite"
    NEXTJS = "nextjs"
    NUXTJS = "nuxtjs"
    VUEJS = "vuejs"
    SVELTE = "svelte"
    ANGULAR = "angular"
    FASTAPI = "fastapi"
    FLASK = "flask"
    DJANGO = "django"
    EXPRESS = "express"
    NESTJS = "nestjs"
    SPRINGBOOT = "springboot"
    DOTNET = "dotnet"
    NODE = "node"
    PYTHON = "python"
    GO = "go"
    JAVA = "java"
    RUST = "rust"
    GENERIC = "generic"
    UNKNOWN = "unknown"


class DockerfileSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RegistryProvider(str, Enum):
    DOCKER_HUB = "docker_hub"
    AWS_ECR = "aws_ecr"
    GCP_ARTIFACT_REGISTRY = "gcp_artifact_registry"


class DockerfileSecurityIssue(BaseModel):
    ruleId: str = Field(description="e.g., RUN_AS_ROOT, UNPINNED_BASE_IMAGE, LEAKED_SECRET")
    severity: DockerfileSeverity
    description: str
    lineNumber: int | None = None


class DockerfileSecurityReport(BaseModel):
    """Structured Gemini output - camelCase for UI consumption."""

    summary: str
    securityIssues: list[DockerfileSecurityIssue] = Field(default_factory=list)
    hasMultiStage: bool
    improvedDockerfile: str
    explanationOfChanges: list[str] = Field(default_factory=list)
    analysisSource: str = Field(
        default="gemini",
        description="gemini | heuristic",
    )


class DockerfileScanRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    installation_id: int = Field(..., ge=1)
    full_name: str = Field(..., min_length=3, max_length=200, pattern=r"^[^/\s]+/[^/\s]+$")
    ref: str | None = Field(default=None, max_length=200)


class DetectedDockerfile(BaseModel):
    path: str
    content: str
    size_bytes: int
    sha: str | None = None


class DockerfileScanResponse(BaseModel):
    full_name: str
    ref: str
    dockerfiles: list[DetectedDockerfile]
    detected_stack: ProjectStack
    scaffold_suggested: bool
    root_markers: list[str] = Field(default_factory=list)


class DockerfileScaffoldRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    installation_id: int | None = Field(default=None, ge=1)
    full_name: str | None = Field(default=None, max_length=200)
    stack: ProjectStack | None = None
    ref: str | None = Field(default=None, max_length=200)
    app_name: str | None = Field(default=None, max_length=100)
    listen_port: int = Field(default=8080, ge=1, le=65535)


class DockerfileScaffoldResponse(BaseModel):
    stack: ProjectStack
    path: str
    content: str
    detected_from: list[str] = Field(default_factory=list)


class DockerfileReviewRequest(BaseModel):
    dockerfile_content: str = Field(..., min_length=1, max_length=200_000)
    stack: ProjectStack | None = None
    source_path: str | None = Field(default=None, max_length=500)


class DockerfileReviewResponse(BaseModel):
    report: DockerfileSecurityReport
    source_path: str | None = None


class DockerfilePushRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    installation_id: int = Field(..., ge=1)
    full_name: str = Field(..., min_length=3, max_length=200, pattern=r"^[^/\s]+/[^/\s]+$")
    dockerfile_content: str = Field(..., min_length=1, max_length=200_000)
    path: str = Field(
        default="dockers/Dockerfile",
        max_length=500,
        description="Repo-relative path; must live under dockers/",
    )
    commit_message: str = Field(
        default="chore: add Launchpad-hardened Dockerfile under dockers/",
        min_length=1,
        max_length=500,
    )
    branch: str | None = Field(default=None, max_length=200)


class DockerfilePushResponse(BaseModel):
    full_name: str
    html_url: str
    default_branch: str
    path: str
    commit_message: str
    installation_id: int


class RepoScaffoldFile(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1, max_length=400_000)


class RepoPushBundleRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    installation_id: int = Field(..., ge=1)
    full_name: str = Field(..., min_length=3, max_length=200, pattern=r"^[^/\s]+/[^/\s]+$")
    files: list[RepoScaffoldFile] = Field(..., min_length=1, max_length=80)
    commit_message: str = Field(
        default="chore: add Launchpad infra scaffold",
        min_length=1,
        max_length=500,
    )
    branch: str | None = Field(default=None, max_length=200)


class RepoPushBundleResponse(BaseModel):
    full_name: str
    html_url: str
    default_branch: str
    paths: list[str]
    commit_message: str
    installation_id: int


class DockerHubCredentials(BaseModel):
    username: str = Field(..., min_length=1, max_length=200)
    password_or_token: str = Field(..., min_length=1, max_length=500)
    repository: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="e.g. myuser/myapp or org/myapp",
    )


class AwsEcrCredentials(BaseModel):
    access_key_id: str = Field(..., min_length=1, max_length=200)
    secret_access_key: str = Field(..., min_length=1, max_length=500)
    session_token: str | None = Field(default=None, max_length=2000)
    region: str = Field(..., min_length=2, max_length=32)
    account_id: str = Field(..., min_length=12, max_length=12, pattern=r"^\d{12}$")
    repository: str = Field(..., min_length=1, max_length=256)


class GcpArtifactRegistryCredentials(BaseModel):
    service_account_json: str = Field(..., min_length=2, max_length=50_000)
    project_id: str = Field(..., min_length=1, max_length=100)
    location: str = Field(..., min_length=2, max_length=64)
    repository: str = Field(..., min_length=1, max_length=100)
    image_name: str = Field(..., min_length=1, max_length=200)


class RegistryTarget(BaseModel):
    provider: RegistryProvider
    docker_hub: DockerHubCredentials | None = None
    aws_ecr: AwsEcrCredentials | None = None
    gcp_artifact_registry: GcpArtifactRegistryCredentials | None = None


class DockerfileBuildRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    installation_id: int = Field(..., ge=1)
    full_name: str = Field(..., min_length=3, max_length=200, pattern=r"^[^/\s]+/[^/\s]+$")
    dockerfile_path: str = Field(
        default="dockers/Dockerfile",
        min_length=1,
        max_length=500,
    )
    context_path: str = Field(default=".", max_length=500)
    branch: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Image tags (e.g. latest, v1.2.3, sha-abc1234)",
    )
    registry: RegistryTarget
    dockerfile_content_override: str | None = Field(
        default=None,
        max_length=200_000,
        description="Optional in-memory Dockerfile; skips reading path from repo",
    )


class DockerfileBuildJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DockerfileBuildEnqueueResponse(BaseModel):
    job_id: str
    status: DockerfileBuildJobStatus = DockerfileBuildJobStatus.QUEUED


class DockerfileBuildJobResponse(BaseModel):
    job_id: str
    status: DockerfileBuildJobStatus
    image_refs: list[str] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


# JSON Schema handed to Gemini (camelCase property names matching UI contract).
DOCKERFILE_SECURITY_REPORT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Brief executive summary of Dockerfile security findings.",
        },
        "securityIssues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ruleId": {
                        "type": "string",
                        "description": (
                            "Stable rule id, e.g. RUN_AS_ROOT, UNPINNED_BASE_IMAGE, "
                            "LEAKED_SECRET, MISSING_HEALTHCHECK, LATEST_TAG"
                        ),
                    },
                    "severity": {
                        "type": "string",
                        "enum": [s.value for s in DockerfileSeverity],
                    },
                    "description": {"type": "string"},
                    "lineNumber": {
                        "type": "integer",
                        "description": "1-based line number when applicable",
                    },
                },
                "required": ["ruleId", "severity", "description"],
            },
            "description": "Ordered list of security findings.",
        },
        "hasMultiStage": {
            "type": "boolean",
            "description": "Whether the input Dockerfile already uses multi-stage builds.",
        },
        "improvedDockerfile": {
            "type": "string",
            "description": (
                "Complete refactored multi-stage Dockerfile following 2026 best practices: "
                "non-root USER 10001, minimal alpine/distroless bases, no secrets, "
                "layer cache optimization."
            ),
        },
        "explanationOfChanges": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Human-readable list of hardening changes applied.",
        },
    },
    "required": [
        "summary",
        "securityIssues",
        "hasMultiStage",
        "improvedDockerfile",
        "explanationOfChanges",
    ],
}

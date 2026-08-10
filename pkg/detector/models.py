"""Project and monorepo detection models for repository imports."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ServiceRole(str, Enum):
    WEB = "web"
    API = "api"
    WORKER = "worker"
    UNKNOWN = "unknown"


class MonorepoTool(str, Enum):
    PNPM = "pnpm"
    LERNA = "lerna"
    TURBO = "turbo"
    NX = "nx"
    NPM_WORKSPACES = "npm_workspaces"
    CARGO = "cargo"
    GO_WORK = "go_work"
    MAKE = "make"
    NONE = "none"


class ProjectLayout(str, Enum):
    MONOREPO = "monorepo"
    SINGLE = "single"


class DetectedService(BaseModel):
    """One runnable package/app discovered in a cloned repository."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64, description="K8s-safe service name, e.g. launch-web")
    path: str = Field(description="Path relative to repo root")
    role: ServiceRole = ServiceRole.UNKNOWN
    framework: str = Field(default="generic", max_length=64)
    runtime: str = Field(default="unknown", max_length=32)
    port: int = Field(default=8080, ge=1, le=65535)
    has_dockerfile: bool = False
    dockerfile_path: str | None = None
    env_hints: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    is_preview_target: bool = False
    health_path: str = "/"
    markers: list[str] = Field(default_factory=list)


class EnvExampleVar(BaseModel):
    """One key from ``.env.example`` (or similar) for import-time configuration."""

    key: str = Field(min_length=1, max_length=128)
    example_value: str = Field(default="", max_length=4096)
    suggested_value: str = Field(default="", max_length=4096)
    comment: str | None = Field(default=None, max_length=512)
    source: str = Field(default=".env.example", max_length=256)
    is_secret: bool = False


class DetectionResult(BaseModel):
    """Full scan result for a cloned repository."""

    layout: ProjectLayout
    monorepo_tools: list[MonorepoTool] = Field(default_factory=list)
    services: list[DetectedService] = Field(default_factory=list)
    datastores: list[str] = Field(default_factory=list)
    root_markers: list[str] = Field(default_factory=list)
    package_globs: list[str] = Field(default_factory=list)
    summary: str = ""
    # Runtime hints for import UX (user may still pick any mode).
    has_kubernetes: bool = False
    has_compose: bool = False
    env_example: list[EnvExampleVar] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if not self.summary:
            n = len(self.services)
            tools = ", ".join(t.value for t in self.monorepo_tools if t != MonorepoTool.NONE) or "none"
            hints: list[str] = []
            if self.has_kubernetes:
                hints.append("kubernetes manifests found")
            if self.has_compose:
                hints.append("docker compose found")
            hint_suffix = f" · {'; '.join(hints)}" if hints else ""
            self.summary = (
                f"{self.layout.value} · {n} service(s) · monorepo tools: {tools}"
                + (f" · datastores: {', '.join(self.datastores)}" if self.datastores else "")
                + hint_suffix
            )

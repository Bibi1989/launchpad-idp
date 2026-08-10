"""Schemas for AI-assisted Ansible file refinement."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class AnsibleFilePatch(BaseModel):
    path: str = Field(min_length=3, max_length=512)
    content: str = Field(max_length=200_000)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        cleaned = value.strip().replace("\\", "/")
        if not cleaned or ".." in cleaned.split("/"):
            raise ValueError("invalid path")
        return cleaned


class AnsibleRefineRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    workspace_name: str = Field(default="launchpad-workspace", max_length=64)
    app_deploy_mode: str = Field(default="docker_run", max_length=32)
    reverse_proxy: str = Field(default="none", max_length=32)
    files: list[AnsibleFilePatch] = Field(default_factory=list, max_length=40)

    @field_validator("prompt")
    @classmethod
    def strip_prompt(cls, value: str) -> str:
        return value.strip()

    @field_validator("app_deploy_mode")
    @classmethod
    def normalize_mode(cls, value: str) -> str:
        cleaned = value.strip().lower()
        allowed = {"docker_run", "docker_compose", "systemd", "pm2", "none"}
        if cleaned not in allowed:
            raise ValueError(f"app_deploy_mode must be one of {sorted(allowed)}")
        return cleaned

    @field_validator("reverse_proxy")
    @classmethod
    def normalize_proxy(cls, value: str) -> str:
        cleaned = value.strip().lower()
        allowed = {"none", "nginx", "caddy"}
        if cleaned not in allowed:
            raise ValueError(f"reverse_proxy must be one of {sorted(allowed)}")
        return cleaned


class AnsibleRefineResponse(BaseModel):
    files: list[AnsibleFilePatch]
    summary: str = ""
    source: str = "heuristic"
    gemini_configured: bool = False

"""Schemas for environment metrics and health-ping observability."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class EnvironmentMetricsRead(BaseModel):
    environment_id: UUID
    name: str
    status: str
    namespace_name: str
    cpu_cores: float = 0.0
    memory_gib: float = 0.0
    cpu_percent: float | None = None
    memory_percent: float | None = None
    source: str | None = None
    available: bool = False
    detail: str | None = None
    sampled_at: datetime


class EnvironmentHealthPingRead(BaseModel):
    environment_id: UUID
    name: str
    status: str
    ok: bool
    status_code: int | None = None
    message: str
    preview_url: str | None = None
    latency_ms: float | None = None
    checked_at: datetime


class EnvironmentObservabilityItem(BaseModel):
    environment_id: UUID
    name: str
    status: str
    provider: str | None = None
    deploy_mode: str | None = None
    app_ready: bool = False
    preview_url: str | None = None
    metrics: EnvironmentMetricsRead
    health: EnvironmentHealthPingRead


class EnvironmentObservabilitySummary(BaseModel):
    items: list[EnvironmentObservabilityItem] = Field(default_factory=list)
    healthy_count: int = 0
    unhealthy_count: int = 0
    unknown_count: int = 0
    sampled_at: datetime

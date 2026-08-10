"""Schemas for hybrid local/edge agent nodes.

Covers the full lifecycle: operator enrollment (install token), agent
self-registration, heartbeat telemetry, and the command protocol dispatched
over the reverse WebSocket tunnel.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NodeStatus(str, Enum):
    PENDING = "PENDING"
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    REVOKED = "REVOKED"


# --------------------------------------------------------------------------- #
# Enrollment + registration
# --------------------------------------------------------------------------- #


class NodeEnrollRequest(BaseModel):
    """Operator creates a deployment target and receives a one-line installer."""

    name: str = Field(min_length=2, max_length=128)
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name is required")
        return cleaned


class NodeInstallInstructions(BaseModel):
    """Everything needed to install the agent on a homelab host."""

    node_id: UUID
    name: str
    token: str = Field(description="Single-use enrollment token (lp_...). Shown once.")
    expires_at: datetime
    control_plane_url: str
    agent_ws_url: str
    install_command: str = Field(description="curl | sh one-liner for the host")


class NodeRegisterRequest(BaseModel):
    """Sent by the agent daemon on first boot, authenticated by the install token."""

    enrollment_token: str = Field(min_length=8, max_length=128)
    hostname: str | None = Field(default=None, max_length=253)
    platform: str | None = Field(default=None, max_length=64)
    agent_version: str | None = Field(default=None, max_length=32)
    cpu_cores: int | None = Field(default=None, ge=1, le=1024)
    mem_total_mb: int | None = Field(default=None, ge=1)


class NodeCredentials(BaseModel):
    """Long-lived credential material returned to the agent after registration."""

    node_id: UUID
    agent_secret: str = Field(description="Per-node HMAC secret. Store securely; shown once.")
    agent_ws_url: str
    heartbeat_interval_seconds: int


# --------------------------------------------------------------------------- #
# Telemetry
# --------------------------------------------------------------------------- #


class ContainerSummary(BaseModel):
    id: str
    name: str
    image: str
    status: str
    ports: list[str] = Field(default_factory=list)


class NodeTelemetry(BaseModel):
    """Heartbeat payload pushed by the agent every ``heartbeat_interval_seconds``."""

    cpu_percent: float = Field(ge=0, le=100)
    mem_percent: float = Field(ge=0, le=100)
    disk_percent: float = Field(ge=0, le=100)
    docker_status: str = Field(default="unknown", max_length=32)
    cpu_cores: int | None = Field(default=None, ge=1, le=1024)
    mem_total_mb: int | None = Field(default=None, ge=1)
    containers: list[ContainerSummary] = Field(default_factory=list)


class NodeRead(BaseModel):
    """Node view for the dashboard fleet list."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: NodeStatus
    online: bool = False
    labels: dict[str, str] = Field(default_factory=dict)
    hostname: str | None = None
    platform: str | None = None
    agent_version: str | None = None
    cpu_cores: int | None = None
    mem_total_mb: int | None = None
    last_heartbeat_at: datetime | None = None
    cpu_percent: Decimal | None = None
    mem_percent: Decimal | None = None
    disk_percent: Decimal | None = None
    docker_status: str | None = None
    containers: list[ContainerSummary] = Field(default_factory=list)
    created_at: datetime


# --------------------------------------------------------------------------- #
# Command protocol (control plane -> agent)
# --------------------------------------------------------------------------- #


class NodeCommandAction(str, Enum):
    PULL_IMAGE = "pull_image"
    RUN_CONTAINER = "run_container"
    STOP_CONTAINER = "stop_container"
    RESTART_CONTAINER = "restart_container"
    COLLECT_LOGS = "collect_logs"
    LIST_CONTAINERS = "list_containers"


class PortMapping(BaseModel):
    container_port: int = Field(ge=1, le=65535)
    host_port: int = Field(ge=1, le=65535)
    protocol: str = Field(default="tcp", pattern=r"^(tcp|udp)$")


class VolumeMount(BaseModel):
    host_path: str = Field(min_length=1, max_length=512)
    container_path: str = Field(min_length=1, max_length=512)
    mode: str = Field(default="rw", pattern=r"^(rw|ro)$")


class RunContainerSpec(BaseModel):
    image: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    ports: list[PortMapping] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    volumes: list[VolumeMount] = Field(default_factory=list)
    cpu_limit: float | None = Field(default=None, gt=0, le=1024)
    memory_mb: int | None = Field(default=None, ge=16)
    restart_policy: str = Field(default="unless-stopped", max_length=32)
    command: str | None = Field(default=None, max_length=2048)
    pull: bool = True


class ContainerRef(BaseModel):
    container: str = Field(min_length=1, max_length=256)


class PullImageSpec(BaseModel):
    image: str = Field(min_length=1, max_length=512)


class CollectLogsSpec(BaseModel):
    container: str = Field(min_length=1, max_length=256)
    tail: int = Field(default=200, ge=1, le=5000)


class NodeCommandRequest(BaseModel):
    """REST body for POST /nodes/{id}/commands.

    Exactly one of the ``*`` payload fields is required, matched to ``action``.
    """

    action: NodeCommandAction
    run: RunContainerSpec | None = None
    pull: PullImageSpec | None = None
    ref: ContainerRef | None = None
    logs: CollectLogsSpec | None = None


class NodeCommandResult(BaseModel):
    command_id: str
    action: NodeCommandAction
    ok: bool
    detail: str = ""
    data: dict = Field(default_factory=dict)

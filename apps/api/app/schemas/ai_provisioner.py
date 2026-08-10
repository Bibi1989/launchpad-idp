"""Schemas for the AI Infrastructure Provisioner.

A natural-language prompt is translated into a structured, guardrailed
``InfraBlueprint``. The blueprint is provider-neutral: it maps to Docker run
commands on a homelab agent node, or to a ``ProvisioningWizardRequest`` that
the existing IaC generator renders for a cloud target.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class BlueprintTarget(str, Enum):
    """Where a blueprint is provisioned.

    ``local_node`` dispatches Docker commands to a registered agent; the cloud
    targets render IaC via the existing provisioning pipeline.
    """

    LOCAL_NODE = "local_node"
    GCP = "gcp"
    AWS = "aws"
    AZURE = "azure"


class ServiceKind(str, Enum):
    WEB = "web"
    WORKER = "worker"
    DATASTORE = "datastore"
    CACHE = "cache"


class BlueprintPort(BaseModel):
    container_port: int = Field(ge=1, le=65535)
    host_port: int = Field(ge=1, le=65535)
    protocol: str = Field(default="tcp", pattern=r"^(tcp|udp)$")


class BlueprintVolume(BaseModel):
    host_path: str = Field(min_length=1, max_length=512)
    container_path: str = Field(min_length=1, max_length=512)
    mode: str = Field(default="rw", pattern=r"^(rw|ro)$")


class InfraServiceSpec(BaseModel):
    """A single containerized workload in the blueprint."""

    name: str = Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9-]*$")
    image: str = Field(min_length=1, max_length=512)
    kind: ServiceKind = ServiceKind.WEB
    ports: list[BlueprintPort] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    volumes: list[BlueprintVolume] = Field(default_factory=list)
    cpu_limit: float = Field(default=0.5, gt=0, le=1024)
    memory_mb: int = Field(default=512, ge=16)
    replicas: int = Field(default=1, ge=1, le=50)
    persistent: bool = False
    command: str | None = Field(default=None, max_length=2048)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().lower()


class InfraBlueprint(BaseModel):
    """Structured infrastructure plan (the LLM/heuristic output)."""

    name: str = Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9-]*$")
    summary: str = Field(default="", max_length=1024)
    services: list[InfraServiceSpec] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().lower()


# --------------------------------------------------------------------------- #
# Validation, guardrails, cost
# --------------------------------------------------------------------------- #


class GuardrailSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class GuardrailViolation(BaseModel):
    code: str
    message: str
    severity: GuardrailSeverity = GuardrailSeverity.ERROR
    service: str | None = None


class BlueprintValidation(BaseModel):
    valid: bool
    adjusted: bool = False
    violations: list[GuardrailViolation] = Field(default_factory=list)


class CostLineItem(BaseModel):
    service: str
    cpu_usd: float
    memory_usd: float
    addon_usd: float
    hourly_usd: float


class CostEstimate(BaseModel):
    hourly_usd: float
    monthly_usd: float
    currency: str = "USD"
    self_hosted: bool = False
    breakdown: list[CostLineItem] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Requests / responses
# --------------------------------------------------------------------------- #


class BlueprintGenerateRequest(BaseModel):
    prompt: str = Field(min_length=4, max_length=4000)
    target: BlueprintTarget = BlueprintTarget.LOCAL_NODE
    node_id: UUID | None = Field(default=None, description="Required when target=local_node")
    region: str | None = Field(default=None, max_length=64)


class BlueprintFixRequest(BaseModel):
    """Repair a blueprint after a deploy failure (missing image tags, etc.)."""

    blueprint: InfraBlueprint
    error_log: str = Field(min_length=4, max_length=12000)
    prompt: str | None = Field(default=None, max_length=4000)
    target: BlueprintTarget = BlueprintTarget.LOCAL_NODE
    node_id: UUID | None = None
    region: str | None = Field(default=None, max_length=64)


class BlueprintGenerateResponse(BaseModel):
    blueprint: InfraBlueprint
    target: BlueprintTarget
    node_id: UUID | None = None
    source: str = Field(description="gemini | heuristic")
    validation: BlueprintValidation
    cost: CostEstimate


class BlueprintDeployRequest(BaseModel):
    blueprint: InfraBlueprint
    target: BlueprintTarget = BlueprintTarget.LOCAL_NODE
    node_id: UUID | None = None
    region: str | None = Field(default=None, max_length=64)


class DeployStepResult(BaseModel):
    step: str
    ok: bool
    detail: str = ""


class DeployedServiceLink(BaseModel):
    """A running (or rendered) service the operator can open after deploy."""

    name: str
    container_name: str
    host_port: int | None = None
    container_port: int | None = None
    url: str | None = None
    ok: bool = True


class BlueprintDeployResponse(BaseModel):
    deployment_id: str
    target: BlueprintTarget
    mode: str = Field(description="homelab_docker | iac")
    node_id: UUID | None = None
    node_name: str | None = None
    ok: bool
    steps: list[DeployStepResult] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    workspace_id: str | None = None
    view_path: str | None = Field(
        default=None,
        description="In-app path to inspect the deployment (workspace or fleet node).",
    )
    services: list[DeployedServiceLink] = Field(default_factory=list)


class AiProvisionerStatus(BaseModel):
    gemini_configured: bool
    model: str
    heuristic_fallback: bool

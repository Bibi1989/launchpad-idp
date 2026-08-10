"""AI Infrastructure Provisioner.

Translates a natural-language prompt into a structured, guardrailed
``InfraBlueprint`` using Gemini (structured JSON output), with a deterministic
keyword heuristic fallback so the feature works without an API key. The
blueprint maps to Docker run specs for a homelab node, or to a
``ProvisioningWizardRequest`` for cloud IaC rendering.

Mirrors the Gemini call pattern established in ``services/preview_analyzer.py``.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.ai_provisioner import (
    BlueprintFixRequest,
    BlueprintGenerateRequest,
    BlueprintGenerateResponse,
    BlueprintPort,
    BlueprintTarget,
    BlueprintValidation,
    BlueprintVolume,
    CostEstimate,
    CostLineItem,
    GuardrailSeverity,
    GuardrailViolation,
    InfraBlueprint,
    InfraServiceSpec,
    ServiceKind,
)
from app.schemas.nodes import (
    PortMapping,
    RunContainerSpec,
    VolumeMount,
)

logger = get_logger(__name__)

# JSON schema handed to Gemini for structured output (matches InfraBlueprint).
INFRA_BLUEPRINT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "services"],
    "properties": {
        "name": {"type": "string", "description": "kebab-case stack name"},
        "summary": {"type": "string"},
        "services": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "image", "kind"],
                "properties": {
                    "name": {"type": "string"},
                    "image": {"type": "string", "description": "docker image ref"},
                    "kind": {
                        "type": "string",
                        "enum": ["web", "worker", "datastore", "cache"],
                    },
                    "ports": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["container_port", "host_port"],
                            "properties": {
                                "container_port": {"type": "integer"},
                                "host_port": {"type": "integer"},
                                "protocol": {"type": "string", "enum": ["tcp", "udp"]},
                            },
                        },
                    },
                    "env": {"type": "object", "additionalProperties": {"type": "string"}},
                    "volumes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["host_path", "container_path"],
                            "properties": {
                                "host_path": {"type": "string"},
                                "container_path": {"type": "string"},
                                "mode": {"type": "string", "enum": ["rw", "ro"]},
                            },
                        },
                    },
                    "cpu_limit": {"type": "number", "description": "vCPU cores"},
                    "memory_mb": {"type": "integer"},
                    "replicas": {"type": "integer"},
                    "persistent": {"type": "boolean"},
                    "command": {"type": "string", "description": "optional container command override"},
                },
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}

_SYSTEM_INSTRUCTION = """You are the Launchpad AI Infrastructure Provisioner.
Translate the operator's natural-language request into a concrete container
deployment blueprint. Output ONLY structured JSON matching the schema.

Rules:
- Use real, well-known public Docker images that exist on Docker Hub today.
  Good examples: redis:7-alpine, postgres:16-alpine, nginx:1.27-alpine,
  tiangolo/uvicorn-gunicorn-fastapi:python3.11-slim, python:3.11-slim.
- NEVER use tiangolo/uvicorn-gunicorn-fastapi alpine tags (e.g. python3.10-alpine).
  Those tags were removed; use python3.11-slim or python3.10-slim instead.
- Do not invent registries or tags.
- Choose sensible defaults: cpu_limit in vCPU cores (e.g. 0.5), memory_mb, ports.
- Set kind to one of web, worker, datastore, cache.
- Mark datastores/caches persistent=true and give them a volume when appropriate.
- Keep names kebab-case. Prefer the smallest set of services that satisfies the request.
- For homelab/local targets keep total CPU <= 2 vCPU and memory modest."""

# Known-bad / deprecated image refs rewritten during guardrails.
_FASTAPI_BASE = "tiangolo/uvicorn-gunicorn-fastapi"
_FASTAPI_SAFE = f"{_FASTAPI_BASE}:python3.11-slim"
_IMAGE_EXACT_REWRITES: dict[str, str] = {
    f"{_FASTAPI_BASE}:python3.10-alpine": _FASTAPI_SAFE,
    f"{_FASTAPI_BASE}:python3.9-alpine": _FASTAPI_SAFE,
    f"{_FASTAPI_BASE}:python3.8-alpine": _FASTAPI_SAFE,
    f"{_FASTAPI_BASE}:python3.11-alpine": _FASTAPI_SAFE,
}


def sanitize_service_image(image: str) -> tuple[str, str | None]:
    """Rewrite known-broken Docker image refs. Returns (image, reason_or_None)."""
    raw = (image or "").strip()
    if not raw:
        return raw, None
    lowered = raw.lower()
    key = lowered.removeprefix("docker.io/")
    if key in _IMAGE_EXACT_REWRITES:
        return _IMAGE_EXACT_REWRITES[key], (
            f"Replaced unavailable image '{raw}' with '{_IMAGE_EXACT_REWRITES[key]}'."
        )
    if key.startswith(f"{_FASTAPI_BASE}:") and "alpine" in key:
        return _FASTAPI_SAFE, (
            f"Replaced unavailable alpine tag '{raw}' with '{_FASTAPI_SAFE}'."
        )
    return raw, None


class AiProvisionerService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def gemini_configured(self) -> bool:
        return bool(self._settings.gemini_api_key)

    # -- Generation -------------------------------------------------------- #

    async def generate(self, request: BlueprintGenerateRequest) -> BlueprintGenerateResponse:
        source = "heuristic"
        blueprint: InfraBlueprint | None = None
        if self.gemini_configured:
            try:
                blueprint = await asyncio.to_thread(self._generate_with_gemini, request.prompt)
                source = "gemini"
            except Exception:
                logger.exception("ai_provisioner_gemini_failed")
                if not self._settings.ai_provisioner_heuristic_fallback:
                    raise
        if blueprint is None:
            blueprint = self._heuristic_blueprint(request.prompt)

        return self._finalize(blueprint, request.target, request.node_id, source)

    async def fix(self, request: BlueprintFixRequest) -> BlueprintGenerateResponse:
        """Repair a failed blueprint using the deploy error (Gemini, else deterministic)."""
        source = "heuristic"
        blueprint: InfraBlueprint | None = None
        repair_prompt = self._build_fix_prompt(request)
        if self.gemini_configured:
            try:
                blueprint = await asyncio.to_thread(self._generate_with_gemini, repair_prompt)
                source = "gemini"
            except Exception:
                logger.exception("ai_provisioner_fix_gemini_failed")
                if not self._settings.ai_provisioner_heuristic_fallback:
                    raise
        if blueprint is None:
            blueprint = self._deterministic_fix(request.blueprint, request.error_log)
            source = "heuristic"
        return self._finalize(blueprint, request.target, request.node_id, source)

    def _finalize(
        self,
        blueprint: InfraBlueprint,
        target: BlueprintTarget,
        node_id,
        source: str,
    ) -> BlueprintGenerateResponse:
        adjusted, validation = self.validate_and_guardrail(blueprint, target)
        cost = self.estimate_cost(adjusted, target)
        return BlueprintGenerateResponse(
            blueprint=adjusted,
            target=target,
            node_id=node_id,
            source=source,
            validation=validation,
            cost=cost,
        )

    @staticmethod
    def _build_fix_prompt(request: BlueprintFixRequest) -> str:
        original = (request.prompt or request.blueprint.summary or "").strip()
        return (
            "The previous infrastructure blueprint failed to deploy. "
            "Return a corrected blueprint that fixes the error.\n\n"
            f"Original request:\n{original or '(none)'}\n\n"
            f"Current blueprint JSON:\n{request.blueprint.model_dump_json()}\n\n"
            f"Deployment error:\n{request.error_log.strip()}\n\n"
            "Fix rules:\n"
            "- Replace missing or invalid Docker image tags with real public tags.\n"
            "- Never use tiangolo/uvicorn-gunicorn-fastapi alpine tags; "
            "use python3.11-slim or python3.10-slim.\n"
            "- Prefer redis:7-alpine, postgres:16-alpine, nginx:1.27-alpine, "
            "tiangolo/uvicorn-gunicorn-fastapi:python3.11-slim.\n"
            "- Keep the same service intent; only change what is broken."
        )

    def _deterministic_fix(self, blueprint: InfraBlueprint, error_log: str) -> InfraBlueprint:
        """Rewrite known-bad images when Gemini is unavailable."""
        _ = error_log
        services: list[InfraServiceSpec] = []
        notes = list(blueprint.notes)
        notes.append("Repaired by deterministic image sanitizer (Gemini unavailable).")
        for svc in blueprint.services:
            fixed, reason = sanitize_service_image(svc.image)
            copy = svc.model_copy(update={"image": fixed})
            if reason:
                notes.append(reason)
            services.append(copy)
        return blueprint.model_copy(update={"services": services, "notes": notes})

    def _generate_with_gemini(self, prompt: str) -> InfraBlueprint:
        from google import genai
        from google.genai import types

        api_key = self._settings.gemini_api_key
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self._settings.gemini_model,
            contents=(
                "Design an infrastructure blueprint for this request:\n\n"
                f"{prompt.strip()}"
            ),
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                temperature=0.2,
                response_mime_type="application/json",
                response_json_schema=INFRA_BLUEPRINT_JSON_SCHEMA,
            ),
        )
        raw_text = (response.text or "").strip()
        if not raw_text:
            raise RuntimeError("Gemini returned an empty response")
        return InfraBlueprint.model_validate_json(raw_text)

    def _heuristic_blueprint(self, prompt: str) -> InfraBlueprint:
        """Keyword-driven blueprint so the feature works without an LLM key."""
        text = prompt.lower()
        services: list[InfraServiceSpec] = []
        notes = ["Generated by the deterministic heuristic (Gemini not configured or unavailable)."]

        if "redis" in text or "cache" in text:
            services.append(
                InfraServiceSpec(
                    name="redis",
                    image="redis:7-alpine",
                    kind=ServiceKind.CACHE,
                    ports=[BlueprintPort(container_port=6379, host_port=6379)],
                    cpu_limit=0.25,
                    memory_mb=self._mb_from_prompt(text, default=256),
                    persistent=True,
                    volumes=[BlueprintVolume(host_path="/var/lib/launchpad/redis", container_path="/data")],
                )
            )
        if "postgres" in text or "database" in text or "sql" in text:
            services.append(
                InfraServiceSpec(
                    name="postgres",
                    image="postgres:16-alpine",
                    kind=ServiceKind.DATASTORE,
                    ports=[BlueprintPort(container_port=5432, host_port=5432)],
                    env={"POSTGRES_PASSWORD": "change-me", "POSTGRES_DB": "app"},
                    cpu_limit=0.5,
                    memory_mb=512,
                    persistent=True,
                    volumes=[
                        BlueprintVolume(
                            host_path="/var/lib/launchpad/postgres",
                            container_path="/var/lib/postgresql/data",
                        )
                    ],
                )
            )
        if "fastapi" in text or "worker" in text or "api" in text or "app" in text:
            is_worker = "worker" in text and "api" not in text
            services.append(
                InfraServiceSpec(
                    name="worker" if is_worker else "api",
                    image="python:3.11-slim",
                    kind=ServiceKind.WORKER if is_worker else ServiceKind.WEB,
                    ports=[] if is_worker else [BlueprintPort(container_port=8080, host_port=8080)],
                    cpu_limit=0.5,
                    memory_mb=self._mb_from_prompt(text, default=512),
                    command="python -m http.server 8080" if not is_worker else None,
                )
            )
        if "nginx" in text or "web" in text or ("frontend" in text):
            services.append(
                InfraServiceSpec(
                    name="web",
                    image="nginx:1.27-alpine",
                    kind=ServiceKind.WEB,
                    ports=[BlueprintPort(container_port=80, host_port=8081)],
                    cpu_limit=0.25,
                    memory_mb=128,
                )
            )

        if not services:
            services.append(
                InfraServiceSpec(
                    name="app",
                    image="nginx:1.27-alpine",
                    kind=ServiceKind.WEB,
                    ports=[BlueprintPort(container_port=80, host_port=8080)],
                    cpu_limit=0.5,
                    memory_mb=256,
                )
            )
            notes.append("No recognizable stack in the prompt; scaffolded a single web service.")

        return InfraBlueprint(name="ai-stack", summary=prompt.strip()[:1024], services=services, notes=notes)

    @staticmethod
    def _mb_from_prompt(text: str, *, default: int) -> int:
        """Extract a memory hint like '1gb' / '512mb' from the prompt."""
        import re

        gb = re.search(r"(\d+)\s*g(i?b)?\b", text)
        if gb:
            return int(gb.group(1)) * 1024
        mb = re.search(r"(\d+)\s*m(i?b)?\b", text)
        if mb:
            return int(mb.group(1))
        return default

    # -- Guardrails -------------------------------------------------------- #

    def validate_and_guardrail(
        self, blueprint: InfraBlueprint, target: BlueprintTarget
    ) -> tuple[InfraBlueprint, BlueprintValidation]:
        violations: list[GuardrailViolation] = []
        adjusted = False
        services: list[InfraServiceSpec] = []

        if not blueprint.services:
            violations.append(
                GuardrailViolation(
                    code="no_services",
                    message="Blueprint must contain at least one service.",
                    severity=GuardrailSeverity.ERROR,
                )
            )

        is_local = target == BlueprintTarget.LOCAL_NODE
        max_vcpu = self._settings.agent_local_node_max_vcpu
        max_mem = self._settings.agent_local_node_max_memory_mb

        total_cpu = 0.0
        total_mem = 0
        for svc in blueprint.services:
            svc = svc.model_copy(deep=True)
            fixed_image, image_reason = sanitize_service_image(svc.image)
            if image_reason:
                violations.append(
                    GuardrailViolation(
                        code="image_rewritten",
                        message=image_reason,
                        severity=GuardrailSeverity.WARNING,
                        service=svc.name,
                    )
                )
                svc.image = fixed_image
                adjusted = True
            if is_local:
                if svc.cpu_limit > max_vcpu:
                    violations.append(
                        GuardrailViolation(
                            code="cpu_clamped",
                            message=f"{svc.name}: cpu_limit reduced to {max_vcpu} vCPU (local node cap).",
                            severity=GuardrailSeverity.WARNING,
                            service=svc.name,
                        )
                    )
                    svc.cpu_limit = max_vcpu
                    adjusted = True
                if svc.memory_mb > max_mem:
                    violations.append(
                        GuardrailViolation(
                            code="mem_clamped",
                            message=f"{svc.name}: memory reduced to {max_mem} MB (local node cap).",
                            severity=GuardrailSeverity.WARNING,
                            service=svc.name,
                        )
                    )
                    svc.memory_mb = max_mem
                    adjusted = True
            total_cpu += svc.cpu_limit * svc.replicas
            total_mem += svc.memory_mb * svc.replicas
            services.append(svc)

        if is_local and total_cpu > max_vcpu:
            violations.append(
                GuardrailViolation(
                    code="total_cpu_exceeded",
                    message=(
                        f"Total requested CPU {total_cpu:.2f} vCPU exceeds the local node "
                        f"cap of {max_vcpu} vCPU. Reduce replicas or split across nodes."
                    ),
                    severity=GuardrailSeverity.ERROR,
                )
            )
        if is_local and total_mem > max_mem:
            violations.append(
                GuardrailViolation(
                    code="total_mem_exceeded",
                    message=(
                        f"Total requested memory {total_mem} MB exceeds the local node "
                        f"cap of {max_mem} MB."
                    ),
                    severity=GuardrailSeverity.ERROR,
                )
            )

        adjusted_blueprint = blueprint.model_copy(update={"services": services})
        has_error = any(v.severity == GuardrailSeverity.ERROR for v in violations)
        validation = BlueprintValidation(
            valid=not has_error,
            adjusted=adjusted,
            violations=violations,
        )
        return adjusted_blueprint, validation

    # -- Cost -------------------------------------------------------------- #

    def estimate_cost(self, blueprint: InfraBlueprint, target: BlueprintTarget) -> CostEstimate:
        cpu_rate = self._settings.cost_rate_cpu_core_hour
        mem_rate = self._settings.cost_rate_memory_gib_hour
        pg_rate = self._settings.cost_rate_postgres_hour
        redis_rate = self._settings.cost_rate_redis_hour

        breakdown: list[CostLineItem] = []
        hourly = Decimal(0)
        for svc in blueprint.services:
            replicas = Decimal(svc.replicas)
            cpu_cores = Decimal(str(svc.cpu_limit)) * replicas
            mem_gib = (Decimal(svc.memory_mb) / Decimal(1024)) * replicas
            cpu_usd = (cpu_cores * cpu_rate).quantize(Decimal("0.0001"))
            mem_usd = (mem_gib * mem_rate).quantize(Decimal("0.0001"))
            addon_usd = Decimal(0)
            if svc.kind == ServiceKind.DATASTORE:
                addon_usd = (pg_rate * replicas).quantize(Decimal("0.0001"))
            elif svc.kind == ServiceKind.CACHE:
                addon_usd = (redis_rate * replicas).quantize(Decimal("0.0001"))
            line_hourly = (cpu_usd + mem_usd + addon_usd).quantize(Decimal("0.0001"))
            hourly += line_hourly
            breakdown.append(
                CostLineItem(
                    service=svc.name,
                    cpu_usd=float(cpu_usd),
                    memory_usd=float(mem_usd),
                    addon_usd=float(addon_usd),
                    hourly_usd=float(line_hourly),
                )
            )

        monthly = (hourly * Decimal(self._settings.cost_hours_per_month)).quantize(Decimal("0.01"))
        return CostEstimate(
            hourly_usd=float(hourly.quantize(Decimal("0.0001"))),
            monthly_usd=float(monthly),
            self_hosted=target == BlueprintTarget.LOCAL_NODE,
            breakdown=breakdown,
        )

    # -- Mapping: homelab Docker ------------------------------------------ #

    def build_run_specs(self, blueprint: InfraBlueprint) -> list[RunContainerSpec]:
        """Expand a blueprint into one RunContainerSpec per replica."""
        specs: list[RunContainerSpec] = []
        for svc in blueprint.services:
            for replica in range(svc.replicas):
                suffix = "" if svc.replicas == 1 else f"-{replica + 1}"
                # Only the first replica keeps the fixed host port to avoid clashes.
                ports = [
                    PortMapping(
                        container_port=p.container_port,
                        host_port=p.host_port + replica,
                        protocol=p.protocol,
                    )
                    for p in svc.ports
                ]
                specs.append(
                    RunContainerSpec(
                        image=svc.image,
                        name=f"{blueprint.name}-{svc.name}{suffix}",
                        ports=ports,
                        env=svc.env,
                        volumes=[
                            VolumeMount(
                                host_path=v.host_path,
                                container_path=v.container_path,
                                mode=v.mode,
                            )
                            for v in svc.volumes
                        ],
                        cpu_limit=svc.cpu_limit,
                        memory_mb=svc.memory_mb,
                        command=svc.command,
                        pull=True,
                    )
                )
        return specs

    # -- Mapping: cloud IaC ------------------------------------------------ #

    def to_wizard_request(
        self, blueprint: InfraBlueprint, target: BlueprintTarget, region: str | None
    ) -> Any:
        """Map a blueprint to a ProvisioningWizardRequest for IaC rendering.

        Cloud targets render a serverless container runtime plus managed data
        services matching the blueprint's datastore/cache services. Credentials
        are omitted and ``run_init`` is False, so this renders IaC (no apply).
        """
        from app.schemas.cloud import (
            AwsCloudConfig,
            AwsResources,
            AzureCloudConfig,
            AzureResources,
            GcpCloudConfig,
            GcpResources,
            IaCEngine,
            ProvisioningWizardRequest,
            RunningInstanceConfig,
            RunningInstanceKind,
            WorkspaceArtifactsMode,
            WorkspaceRuntimeMode,
        )

        name = blueprint.name if len(blueprint.name) >= 3 else f"{blueprint.name}-app"
        has_datastore = any(s.kind == ServiceKind.DATASTORE for s in blueprint.services)
        has_cache = any(s.kind == ServiceKind.CACHE for s in blueprint.services)
        web = next((s for s in blueprint.services if s.kind == ServiceKind.WEB), None)
        service_name = (web.name if web else blueprint.services[0].name) if blueprint.services else name

        if target == BlueprintTarget.GCP:
            cloud = GcpCloudConfig(
                resources=GcpResources(
                    project_id=(region and f"{name}-project") or f"{name}-project",
                    region=region or "us-central1",
                    cloud_run=True,
                    artifact_registry=True,
                    cloud_sql=has_datastore,
                    memorystore=has_cache,
                )
            )
        elif target == BlueprintTarget.AWS:
            cloud = AwsCloudConfig(
                resources=AwsResources(
                    region=region or "us-east-1",
                    app_runner=True,
                    ecr=True,
                    rds=has_datastore,
                    elasticache=has_cache,
                )
            )
        elif target == BlueprintTarget.AZURE:
            cloud = AzureCloudConfig(
                resources=AzureResources(
                    resource_group=f"{name}-rg",
                    location=region or "eastus",
                    container_apps=True,
                    acr=True,
                    cosmos_db=has_datastore,
                    redis_cache=has_cache,
                )
            )
        else:  # pragma: no cover - guarded by the router
            raise ValueError(f"{target} is not a cloud IaC target")

        return ProvisioningWizardRequest(
            name=name,
            iac_engine=IaCEngine.TERRAFORM,
            cloud=cloud,
            run_init=False,
            runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.SERVERLESS,
                service_name=service_name[:63],
                region=region,
            ),
            artifact_mode=WorkspaceArtifactsMode.IAC_ONLY,
        )

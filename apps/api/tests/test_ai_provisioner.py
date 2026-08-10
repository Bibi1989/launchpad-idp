"""AI Infrastructure Provisioner: heuristic generation, guardrails, cost, mapping."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.schemas.ai_provisioner import (
    BlueprintGenerateRequest,
    BlueprintTarget,
    InfraBlueprint,
    InfraServiceSpec,
    ServiceKind,
)
from app.schemas.cloud import (
    ProvisioningWizardRequest,
    RunningInstanceKind,
    WorkspaceRuntimeMode,
)
from app.services.ai_provisioner import AiProvisionerService


def _heuristic_service() -> AiProvisionerService:
    # Force the deterministic heuristic path (no live Gemini key).
    settings = get_settings().model_copy(update={"gemini_api_key": None})
    return AiProvisionerService(settings=settings)


@pytest.mark.asyncio
async def test_heuristic_generates_redis_and_worker() -> None:
    svc = _heuristic_service()
    req = BlueprintGenerateRequest(
        prompt="Deploy a Redis cache with 1GB RAM and a FastAPI worker with persistent storage",
        target=BlueprintTarget.LOCAL_NODE,
        node_id="11111111-1111-1111-1111-111111111111",
    )
    resp = await svc.generate(req)
    assert resp.source == "heuristic"
    names = {s.name for s in resp.blueprint.services}
    assert "redis" in names
    assert resp.validation.valid is True
    assert resp.cost.self_hosted is True
    assert resp.cost.hourly_usd >= 0
    # 1GB hint should size the memory-bearing service accordingly.
    assert any(s.memory_mb >= 1024 for s in resp.blueprint.services)


def test_guardrail_rewrites_missing_fastapi_alpine_image() -> None:
    svc = _heuristic_service()
    bp = InfraBlueprint(
        name="api",
        services=[
            InfraServiceSpec(
                name="fastapi-app",
                image="tiangolo/uvicorn-gunicorn-fastapi:python3.10-alpine",
                kind=ServiceKind.WEB,
            )
        ],
    )
    adjusted, validation = svc.validate_and_guardrail(bp, BlueprintTarget.LOCAL_NODE)
    assert adjusted.services[0].image == "tiangolo/uvicorn-gunicorn-fastapi:python3.11-slim"
    assert validation.adjusted is True
    assert any(v.code == "image_rewritten" for v in validation.violations)


@pytest.mark.asyncio
async def test_fix_rewrites_bad_image_without_gemini() -> None:
    from app.schemas.ai_provisioner import BlueprintFixRequest

    svc = _heuristic_service()
    bp = InfraBlueprint(
        name="api",
        services=[
            InfraServiceSpec(
                name="fastapi-app",
                image="tiangolo/uvicorn-gunicorn-fastapi:python3.10-alpine",
            )
        ],
    )
    resp = await svc.fix(
        BlueprintFixRequest(
            blueprint=bp,
            error_log='pull ... python3.10-alpine: not found',
            prompt="deploy fastapi",
            target=BlueprintTarget.LOCAL_NODE,
            node_id="11111111-1111-1111-1111-111111111111",
        )
    )
    assert resp.blueprint.services[0].image == "tiangolo/uvicorn-gunicorn-fastapi:python3.11-slim"
    assert resp.validation.valid is True


def test_guardrail_clamps_to_local_caps() -> None:
    svc = _heuristic_service()
    settings = get_settings()
    over = InfraBlueprint(
        name="big",
        services=[
            InfraServiceSpec(name="a", image="x", cpu_limit=8.0, memory_mb=99999),
        ],
    )
    adjusted, validation = svc.validate_and_guardrail(over, BlueprintTarget.LOCAL_NODE)
    assert adjusted.services[0].cpu_limit == settings.agent_local_node_max_vcpu
    assert adjusted.services[0].memory_mb == settings.agent_local_node_max_memory_mb
    assert validation.adjusted is True
    assert {v.code for v in validation.violations} >= {"cpu_clamped", "mem_clamped"}


def test_guardrail_total_exceeds_is_invalid() -> None:
    svc = _heuristic_service()
    # Two services each at the cap => total is double the cap => hard error.
    blueprint = InfraBlueprint(
        name="pair",
        services=[
            InfraServiceSpec(name="a", image="x", cpu_limit=2.0, memory_mb=4096),
            InfraServiceSpec(name="b", image="y", cpu_limit=2.0, memory_mb=4096),
        ],
    )
    _, validation = svc.validate_and_guardrail(blueprint, BlueprintTarget.LOCAL_NODE)
    assert validation.valid is False
    assert any(v.code == "total_cpu_exceeded" for v in validation.violations)


def test_cost_includes_datastore_addon() -> None:
    svc = _heuristic_service()
    with_db = InfraBlueprint(
        name="db",
        services=[InfraServiceSpec(name="pg", image="postgres:16", kind=ServiceKind.DATASTORE, memory_mb=512)],
    )
    cost = svc.estimate_cost(with_db, BlueprintTarget.GCP)
    assert cost.breakdown[0].addon_usd > 0
    assert cost.monthly_usd == pytest.approx(cost.hourly_usd * get_settings().cost_hours_per_month, rel=1e-3)


def test_build_run_specs_expands_replicas() -> None:
    svc = _heuristic_service()
    bp = InfraBlueprint(
        name="web",
        services=[
            InfraServiceSpec(
                name="api",
                image="nginx",
                replicas=2,
                ports=[{"container_port": 80, "host_port": 8080}],
            )
        ],
    )
    specs = svc.build_run_specs(bp)
    assert [s.name for s in specs] == ["web-api-1", "web-api-2"]
    # Replicas get distinct host ports to avoid clashes.
    assert specs[0].ports[0].host_port == 8080
    assert specs[1].ports[0].host_port == 8081


@pytest.mark.parametrize("target", [BlueprintTarget.GCP, BlueprintTarget.AWS, BlueprintTarget.AZURE])
def test_cloud_mapping_produces_valid_wizard_request(target: BlueprintTarget) -> None:
    svc = _heuristic_service()
    bp = InfraBlueprint(
        name="stack",
        services=[
            InfraServiceSpec(name="web", image="nginx", kind=ServiceKind.WEB),
            InfraServiceSpec(name="pg", image="postgres:16", kind=ServiceKind.DATASTORE),
            InfraServiceSpec(name="cache", image="redis:7", kind=ServiceKind.CACHE),
        ],
    )
    wiz = svc.to_wizard_request(bp, target, "us-central1")
    assert isinstance(wiz, ProvisioningWizardRequest)
    assert wiz.runtime_mode == WorkspaceRuntimeMode.RUNNING_INSTANCE
    assert wiz.running_instance.kind == RunningInstanceKind.SERVERLESS
    assert wiz.run_init is False


def test_local_node_is_not_a_cloud_iac_target() -> None:
    svc = _heuristic_service()
    bp = InfraBlueprint(name="x", services=[InfraServiceSpec(name="a", image="nginx")])
    with pytest.raises(ValueError):
        svc.to_wizard_request(bp, BlueprintTarget.LOCAL_NODE, None)

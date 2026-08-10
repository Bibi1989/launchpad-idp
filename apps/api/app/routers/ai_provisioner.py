"""AI Infrastructure Provisioner control plane.

- POST /ai/generate-blueprint : natural language -> validated blueprint + cost
- POST /ai/fix-blueprint     : repair blueprint from deploy error log
- POST /ai/deploy-blueprint    : execute on a homelab node (Docker) or render cloud IaC
- GET  /ai/status              : Gemini availability
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db_session
from app.core.logging import get_logger
from app.deps.auth import CurrentUser
from app.deps.org import CurrentOrg
from app.schemas.ai_provisioner import (
    AiProvisionerStatus,
    BlueprintDeployRequest,
    BlueprintDeployResponse,
    BlueprintFixRequest,
    BlueprintGenerateRequest,
    BlueprintGenerateResponse,
    BlueprintTarget,
    DeployStepResult,
    DeployedServiceLink,
    GuardrailSeverity,
)
from app.schemas.ansible_ai import AnsibleRefineRequest, AnsibleRefineResponse
from app.schemas.nodes import NodeCommandAction
from app.services.ai_provisioner import AiProvisionerService
from app.services.ansible_ai import AnsibleAiService
from app.services.node_registry import NodeRegistryService, get_agent_hub
from app.services.provisioning import ProvisioningService

logger = get_logger(__name__)
router = APIRouter(prefix="/ai", tags=["ai-provisioner"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/status", response_model=AiProvisionerStatus)
async def ai_status(user: CurrentUser) -> AiProvisionerStatus:
    settings = get_settings()
    return AiProvisionerStatus(
        gemini_configured=bool(settings.gemini_api_key),
        model=settings.gemini_model,
        heuristic_fallback=settings.ai_provisioner_heuristic_fallback,
    )


@router.post("/refine-ansible", response_model=AnsibleRefineResponse)
async def refine_ansible(
    payload: AnsibleRefineRequest,
    user: CurrentUser,
    org: CurrentOrg,
) -> AnsibleRefineResponse:
    """Update Ansible YAML files from a natural-language prompt."""
    _ = user, org
    service = AnsibleAiService()
    try:
        files, summary, source = service.refine(
            prompt=payload.prompt,
            app_deploy_mode=payload.app_deploy_mode,
            reverse_proxy=payload.reverse_proxy,
            workspace_name=payload.workspace_name,
            current_files=[f.model_dump() for f in payload.files],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "ansible_refine_invalid", "message": str(exc)},
        ) from exc

    return AnsibleRefineResponse(
        files=files,
        summary=summary,
        source=source,
        gemini_configured=service.gemini_configured,
    )


@router.post("/generate-blueprint", response_model=BlueprintGenerateResponse)
async def generate_blueprint(
    payload: BlueprintGenerateRequest,
    user: CurrentUser,
    org: CurrentOrg,
) -> BlueprintGenerateResponse:
    if payload.target == BlueprintTarget.LOCAL_NODE and payload.node_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "node_required", "message": "node_id is required for local_node target"},
        )
    service = AiProvisionerService()
    return await service.generate(payload)


@router.post("/fix-blueprint", response_model=BlueprintGenerateResponse)
async def fix_blueprint(
    payload: BlueprintFixRequest,
    user: CurrentUser,
    org: CurrentOrg,
) -> BlueprintGenerateResponse:
    """Repair a blueprint using the deploy error log (Gemini or deterministic)."""
    _ = user, org
    if payload.target == BlueprintTarget.LOCAL_NODE and payload.node_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "node_required", "message": "node_id is required for local_node target"},
        )
    service = AiProvisionerService()
    return await service.fix(payload)


@router.post("/deploy-blueprint", response_model=BlueprintDeployResponse)
async def deploy_blueprint(
    payload: BlueprintDeployRequest,
    user: CurrentUser,
    org: CurrentOrg,
    session: DbSession,
) -> BlueprintDeployResponse:
    service = AiProvisionerService()
    blueprint, validation = service.validate_and_guardrail(payload.blueprint, payload.target)
    if not validation.valid:
        errors = [v.message for v in validation.violations if v.severity == GuardrailSeverity.ERROR]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "guardrail_failed", "message": "; ".join(errors) or "Invalid blueprint"},
        )

    deployment_id = uuid4().hex
    if payload.target == BlueprintTarget.LOCAL_NODE:
        return await _deploy_to_node(
            deployment_id, blueprint, payload, service, session, org
        )
    return await _deploy_to_cloud(deployment_id, blueprint, payload, service, session, user, org)


async def _deploy_to_node(
    deployment_id: str,
    blueprint,
    payload: BlueprintDeployRequest,
    service: AiProvisionerService,
    session: AsyncSession,
    org,
) -> BlueprintDeployResponse:
    if payload.node_id is None:
        raise HTTPException(status_code=422, detail="node_id is required for local_node target")

    registry = NodeRegistryService(session)
    node = await registry.get_node(payload.node_id, org_id=org.org_id)
    if node is None:
        raise HTTPException(status_code=404, detail={"code": "node_not_found", "message": "Node not found"})
    if not get_agent_hub().is_online(str(node.id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "node_offline", "message": "Agent is not connected"},
        )

    steps: list[DeployStepResult] = []
    logs: list[str] = [f"Deploying '{blueprint.name}' to node {node.name}"]
    specs = service.build_run_specs(blueprint)
    services: list[DeployedServiceLink] = []
    ok = True
    host_base = _node_reachability_host(node.hostname)

    for spec in specs:
        if spec.pull:
            try:
                result = await registry.dispatch_command(
                    node,
                    action=NodeCommandAction.PULL_IMAGE,
                    payload={"image": spec.image},
                )
                steps.append(DeployStepResult(step=f"pull {spec.image}", ok=result.ok, detail=result.detail))
                logs.append(f"pull {spec.image}: {'ok' if result.ok else 'failed'} {result.detail}".strip())
                if not result.ok:
                    ok = False
                    continue
            except (TimeoutError, RuntimeError) as exc:
                steps.append(DeployStepResult(step=f"pull {spec.image}", ok=False, detail=str(exc)))
                logs.append(f"pull {spec.image}: error {exc}")
                ok = False
                continue
        try:
            result = await registry.dispatch_command(
                node,
                action=NodeCommandAction.RUN_CONTAINER,
                payload=spec.model_dump(),
            )
            steps.append(DeployStepResult(step=f"run {spec.name}", ok=result.ok, detail=result.detail))
            logs.append(f"run {spec.name}: {'ok' if result.ok else 'failed'} {result.detail}".strip())
            if not result.ok:
                ok = False
            primary = spec.ports[0] if spec.ports else None
            url = None
            if primary is not None and host_base:
                url = f"http://{host_base}:{primary.host_port}"
            services.append(
                DeployedServiceLink(
                    name=spec.name,
                    container_name=spec.name,
                    host_port=primary.host_port if primary else None,
                    container_port=primary.container_port if primary else None,
                    url=url,
                    ok=result.ok,
                )
            )
        except (TimeoutError, RuntimeError) as exc:
            steps.append(DeployStepResult(step=f"run {spec.name}", ok=False, detail=str(exc)))
            logs.append(f"run {spec.name}: error {exc}")
            ok = False
            services.append(
                DeployedServiceLink(
                    name=spec.name,
                    container_name=spec.name,
                    ok=False,
                )
            )

    logs.append("Deployment complete" if ok else "Deployment finished with errors")
    view_path = f"/fleet?node={node.id}" if ok else None
    return BlueprintDeployResponse(
        deployment_id=deployment_id,
        target=payload.target,
        mode="homelab_docker",
        node_id=node.id,
        node_name=node.name,
        ok=ok,
        steps=steps,
        logs=logs,
        view_path=view_path,
        services=services,
    )


def _node_reachability_host(hostname: str | None) -> str:
    """Best-effort host for opening published ports from the operator browser."""
    host = (hostname or "").strip().lower()
    if not host or host in {"localhost", "127.0.0.1", "::1"}:
        return "127.0.0.1"
    # Docker / k3d agent containers often report container hostnames; ports are on the
    # Docker host from the operator's perspective when agent shares the host network
    # or publishes via host.docker.internal. Prefer localhost for local agents.
    if host.endswith(".internal") or host.startswith("k3d-") or "docker" in host:
        return "127.0.0.1"
    return hostname.strip() if hostname else "127.0.0.1"


async def _deploy_to_cloud(
    deployment_id: str,
    blueprint,
    payload: BlueprintDeployRequest,
    service: AiProvisionerService,
    session: AsyncSession,
    user,
    org,
) -> BlueprintDeployResponse:
    logs = [f"Rendering IaC for '{blueprint.name}' targeting {payload.target.value}"]
    try:
        wizard = service.to_wizard_request(blueprint, payload.target, payload.region)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "unsupported_target", "message": str(exc)}) from exc

    provisioning = ProvisioningService(session)
    try:
        bundle = await provisioning.generate_bundle(
            wizard,
            owner=user,
            org_id=org.org_id,
            project_id=None,
        )
    except Exception as exc:
        logger.exception("ai_deploy_cloud_failed", target=payload.target.value)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "iac_render_failed", "message": str(exc)},
        ) from exc

    logs.append(f"Generated {len(bundle.files)} IaC file(s) in workspace {bundle.workspace_id}")
    steps = [DeployStepResult(step=f"render {f}", ok=True) for f in bundle.files[:50]]
    workspace_id = str(bundle.workspace_id)
    return BlueprintDeployResponse(
        deployment_id=deployment_id,
        target=payload.target,
        mode="iac",
        ok=True,
        steps=steps,
        logs=logs,
        workspace_id=workspace_id,
        view_path=f"/workspaces/{workspace_id}",
        services=[
            DeployedServiceLink(
                name=svc.name,
                container_name=svc.name,
                ok=True,
            )
            for svc in blueprint.services
        ],
    )

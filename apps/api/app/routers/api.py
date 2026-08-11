from __future__ import annotations

import asyncio
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db_session
from app.core.events import EnvChannelEvent, subscribe_env_events
from app.deps.auth import CurrentUser
from app.deps.org import CurrentOrg
from app.repositories.user import UserRepository
from app.schemas.diagnostic import AnalyzePreviewRequest, AnalyzePreviewResponse
from app.schemas.environment import (
    AuditLogRead,
    EnvironmentCreate,
    EnvironmentExtendRequest,
    EnvironmentPromoteRequest,
    EnvironmentRead,
    HealthResponse,
    KindClusterActionRequest,
    KindClusterActionResult,
    KindClusterStatus,
    PreviewAppTemplateRead,
    PreviewBuildStatus,
    PreviewLaunchRequest,
)
from app.services.audit import AuditService
from app.services.environment import TERMINAL_STATUSES, EnvironmentService
from app.services.kind_cluster import delete_kind_cluster, ensure_kind_cluster, probe_kind_cluster
from app.services.preview_analyzer import PreviewAnalyzerError, PreviewAnalyzerService
from app.services.security_telemetry import collect_telemetry

router = APIRouter()


def get_environment_service(
    session: AsyncSession = Depends(get_db_session),
) -> EnvironmentService:
    return EnvironmentService(session)


def get_audit_service(
    session: AsyncSession = Depends(get_db_session),
) -> AuditService:
    return AuditService(session)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="launchpad-api")


@router.get("/preview/kind/status", response_model=KindClusterStatus)
async def preview_kind_status(user: CurrentUser) -> KindClusterStatus:
    """Read-only Kind readiness for Launch → Local (does not start the cluster)."""
    _ = user
    payload = await probe_kind_cluster()
    return KindClusterStatus.model_validate(payload)


@router.post("/preview/kind/up", response_model=KindClusterActionResult)
async def preview_kind_up(
    user: CurrentUser,
    payload: KindClusterActionRequest | None = None,
) -> KindClusterActionResult:
    """Create or ensure the local k3s/kind cluster (Settings → Local Kubernetes)."""
    _ = user
    body = payload or KindClusterActionRequest()
    try:
        result = await ensure_kind_cluster(
            cluster_name=body.cluster_name,
            respect_auto_manage=False,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "kind_up_failed", "message": str(exc)},
        ) from exc
    status_value = str(result.get("status") or "ready")
    cluster = str(result.get("cluster") or body.cluster_name or "")
    engine = str(result.get("engine") or "k3s")
    context = result.get("context")
    reason = result.get("reason")
    if status_value == "skipped":
        message = f"Cluster manage skipped ({reason or 'unknown'})"
    else:
        message = f"Local {engine} cluster '{cluster}' is ready"
    return KindClusterActionResult(
        status=status_value,
        cluster=cluster,
        engine=engine,
        context=str(context) if context else None,
        message=message,
        output=result.get("output"),
        reason=str(reason) if reason else None,
    )


@router.post("/preview/kind/down", response_model=KindClusterActionResult)
async def preview_kind_down(
    user: CurrentUser,
    payload: KindClusterActionRequest | None = None,
) -> KindClusterActionResult:
    """Delete the local k3s/kind cluster (Settings → Local Kubernetes)."""
    _ = user
    body = payload or KindClusterActionRequest()
    try:
        result = await delete_kind_cluster(
            cluster_name=body.cluster_name,
            respect_auto_manage=False,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "kind_down_failed", "message": str(exc)},
        ) from exc
    status_value = str(result.get("status") or "deleted")
    cluster = str(result.get("cluster") or body.cluster_name or "")
    engine = str(result.get("engine") or "k3s")
    reason = result.get("reason")
    if status_value == "skipped":
        message = f"Cluster delete skipped ({reason or 'unknown'})"
    else:
        message = f"Local {engine} cluster '{cluster}' deleted"
    return KindClusterActionResult(
        status=status_value,
        cluster=cluster,
        engine=engine,
        context=None,
        message=message,
        output=result.get("output"),
        reason=str(reason) if reason else None,
    )


@router.get("/preview/build/status", response_model=PreviewBuildStatus)
async def preview_build_status(user: CurrentUser) -> PreviewBuildStatus:
    """Whether preview launches build container images from git (Dockerfile at repo root)."""
    _ = user
    settings = get_settings()
    if settings.preview_build_enabled:
        message = (
            f"Custom-repo previews clone your repository and build {settings.preview_build_dockerfile} "
            "before deploy. Catalog templates still use fixed demo images."
        )
        if settings.preview_image_registry:
            message += f" Images push to {settings.preview_image_registry}."
        elif settings.preview_build_kind_load:
            message += " Images load into the local kind cluster after build."
    else:
        message = (
            "Preview builds are disabled - launches use the configured workload image. "
            "Set PREVIEW_BUILD_ENABLED=true to build from Dockerfile."
        )
    return PreviewBuildStatus(
        enabled=settings.preview_build_enabled,
        dockerfile=settings.preview_build_dockerfile,
        kind_load=settings.preview_build_kind_load,
        registry=settings.preview_image_registry,
        message=message,
    )


@router.get("/preview/templates", response_model=list[PreviewAppTemplateRead])
async def list_preview_templates(
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
) -> list[PreviewAppTemplateRead]:
    _ = user
    return service.list_preview_templates()


@router.post(
    "/preview/launch",
    response_model=EnvironmentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def launch_preview(
    payload: PreviewLaunchRequest,
    request: Request,
    user: CurrentUser,
    org: CurrentOrg,
    service: EnvironmentService = Depends(get_environment_service),
) -> EnvironmentRead:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return await service.launch_preview(
        payload,
        owner=user,
        correlation_id=correlation_id,
        org_id=org.org_id,
    )


@router.get("/environments", response_model=list[EnvironmentRead])
async def list_environments(
    user: CurrentUser,
    org: CurrentOrg,
    service: EnvironmentService = Depends(get_environment_service),
) -> list[EnvironmentRead]:
    return await service.list_environments(user, org_id=org.org_id)


@router.post(
    "/environments",
    response_model=EnvironmentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_environment(
    payload: EnvironmentCreate,
    request: Request,
    user: CurrentUser,
    org: CurrentOrg,
    service: EnvironmentService = Depends(get_environment_service),
) -> EnvironmentRead:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return await service.enqueue_provision(
        payload,
        owner=user,
        correlation_id=correlation_id,
        org_id=org.org_id,
    )


@router.get("/environments/{environment_id}", response_model=EnvironmentRead)
async def get_environment(
    environment_id: UUID,
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
) -> EnvironmentRead:
    return await service.get_environment(environment_id, user)


@router.get("/environments/{environment_id}/audits", response_model=list[AuditLogRead])
async def list_environment_audits(
    environment_id: UUID,
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
    audits: AuditService = Depends(get_audit_service),
    limit: int = 50,
) -> list[AuditLogRead]:
    await service.get_environment_entity(environment_id, user)
    rows = await audits.list_for_environment(environment_id, limit=limit)
    return [AuditLogRead.model_validate(row) for row in rows]


@router.post(
    "/environments/{environment_id}/analyze",
    response_model=AnalyzePreviewResponse,
)
async def analyze_environment_preview(
    environment_id: UUID,
    request: Request,
    user: CurrentUser,
    payload: Annotated[AnalyzePreviewRequest | None, Body()] = None,
    service: EnvironmentService = Depends(get_environment_service),
) -> AnalyzePreviewResponse:
    """Gemini structured diagnostic over CI/CD, K8s, Trivy SARIF, and CodeQL telemetry."""
    body = payload or AnalyzePreviewRequest()
    environment = await service.get_environment(environment_id, user)
    correlation_id = getattr(request.state, "correlation_id", "unknown")

    env_messages: list[str] = []
    if body.includeEnvironmentLogs:
        logs = await service.list_logs(environment_id, user)
        env_messages = [entry.message for entry in logs]
        if environment.error_message:
            env_messages.append(environment.error_message)

    bundle = collect_telemetry(
        cicd_logs=body.cicdLogs,
        kubernetes_logs=body.kubernetesLogs,
        trivy_sarif=body.trivySarif,
        codeql_sarif=body.codeqlSarif,
        sast_logs=body.sastLogs,
        environment_log_messages=env_messages,
    )

    analyzer = PreviewAnalyzerService()
    try:
        report = await analyzer.analyze(
            bundle,
            manifest_snippets=body.manifestSnippets,
            correlation_id=correlation_id,
        )
    except PreviewAnalyzerError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "analyze_failed", "message": str(exc)},
        ) from exc

    return AnalyzePreviewResponse(
        report=report,
        telemetrySummary=bundle.to_summary(),
        geminiConfigured=analyzer.gemini_configured,
    )


@router.get("/preview/analyzer/status")
async def preview_analyzer_status(user: CurrentUser) -> dict[str, object]:
    """Whether Gemini AI is configured for preview / workspace analysis."""
    _ = user
    analyzer = PreviewAnalyzerService()
    settings = get_settings()
    return {
        "gemini_configured": analyzer.gemini_configured,
        "gemini_model": settings.gemini_model,
        "heuristic_fallback": settings.preview_analyzer_heuristic_fallback,
        "message": (
            "Gemini is ready."
            if analyzer.gemini_configured
            else "Set GEMINI_API_KEY on the API (and in deploy/oci .env for Compose) to enable AI analysis."
        ),
    }


@router.post(
    "/preview/analyze",
    response_model=AnalyzePreviewResponse,
)
async def analyze_preview_telemetry(
    request: Request,
    user: CurrentUser,
    payload: AnalyzePreviewRequest,
) -> AnalyzePreviewResponse:
    """Ad-hoc analyzer for uploaded CI/CD, SARIF, and runtime telemetry (no environment)."""
    _ = user
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    bundle = collect_telemetry(
        cicd_logs=payload.cicdLogs,
        kubernetes_logs=payload.kubernetesLogs,
        trivy_sarif=payload.trivySarif,
        codeql_sarif=payload.codeqlSarif,
        sast_logs=payload.sastLogs,
    )
    analyzer = PreviewAnalyzerService()
    try:
        report = await analyzer.analyze(
            bundle,
            manifest_snippets=payload.manifestSnippets,
            correlation_id=correlation_id,
        )
    except PreviewAnalyzerError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "analyze_failed", "message": str(exc)},
        ) from exc
    return AnalyzePreviewResponse(
        report=report,
        telemetrySummary=bundle.to_summary(),
        geminiConfigured=analyzer.gemini_configured,
    )


@router.post(
    "/environments/{environment_id}/drift-scan",
    response_model=EnvironmentRead,
)
async def scan_environment_drift(
    environment_id: UUID,
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
) -> EnvironmentRead:
    return await service.scan_drift(environment_id, user)


@router.post(
    "/environments/{environment_id}/retry",
    response_model=EnvironmentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_environment_provision(
    environment_id: UUID,
    request: Request,
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
) -> EnvironmentRead:
    """Re-queue provision for a FAILED environment."""
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return await service.retry_provision(
        environment_id,
        owner=user,
        correlation_id=correlation_id,
    )


@router.post(
    "/environments/{environment_id}/cancel-provision",
    response_model=EnvironmentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_environment_provision(
    environment_id: UUID,
    request: Request,
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
) -> EnvironmentRead:
    """Stop an in-flight provision without tearing down resources."""
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return await service.cancel_provision(
        environment_id,
        owner=user,
        correlation_id=correlation_id,
    )


@router.post(
    "/environments/{environment_id}/extend",
    response_model=EnvironmentRead,
)
async def extend_environment_ttl(
    environment_id: UUID,
    payload: EnvironmentExtendRequest,
    request: Request,
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
) -> EnvironmentRead:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return await service.extend_ttl(
        environment_id,
        payload,
        owner=user,
        correlation_id=correlation_id,
    )


@router.post(
    "/environments/{environment_id}/pause",
    response_model=EnvironmentRead,
)
async def pause_environment(
    environment_id: UUID,
    request: Request,
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
) -> EnvironmentRead:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return await service.pause_environment(
        environment_id,
        owner=user,
        correlation_id=correlation_id,
    )


@router.post(
    "/environments/{environment_id}/resume",
    response_model=EnvironmentRead,
)
async def resume_environment(
    environment_id: UUID,
    request: Request,
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
) -> EnvironmentRead:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return await service.resume_environment(
        environment_id,
        owner=user,
        correlation_id=correlation_id,
    )


@router.post(
    "/environments/{environment_id}/promote",
    response_model=EnvironmentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def promote_environment_to_cloud(
    environment_id: UUID,
    payload: EnvironmentPromoteRequest,
    request: Request,
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
) -> EnvironmentRead:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return await service.promote_to_cloud(
        environment_id,
        payload,
        owner=user,
        correlation_id=correlation_id,
    )


@router.delete(
    "/environments/{environment_id}",
    response_model=EnvironmentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def teardown_environment(
    environment_id: UUID,
    request: Request,
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
    force: bool = False,
) -> EnvironmentRead:
    """Tear down a preview. ``?force=true`` cancels an in-flight provision and
    cleans up stranded resources instead of returning 409."""
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return await service.request_teardown(
        environment_id,
        owner=user,
        correlation_id=correlation_id,
        force=force,
    )


@router.get("/environments/{environment_id}/stream")
async def stream_environment_events(
    environment_id: UUID,
    user: CurrentUser,
    service: EnvironmentService = Depends(get_environment_service),
) -> StreamingResponse:
    """SSE bridge over Redis Pub/Sub channel `env_channel:{env_id}`."""
    environment = await service.get_environment(environment_id, user)
    owner_id = user.id

    async def event_generator():
        from app.core.database import AsyncSessionLocal

        snapshot = {
            "type": "STATUS_CHANGE",
            "status": environment.status.value,
            "commit_sha": environment.latest_commit_sha,
            "message": "stream connected",
            "environment_id": str(environment_id),
            "preview_url": environment.preview_url,
            "node_port": environment.node_port,
            "app_ready": bool(environment.app_ready),
            "error_message": environment.error_message,
        }
        yield f"event: message\ndata: {json.dumps(snapshot)}\n\n"

        try:
            async for event in subscribe_env_events(environment_id):
                async with AsyncSessionLocal() as session:
                    real_owner = await UserRepository(session).get_by_id(owner_id)
                    if real_owner is None:
                        yield (
                            "event: error\ndata: "
                            f"{json.dumps({'message': 'unauthorized'})}\n\n"
                        )
                        break
                    live = EnvironmentService(session)
                    try:
                        await live.get_environment(environment_id, real_owner)
                    except Exception:
                        yield (
                            "event: error\ndata: "
                            f"{json.dumps({'message': 'environment_not_found'})}\n\n"
                        )
                        break

                payload = _serialize_env_event(event, environment_id)
                yield f"event: message\ndata: {json.dumps(payload)}\n\n"
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _serialize_env_event(
    event: EnvChannelEvent,
    environment_id: UUID,
) -> dict[str, str | int | bool | None]:
    return {
        "type": event.type,
        "status": event.status,
        "commit_sha": event.commit_sha,
        "message": event.message,
        "log_level": event.log_level,
        "stage": event.stage.value if event.stage else None,
        "timestamp": event.timestamp,
        "environment_id": event.environment_id or str(environment_id),
        "preview_url": event.preview_url,
        "node_port": event.node_port,
        "app_ready": event.app_ready,
        "notice": event.notice,
        "error_message": event.error_message,
    }


@router.get("/environments/{environment_id}/logs/stream")
async def stream_environment_logs(
    environment_id: UUID,
    user: CurrentUser,
) -> StreamingResponse:
    settings = get_settings()
    owner_id = user.id

    async def event_generator():
        from app.core.database import AsyncSessionLocal

        seen_ids: set[UUID] = set()

        while True:
            async with AsyncSessionLocal() as session:
                real_owner = await UserRepository(session).get_by_id(owner_id)
                if real_owner is None:
                    yield (
                        "event: error\ndata: "
                        f"{json.dumps({'message': 'unauthorized'})}\n\n"
                    )
                    break
                service = EnvironmentService(session)
                environment = await service.get_environment(environment_id, real_owner)
                logs = await service.list_logs(environment_id, real_owner)

            for entry in logs:
                if entry.id in seen_ids:
                    continue
                seen_ids.add(entry.id)
                payload = {
                    "id": str(entry.id),
                    "environment_id": str(entry.environment_id),
                    "log_level": entry.log_level.value,
                    "stage": entry.stage.value if entry.stage else None,
                    "message": entry.message,
                    "timestamp": entry.timestamp.isoformat(),
                }
                yield f"id: {entry.id}\nevent: log\ndata: {json.dumps(payload)}\n\n"

            if environment.status in TERMINAL_STATUSES:
                done_payload = {
                    "environment_id": str(environment_id),
                    "status": environment.status.value,
                }
                yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"
                break

            await asyncio.sleep(settings.sse_poll_interval_seconds)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

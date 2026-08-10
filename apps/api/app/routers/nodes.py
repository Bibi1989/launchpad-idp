"""Hybrid local/edge agent node control plane.

REST surface for operators (enroll, list, inspect, command, revoke), an
unauthenticated agent registration endpoint (install-token authenticated), and
the reverse WebSocket tunnel the agent dials back to for telemetry + commands.
"""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, get_db_session
from app.core.logging import get_logger
from app.deps.auth import CurrentUser
from app.deps.org import CurrentOrg
from app.schemas.nodes import (
    NodeCommandAction,
    NodeCommandRequest,
    NodeCommandResult,
    NodeCredentials,
    NodeEnrollRequest,
    NodeInstallInstructions,
    NodeRead,
    NodeRegisterRequest,
    NodeTelemetry,
)
from app.services import agent_install
from app.services.node_registry import NodeRegistryService, get_agent_hub

logger = get_logger(__name__)
router = APIRouter(prefix="/nodes", tags=["nodes"])
# Agent install scripts dial `/api/v1/ws/nodes/connect` (not under `/nodes`).
ws_router = APIRouter(tags=["nodes"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def _service(session: AsyncSession) -> NodeRegistryService:
    return NodeRegistryService(session)


# --------------------------------------------------------------------------- #
# Operator REST surface
# --------------------------------------------------------------------------- #


@router.post("", response_model=NodeInstallInstructions, status_code=status.HTTP_201_CREATED)
async def enroll_node(
    payload: NodeEnrollRequest,
    request: Request,
    user: CurrentUser,
    org: CurrentOrg,
    session: DbSession,
) -> NodeInstallInstructions:
    """Register a new deployment target and return a one-line install command."""
    service = _service(session)
    node, raw_token = await service.create_enrollment(
        name=payload.name,
        owner=user,
        org_id=org.org_id,
        labels=payload.labels,
    )
    base = str(request.base_url)
    return NodeInstallInstructions(
        node_id=node.id,
        name=node.name,
        token=raw_token,
        expires_at=node.enrollment_expires_at,  # type: ignore[arg-type]
        control_plane_url=agent_install.control_plane_url(request_base_url=base),
        agent_ws_url=agent_install.agent_ws_url(request_base_url=base),
        install_command=agent_install.one_line_install_command(raw_token, request_base_url=base),
    )


@router.get("", response_model=list[NodeRead])
async def list_nodes(
    user: CurrentUser,
    org: CurrentOrg,
    session: DbSession,
) -> list[NodeRead]:
    return await _service(session).list_nodes(org_id=org.org_id)


@router.get("/{node_id}", response_model=NodeRead)
async def get_node(
    node_id: UUID,
    user: CurrentUser,
    org: CurrentOrg,
    session: DbSession,
) -> NodeRead:
    read = await _service(session).get_node_read(node_id, org_id=org.org_id)
    if read is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "node_not_found", "message": "Node not found"},
        )
    return read


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_node(
    node_id: UUID,
    user: CurrentUser,
    org: CurrentOrg,
    session: DbSession,
) -> None:
    service = _service(session)
    node = await service.get_node(node_id, org_id=org.org_id)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "node_not_found", "message": "Node not found"},
        )
    await service.revoke_node(node)


@router.post("/{node_id}/commands", response_model=NodeCommandResult)
async def dispatch_command(
    node_id: UUID,
    payload: NodeCommandRequest,
    user: CurrentUser,
    org: CurrentOrg,
    session: DbSession,
) -> NodeCommandResult:
    service = _service(session)
    node = await service.get_node(node_id, org_id=org.org_id)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "node_not_found", "message": "Node not found"},
        )
    if not get_agent_hub().is_online(str(node.id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "node_offline", "message": "Agent is not connected"},
        )
    command_payload = _command_payload(payload)
    try:
        return await service.dispatch_command(
            node, action=payload.action, payload=command_payload
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"code": "agent_timeout", "message": str(exc)},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "agent_error", "message": str(exc)},
        ) from exc


def _command_payload(payload: NodeCommandRequest) -> dict:
    """Validate and shape the action-specific payload."""
    action = payload.action
    if action == NodeCommandAction.PULL_IMAGE:
        if payload.pull is None:
            raise HTTPException(status_code=422, detail="pull payload required")
        return payload.pull.model_dump()
    if action == NodeCommandAction.RUN_CONTAINER:
        if payload.run is None:
            raise HTTPException(status_code=422, detail="run payload required")
        return payload.run.model_dump()
    if action in {NodeCommandAction.STOP_CONTAINER, NodeCommandAction.RESTART_CONTAINER}:
        if payload.ref is None:
            raise HTTPException(status_code=422, detail="ref payload required")
        return payload.ref.model_dump()
    if action == NodeCommandAction.COLLECT_LOGS:
        if payload.logs is None:
            raise HTTPException(status_code=422, detail="logs payload required")
        return payload.logs.model_dump()
    return {}


# --------------------------------------------------------------------------- #
# Agent registration (install-token authenticated; no user session)
# --------------------------------------------------------------------------- #


@router.post("/register", response_model=NodeCredentials)
async def register_agent(
    payload: NodeRegisterRequest,
    request: Request,
    session: DbSession,
) -> NodeCredentials:
    service = _service(session)
    try:
        node, secret = await service.register_agent(
            enrollment_token=payload.enrollment_token,
            hostname=payload.hostname,
            platform=payload.platform,
            agent_version=payload.agent_version,
            cpu_cores=payload.cpu_cores,
            mem_total_mb=payload.mem_total_mb,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "enrollment_failed", "message": str(exc)},
        ) from exc
    settings = get_settings()
    return NodeCredentials(
        node_id=node.id,
        agent_secret=secret,
        agent_ws_url=agent_install.agent_ws_url(settings, request_base_url=str(request.base_url)),
        heartbeat_interval_seconds=settings.agent_heartbeat_interval_seconds,
    )


# --------------------------------------------------------------------------- #
# Reverse tunnel (agent -> control plane)
# --------------------------------------------------------------------------- #


@ws_router.websocket("/ws/nodes/connect")
async def node_tunnel(websocket: WebSocket) -> None:
    """Agent's outbound WSS tunnel. HMAC-authenticated via query params."""
    await websocket.accept()

    async with AsyncSessionLocal() as db:
        node = await NodeRegistryService(db).authenticate_ws(dict(websocket.query_params))
    if node is None:
        await websocket.send_json({"type": "error", "message": "Authentication failed"})
        await websocket.close(code=4401)
        return

    node_id = node.id
    settings = get_settings()
    hub = get_agent_hub()
    await hub.register(str(node_id), websocket)
    await websocket.send_json(
        {
            "type": "ready",
            "node_id": str(node_id),
            "heartbeat_interval_seconds": settings.agent_heartbeat_interval_seconds,
        }
    )

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            text = message.get("text")
            if text is None:
                continue
            await _handle_agent_frame(node_id, text, websocket)
    except WebSocketDisconnect:
        logger.info("node_tunnel_disconnected", node_id=str(node_id))
    except Exception:
        logger.exception("node_tunnel_error", node_id=str(node_id))
    finally:
        await hub.unregister(str(node_id), websocket)
        async with AsyncSessionLocal() as db:
            await NodeRegistryService(db).mark_offline(node_id)


async def _handle_agent_frame(node_id: UUID, text: str, websocket: WebSocket) -> None:
    try:
        frame = json.loads(text)
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "message": "Invalid JSON frame"})
        return

    frame_type = frame.get("type")
    if frame_type == "heartbeat":
        try:
            telemetry = NodeTelemetry.model_validate(frame.get("telemetry", {}))
        except ValidationError:
            await websocket.send_json({"type": "error", "message": "Invalid telemetry"})
            return
        async with AsyncSessionLocal() as db:
            await NodeRegistryService(db).apply_heartbeat(node_id, telemetry)
        await websocket.send_json({"type": "heartbeat_ack"})
    elif frame_type == "command_result":
        command_id = frame.get("command_id")
        try:
            result = NodeCommandResult.model_validate(frame.get("result", {}))
        except ValidationError:
            logger.warning("node_command_result_invalid", node_id=str(node_id))
            return
        if command_id:
            get_agent_hub().resolve(str(command_id), result)
    else:
        await websocket.send_json(
            {"type": "error", "message": f"Unknown frame type: {frame_type}"}
        )

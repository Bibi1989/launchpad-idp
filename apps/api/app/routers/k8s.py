from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.deps.auth import get_current_user, get_user_from_websocket
from app.models.domain import User
from app.services.k8s_bundle import _namespace_name
from app.services.k8s_manager import get_k8s_manager
from app.services.provisioning import ProvisioningService
from app.services import workspace_files as ws_files

from app.core.secrets import decrypt_secret

logger = get_logger(__name__)
router = APIRouter()


class DeleteResourceRequest(BaseModel):
    kind: str
    namespace: str | None = None
    name: str


async def _resolve_workspace_namespace(
    workspace_id: UUID,
    user: User,
    session: AsyncSession,
    namespace: str | None = None,
) -> str:
    """Prefer explicit namespace; otherwise use lp-{workspace} from scaffold convention."""
    if namespace and namespace.strip():
        return namespace.strip()
    prov_svc = ProvisioningService(session)
    row = await prov_svc.get_workspace_for_owner(workspace_id, user)
    return _namespace_name(row.name)

async def _get_workspace_cloud_context(
    workspace_id: UUID,
    user: User,
    session: AsyncSession,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Helper to fetch workspace provider, cloud_config dict, and decrypted credentials dict."""
    prov_svc = ProvisioningService(session)
    provider = "local"
    cloud_config: dict[str, Any] = {}
    credentials: dict[str, Any] = {}

    try:
        ws_row = await prov_svc.get_workspace(workspace_id)
        provider = ws_row.provider or "local"

        if ws_row.encrypted_credentials:
            try:
                credentials = json.loads(decrypt_secret(ws_row.encrypted_credentials))
            except Exception:
                credentials = {}

        wiz_config = await prov_svc.get_wizard_config(workspace_id, user)
        if wiz_config and wiz_config.cloud:
            cloud_config = wiz_config.cloud.model_dump()
    except Exception as exc:
        logger.debug("load_workspace_cloud_context_failed", workspace_id=str(workspace_id), error=str(exc))

    return provider, cloud_config, credentials


async def _load_workspace_files_data(
    workspace_id: UUID,
    user: User,
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Read YAML/JSON files from the provisioning workspace root on disk."""
    try:
        from pathlib import Path

        prov_svc = ProvisioningService(session)
        row = await prov_svc.get_workspace_for_owner(workspace_id, user)
        workspace_dir = Path(row.root_dir)
        if not workspace_dir.is_dir():
            return []
        nodes = ws_files.list_file_tree(workspace_dir)
        result: list[dict[str, Any]] = []
        for node in nodes:
            path_str = str(node.get("path", ""))
            if node.get("type") == "file" and path_str.endswith((".yaml", ".yml", ".json")):
                try:
                    content = ws_files.read_file(workspace_dir, path_str)
                    result.append({"path": path_str, "content": content})
                except Exception:
                    pass
        return result
    except Exception as exc:
        logger.debug(
            "load_workspace_files_data_failed",
            workspace_id=str(workspace_id),
            error=str(exc),
        )
        return []


@router.get("/workspaces/{workspace_id}/k8s/context")
async def get_k8s_cluster_context(
    workspace_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Get active cluster context & connection health metadata."""
    provider, cloud_config, credentials = await _get_workspace_cloud_context(workspace_id, user, session)
    k8s = get_k8s_manager()
    ctx = k8s.acquire_cluster_context(
        str(workspace_id),
        provider=provider,
        cloud_config=cloud_config,
        credentials=credentials,
    )
    target_ns = await _resolve_workspace_namespace(workspace_id, user, session)
    return {
        "workspace_id": ctx.workspace_id,
        "provider": ctx.provider,
        "cluster_name": ctx.cluster_name,
        "context_name": ctx.context_name,
        "region": ctx.region,
        "status": ctx.status,
        "node_count": ctx.node_count,
        "control_plane_health": ctx.control_plane_health,
        "k8s_version": ctx.k8s_version,
        "last_synced_at": ctx.last_synced_at,
        "error_message": ctx.error_message,
        "target_namespace": target_ns,
    }


@router.get("/workspaces/{workspace_id}/k8s/resources")
async def get_k8s_resources(
    workspace_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    namespace: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """Fetch categorized resource grid items for workspace."""
    ns = await _resolve_workspace_namespace(workspace_id, user, session, namespace)
    provider, cloud_config, credentials = await _get_workspace_cloud_context(workspace_id, user, session)
    files_data = await _load_workspace_files_data(workspace_id, user, session)
    k8s = get_k8s_manager()
    items = k8s.get_resource_grid(
        str(workspace_id),
        files_data,
        namespace=ns,
        provider=provider,
        cloud_config=cloud_config,
        credentials=credentials,
    )
    return [
        {
            "id": item.id,
            "kind": item.kind,
            "name": item.name,
            "namespace": item.namespace,
            "status": item.status,
            "ready_replicas": item.ready_replicas,
            "age": item.age,
            "node": item.node,
            "ip": item.ip,
            "ports": item.ports,
            "endpoints": item.endpoints,
            "created_at": item.created_at,
            "manifest_yaml": item.manifest_yaml,
            "events": item.events,
        }
        for item in items
    ]


@router.post("/workspaces/{workspace_id}/k8s/apply")
async def apply_k8s_manifests(
    workspace_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    namespace: str | None = Query(default=None),
) -> StreamingResponse:
    """Stream stage-by-stage pipeline execution events for workspace manifests."""
    ns = await _resolve_workspace_namespace(workspace_id, user, session, namespace)
    provider, cloud_config, credentials = await _get_workspace_cloud_context(workspace_id, user, session)
    prov_svc = ProvisioningService(session)
    row = await prov_svc.get_workspace_for_owner(workspace_id, user)
    files_data = await _load_workspace_files_data(workspace_id, user, session)
    k8s = get_k8s_manager()

    async def event_generator():
        async for event in k8s.execute_apply_pipeline(
            str(workspace_id),
            files_data,
            namespace=ns,
            provider=provider,
            cloud_config=cloud_config,
            credentials=credentials,
            workspace_root=row.root_dir,
        ):
            payload = {
                "stage_id": event.stage_id,
                "stage_name": event.stage_name,
                "status": event.status,
                "timestamp": event.timestamp,
                "message": event.message,
                "details": event.details,
            }
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.delete("/workspaces/{workspace_id}/k8s/resource")
async def delete_k8s_resource(
    workspace_id: UUID,
    body: DeleteResourceRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Delete selected Kubernetes resource."""
    ns = await _resolve_workspace_namespace(workspace_id, user, session, body.namespace)
    provider, cloud_config, credentials = await _get_workspace_cloud_context(workspace_id, user, session)
    k8s = get_k8s_manager()
    return k8s.delete_resource(
        str(workspace_id),
        body.kind,
        ns,
        body.name,
        provider=provider,
        cloud_config=cloud_config,
        credentials=credentials,
    )


@router.get("/workspaces/{workspace_id}/k8s/describe")
async def describe_k8s_resource(
    workspace_id: UUID,
    kind: str,
    name: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    namespace: str | None = Query(default=None),
) -> dict[str, Any]:
    """Get describe spec metadata and event log table for selected resource."""
    ns = await _resolve_workspace_namespace(workspace_id, user, session, namespace)
    provider, cloud_config, credentials = await _get_workspace_cloud_context(workspace_id, user, session)
    files_data = await _load_workspace_files_data(workspace_id, user, session)
    k8s = get_k8s_manager()
    return k8s.describe_resource(
        str(workspace_id),
        kind,
        ns,
        name,
        files_data,
        provider=provider,
        cloud_config=cloud_config,
        credentials=credentials,
    )


@router.get("/workspaces/{workspace_id}/k8s/logs")
async def stream_k8s_pod_logs(
    workspace_id: UUID,
    pod_name: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    namespace: str | None = Query(default=None),
    container_name: str | None = None,
    tail_lines: int = 100,
) -> StreamingResponse:
    """Stream pod logs over Server-Sent Events (SSE)."""
    ns = await _resolve_workspace_namespace(workspace_id, user, session, namespace)
    k8s = get_k8s_manager()

    async def log_stream():
        async for line in k8s.stream_pod_logs(
            str(workspace_id), ns, pod_name, container_name, tail_lines
        ):
            yield f"data: {json.dumps({'line': line.strip()})}\n\n"

    return StreamingResponse(log_stream(), media_type="text/event-stream")


@router.websocket("/ws/k8s/exec/{workspace_id}")
async def k8s_exec_websocket(websocket: WebSocket, workspace_id: UUID) -> None:
    """Interactive WebSocket terminal backed by real ``kubectl exec -it``."""
    import fcntl
    import pty
    import select
    import struct
    import termios

    from app.core.database import AsyncSessionLocal

    await websocket.accept()
    user = await get_user_from_websocket(websocket)
    if user is None:
        await websocket.send_json({"type": "error", "message": "Authentication required"})
        await websocket.close(code=4401)
        return

    pod_name = (websocket.query_params.get("pod") or "").strip()
    container_name = (websocket.query_params.get("container") or "").strip() or None
    namespace_q = (websocket.query_params.get("namespace") or "").strip() or None
    cols = int(websocket.query_params.get("cols") or "120")
    rows = int(websocket.query_params.get("rows") or "40")

    if not pod_name:
        await websocket.send_json({"type": "error", "message": "Query param 'pod' is required"})
        await websocket.close(code=4400)
        return

    async with AsyncSessionLocal() as session:
        try:
            namespace = await _resolve_workspace_namespace(
                workspace_id, user, session, namespace_q
            )
            provider, cloud_config, credentials = await _get_workspace_cloud_context(
                workspace_id, user, session
            )
        except HTTPException as exc:
            detail = exc.detail
            msg = detail.get("message") if isinstance(detail, dict) else str(detail)
            await websocket.send_json({"type": "error", "message": msg})
            await websocket.close(code=4404)
            return

    k8s = get_k8s_manager()
    ctx = k8s.acquire_cluster_context(
        str(workspace_id),
        provider=provider,
        cloud_config=cloud_config,
        credentials=credentials,
    )
    kubectl_bin = shutil.which("kubectl")
    if not kubectl_bin:
        await websocket.send_json(
            {"type": "error", "message": "kubectl not found on the API host"}
        )
        await websocket.close(code=1011)
        return

    context_args = k8s._kubectl_context_args(ctx)
    probe = subprocess.run(
        [kubectl_bin, "get", "pod", pod_name, "-n", namespace, "-o", "name"] + context_args,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if probe.returncode != 0:
        err = (probe.stderr or probe.stdout or "").strip() or "pod not found"
        await websocket.send_json(
            {
                "type": "error",
                "message": (
                    f"Pod '{pod_name}' not found in namespace '{namespace}'. "
                    f"Apply manifests first, then exec a real Pod (not a synthetic name). ({err})"
                ),
            }
        )
        await websocket.close(code=4404)
        return

    # Resolve default container when not provided
    if not container_name:
        cprobe = subprocess.run(
            [
                kubectl_bin,
                "get",
                "pod",
                pod_name,
                "-n",
                namespace,
                "-o",
                "jsonpath={.spec.containers[0].name}",
            ]
            + context_args,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        container_name = (cprobe.stdout or "").strip() or "app"

    shell_candidates = ["/bin/sh", "/bin/bash"]
    cmd = [
        kubectl_bin,
        "exec",
        "-it",
        pod_name,
        "-n",
        namespace,
        "-c",
        container_name,
        *context_args,
        "--",
        shell_candidates[0],
    ]

    master_fd, slave_fd = pty.openpty()

    def _set_winsize(fd: int, r: int, c: int) -> None:
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", r, c, 0, 0))
        except OSError:
            pass

    _set_winsize(master_fd, rows, cols)
    pid = os.fork()
    if pid == 0:
        os.close(master_fd)
        os.setsid()
        try:
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        except OSError:
            pass
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.execvp(cmd[0], cmd)

    os.close(slave_fd)
    loop = asyncio.get_running_loop()
    output_queue: asyncio.Queue[bytes] = asyncio.Queue()
    stop = asyncio.Event()

    def _read_pty() -> None:
        try:
            while not stop.is_set():
                readable, _, _ = select.select([master_fd], [], [], 0.2)
                if not readable:
                    # Check child exit
                    waited_pid, status = os.waitpid(pid, os.WNOHANG)
                    if waited_pid == pid:
                        stop.set()
                        break
                    continue
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    stop.set()
                    break
                if not chunk:
                    stop.set()
                    break
                loop.call_soon_threadsafe(output_queue.put_nowait, chunk)
        finally:
            stop.set()

    reader_future = loop.run_in_executor(None, _read_pty)

    await websocket.send_json(
        {
            "type": "ready",
            "workspace_id": str(workspace_id),
            "pod": pod_name,
            "container": container_name,
            "namespace": namespace,
            "cols": cols,
            "rows": rows,
        }
    )

    async def _pump_output() -> None:
        while not stop.is_set() or not output_queue.empty():
            try:
                chunk = await asyncio.wait_for(output_queue.get(), timeout=0.3)
            except TimeoutError:
                continue
            try:
                await websocket.send_text(chunk.decode("utf-8", errors="replace"))
            except Exception:
                stop.set()
                break

    pump_task = asyncio.create_task(_pump_output())

    try:
        while not stop.is_set():
            try:
                msg = await asyncio.wait_for(websocket.receive(), timeout=0.5)
            except TimeoutError:
                continue
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                os.write(master_fd, text.encode("utf-8", errors="ignore"))
                continue
            msg_type = payload.get("type")
            if msg_type == "input":
                data = str(payload.get("data") or "")
                if data:
                    os.write(master_fd, data.encode("utf-8", errors="ignore"))
            elif msg_type == "resize":
                try:
                    _set_winsize(
                        master_fd,
                        int(payload.get("rows") or rows),
                        int(payload.get("cols") or cols),
                    )
                except Exception:
                    pass
    except WebSocketDisconnect:
        logger.info("k8s_exec_ws_disconnected", workspace_id=str(workspace_id), pod=pod_name)
    except Exception as exc:
        logger.exception("k8s_exec_ws_error", workspace_id=str(workspace_id), error=str(exc))
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        stop.set()
        pump_task.cancel()
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.kill(pid, 15)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
        await reader_future

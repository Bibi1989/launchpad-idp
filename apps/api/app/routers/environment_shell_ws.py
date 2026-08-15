"""WebSocket shell into an environment namespace (kubectl exec) or attach VM (SSH)."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import select
import shutil
import struct
import subprocess
import termios
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger, sanitize_log_message
from app.core.secrets import mask_terminal_output
from app.deps.auth import get_user_from_websocket
from app.models.domain import EnvironmentStatus
from app.schemas.k8s import DeployMode
from app.services.environment import EnvironmentService
from app.services.preview_ssh import resolve_preview_ssh_key_path

logger = get_logger(__name__)
router = APIRouter()


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


async def _pick_running_pod(namespace: str) -> tuple[str, str] | None:
    """Return (pod_name, container_name) for the first Running pod in namespace."""
    kubectl = shutil.which("kubectl")
    if not kubectl:
        return None
    listed = await asyncio.to_thread(
        subprocess.run,
        [
            kubectl,
            "get",
            "pods",
            "-n",
            namespace,
            "--field-selector=status.phase=Running",
            "-o",
            "jsonpath={range .items[*]}{.metadata.name}{'\\t'}{.spec.containers[0].name}{'\\n'}{end}",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if listed.returncode != 0:
        return None
    for line in (listed.stdout or "").splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 1 and parts[0]:
            pod = parts[0]
            container = parts[1] if len(parts) > 1 and parts[1] else "app"
            return pod, container
    return None


def _attach_ssh_target_from_running_instance(ri: object) -> tuple[str, str] | None:
    from app.schemas.cloud import RunningInstanceConfig

    if isinstance(ri, dict):
        try:
            cfg = RunningInstanceConfig.model_validate(ri)
        except Exception:
            return None
    elif isinstance(ri, RunningInstanceConfig):
        cfg = ri
    else:
        return None
    host = (cfg.host or "").strip()
    if not host:
        return None
    user = (cfg.ssh_user or "ubuntu").strip() or "ubuntu"
    return host, user


async def _resolve_attach_ssh(session, environment) -> tuple[str, str, str] | None:
    """Return (host, ssh_user, private_key_path) for attach-mode shells."""
    key_path = resolve_preview_ssh_key_path(str(environment.id))
    if not key_path:
        return None

    from app.services.teardown_context import parse_teardown_context

    ctx = parse_teardown_context(getattr(environment, "teardown_context_json", None))
    if ctx and isinstance(ctx.get("running_instance"), dict):
        target = _attach_ssh_target_from_running_instance(ctx["running_instance"])
        if target:
            return target[0], target[1], key_path

    workspace_id = getattr(environment, "workspace_id", None)
    if workspace_id is None:
        return None
    from app.models.domain import ProvisioningWorkspace
    from app.schemas.cloud import WorkspaceWizardConfig
    from app.services.provisioning import ProvisioningService

    workspace = await session.get(ProvisioningWorkspace, workspace_id)
    if workspace is None:
        return None
    snapshot = ProvisioningService(session)._load_wizard_snapshot(workspace)
    if not snapshot:
        return None
    try:
        wizard = WorkspaceWizardConfig.model_validate({**snapshot, "has_credentials": False})
    except Exception:
        return None
    target = _attach_ssh_target_from_running_instance(wizard.running_instance)
    if not target:
        return None
    return target[0], target[1], key_path


async def _pump_pty_json(
    websocket: WebSocket,
    *,
    master_fd: int,
    pid: int,
    stop: asyncio.Event,
) -> None:
    loop = asyncio.get_running_loop()
    output_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def _read_pty() -> None:
        try:
            while not stop.is_set():
                readable, _, _ = select.select([master_fd], [], [], 0.2)
                if not readable:
                    waited_pid, _status = os.waitpid(pid, os.WNOHANG)
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

    async def _send_output() -> None:
        while not stop.is_set() or not output_queue.empty():
            try:
                chunk = await asyncio.wait_for(output_queue.get(), timeout=0.3)
            except TimeoutError:
                continue
            text = mask_terminal_output(chunk.decode("utf-8", errors="replace"))
            try:
                await websocket.send_json({"type": "output", "data": text})
            except Exception:
                stop.set()
                break

    pump_task = asyncio.create_task(_send_output())
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
                continue
            msg_type = payload.get("type")
            if msg_type == "input":
                data = str(payload.get("data") or "")
                if data:
                    os.write(master_fd, data.encode("utf-8", errors="replace"))
            elif msg_type == "resize":
                next_cols = int(payload.get("cols") or 120)
                next_rows = int(payload.get("rows") or 40)
                _set_winsize(master_fd, next_rows, next_cols)
            elif msg_type == "kill":
                stop.set()
                break
    except WebSocketDisconnect:
        stop.set()
    finally:
        stop.set()
        pump_task.cancel()
        try:
            os.kill(pid, 15)
        except OSError:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        await asyncio.gather(reader_future, return_exceptions=True)


@router.websocket("/ws/environments/{environment_id}/shell")
async def environment_shell_websocket(websocket: WebSocket, environment_id: UUID) -> None:
    """Interactive shell: kubectl exec into preview namespace, or SSH for attach VMs."""
    await websocket.accept()
    user = await get_user_from_websocket(websocket)
    if user is None:
        await websocket.send_json({"type": "error", "message": "Authentication required"})
        await websocket.close(code=4401)
        return

    cols = int(websocket.query_params.get("cols") or "120")
    rows = int(websocket.query_params.get("rows") or "40")
    mode_hint = (websocket.query_params.get("mode") or "").strip().lower()

    prefer_ssh = False
    namespace = ""
    env_id = str(environment_id)
    ssh_tuple: tuple[str, str, str] | None = None

    async with AsyncSessionLocal() as session:
        try:
            environment = await EnvironmentService(session).get_environment_entity(
                environment_id, user
            )
        except Exception as exc:
            await websocket.send_json(
                {"type": "error", "message": sanitize_log_message(str(exc))[:300]}
            )
            await websocket.close(code=4404)
            return

        if environment.status in {
            EnvironmentStatus.DESTROYED,
            EnvironmentStatus.TEARDOWN_PENDING,
        }:
            await websocket.send_json(
                {"type": "error", "message": "Environment is destroyed or tearing down"}
            )
            await websocket.close(code=4409)
            return

        namespace = environment.namespace_name
        deploy_mode = (environment.deploy_mode or "").strip().lower()
        env_id = str(environment.id)
        prefer_ssh = mode_hint == "ssh" or deploy_mode == DeployMode.ATTACH.value
        if prefer_ssh:
            ssh_tuple = await _resolve_attach_ssh(session, environment)

    cmd: list[str] | None = None
    shell_mode = "kubectl-exec"
    target_label = namespace

    if prefer_ssh and ssh_tuple:
        host, ssh_user, key_path = ssh_tuple
        ssh_bin = shutil.which("ssh")
        if ssh_bin:
            cmd = [
                ssh_bin,
                "-tt",
                "-i",
                key_path,
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "LogLevel=ERROR",
                f"{ssh_user}@{host}",
            ]
            shell_mode = "ssh"
            target_label = f"{ssh_user}@{host}"

    if cmd is None:
        kubectl = shutil.which("kubectl")
        if not kubectl:
            await websocket.send_json(
                {"type": "error", "message": "kubectl not found on the API host"}
            )
            await websocket.close(code=1011)
            return
        picked = await _pick_running_pod(namespace)
        if picked is None:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": (
                        f"No Running pods in namespace '{namespace}'. "
                        "Wait until the preview is RUNNING, or use Logs mode."
                    ),
                }
            )
            await websocket.close(code=4404)
            return
        pod_name, container_name = picked
        cmd = [
            kubectl,
            "exec",
            "-it",
            pod_name,
            "-n",
            namespace,
            "-c",
            container_name,
            "--",
            "/bin/sh",
        ]
        shell_mode = "kubectl-exec"
        target_label = f"{namespace}/{pod_name}"

    master_fd, slave_fd = pty.openpty()
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
    stop = asyncio.Event()
    await websocket.send_json(
        {
            "type": "ready",
            "session_id": env_id,
            "mode": shell_mode,
            "target": target_label,
            "cols": cols,
            "rows": rows,
        }
    )
    logger.info(
        "environment_shell_started",
        environment_id=env_id,
        mode=shell_mode,
        target=target_label,
    )
    try:
        await _pump_pty_json(websocket, master_fd=master_fd, pid=pid, stop=stop)
    except Exception:
        logger.exception("environment_shell_failed", environment_id=env_id)
        try:
            await websocket.send_json({"type": "error", "message": "Shell stream failed"})
        except Exception:
            pass
    finally:
        try:
            await websocket.send_json({"type": "status", "status": "killed"})
        except Exception:
            pass

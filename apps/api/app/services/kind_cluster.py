"""Manage the local Kubernetes cluster used by Dev / Launch → Local.

Supports two interchangeable engines, selected by ``settings.local_k8s_engine``:

* ``k3s`` (default) - real k3s running in Docker via **k3d**. Context ``k3d-<name>``.
* ``kind``          - Kubernetes-in-Docker. Context ``kind-<name>``.

The public helpers keep their historical ``*_kind_*`` names so existing callers
(routers, provisioning, environment) need no change; internally they dispatch to
the active engine's CLI and lifecycle script.
"""

from __future__ import annotations

import asyncio
import os
import signal
import shutil
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Wall-clock cap for scripts/{k3s,kind}-up.sh. Without this, a stuck docker pull /
# k3d image import / ingress rollout leaves previews at PROVISION_INITIATED forever.
LOCAL_CLUSTER_UP_TIMEOUT_SECONDS = 240.0
LOCAL_CLUSTER_DOWN_TIMEOUT_SECONDS = 120.0


def _repo_root() -> Path:
    """Resolve a usable working directory for lifecycle scripts.

    Monorepo checkout: ``.../launchpad/apps/api/app/services/kind_cluster.py`` → repo root.
    OCI / compose image: ``/app/app/services/kind_cluster.py`` → ``/app`` (parents[4] does not exist).
    """
    here = Path(__file__).resolve()
    parents = list(here.parents)
    if len(parents) > 4:
        candidate = parents[4]
        if (candidate / "scripts").is_dir() and (candidate / "apps").is_dir():
            return candidate
    # Slim API image layout used by deploy/oci/Dockerfile.api
    if Path("/app/app").is_dir():
        return Path("/app")
    if len(parents) > 4:
        return parents[4]
    return Path.cwd()


def _engine() -> str:
    return get_settings().local_k8s_engine


def _cluster_tool() -> str:
    """CLI binary that manages the active engine's cluster (``k3d`` or ``kind``)."""
    return get_settings().local_cluster_tool


def _context_for(name: str) -> str:
    prefix = "k3d" if _engine() == "k3s" else "kind"
    return f"{prefix}-{name}"


def _script_name(action: str) -> str:
    """Lifecycle script for the active engine, e.g. ``k3s-up.sh`` / ``kind-down.sh``."""
    return f"{_engine()}-{action}.sh"


def _script_path(name: str) -> Path:
    settings = get_settings()
    configured = (settings.kind_scripts_dir or "").strip()
    if configured:
        return Path(configured).expanduser().resolve() / name
    return _repo_root() / "scripts" / name


def local_cluster_available() -> bool:
    """True when the active engine's CLI and kubectl are both installed."""
    return shutil.which(_cluster_tool()) is not None and shutil.which("kubectl") is not None


# Back-compat alias - historical name used across the codebase.
def kind_available() -> bool:
    return local_cluster_available()


async def _list_clusters() -> set[str]:
    """Names of existing local clusters for the active engine (empty on any error)."""
    tool = _cluster_tool()
    if not shutil.which(tool):
        return set()
    if tool == "k3d":
        cmd = ["k3d", "cluster", "list", "--no-headers"]
    else:
        cmd = ["kind", "get", "clusters"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await proc.communicate()
        if proc.returncode != 0:
            return set()
        out = stdout_b.decode("utf-8", errors="replace")
        # k3d prints "NAME SERVERS AGENTS ..."; kind prints one name per line.
        return {line.split()[0].strip() for line in out.splitlines() if line.strip()}
    except OSError as exc:
        logger.warning("local_cluster_list_failed", tool=tool, error=str(exc))
        return set()


async def _nodes_ready(context: str) -> bool:
    """True when at least one node reports Ready via kubectl (engine-agnostic)."""
    if not shutil.which("kubectl"):
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            "--context",
            context,
            "get",
            "nodes",
            "-o",
            'jsonpath={.items[*].status.conditions[?(@.type=="Ready")].status}',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await proc.communicate()
        if proc.returncode != 0:
            return False
        return "True" in stdout_b.decode("utf-8", errors="replace").split()
    except OSError as exc:
        logger.warning("local_cluster_nodes_probe_failed", context=context, error=str(exc))
        return False


async def _rewrite_loopback_kubeconfig_server(context: str) -> None:
    """Replace ``0.0.0.0`` apiserver hosts with ``127.0.0.1`` (k3d desktop quirk)."""
    if not shutil.which("kubectl"):
        return
    try:
        view = await asyncio.create_subprocess_exec(
            "kubectl",
            "config",
            "view",
            "--raw",
            "-o",
            f'jsonpath={{.clusters[?(@.name=="{context}")].cluster.server}}',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await view.communicate()
        if view.returncode != 0:
            return
        server = stdout_b.decode("utf-8", errors="replace").strip()
        if "0.0.0.0" not in server:
            return
        fixed = server.replace("0.0.0.0", "127.0.0.1")
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            "config",
            "set-cluster",
            context,
            f"--server={fixed}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode == 0:
            logger.info("local_cluster_kubeconfig_loopback_rewritten", context=context, server=fixed)
    except OSError as exc:
        logger.warning("local_cluster_kubeconfig_rewrite_failed", context=context, error=str(exc))


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate a lifecycle script and its children (new session / process group)."""
    if proc.returncode is not None or proc.pid is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except (TimeoutError, asyncio.TimeoutError):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            await proc.wait()
        except ProcessLookupError:
            pass


async def _run_lifecycle_script(
    action: str,
    name: str,
    *,
    extra_env: dict[str, str],
    timeout_seconds: float,
) -> tuple[int, str, str]:
    script = _script_path(_script_name(action))
    if not script.is_file():
        raise RuntimeError(f"{_script_name(action)} not found at {script}")

    env = os.environ.copy()
    env["KIND_CLUSTER_NAME"] = name  # shared cluster-name var across engines
    env["LOCAL_CLUSTER_NAME"] = name
    env["LOCAL_K8S_ENGINE"] = _engine()
    # When the cluster is already up, scripts should skip docker pull / image import.
    env.setdefault("PRELOAD_IMAGE", "0")
    env.setdefault("K3D_PRELOAD_IMAGE", "0")
    env.setdefault("KIND_PRELOAD_IMAGE", "0")
    env.update(extra_env)

    logger.info(
        "local_cluster_script_start",
        engine=_engine(),
        action=action,
        cluster=name,
        script=str(script),
        timeout_seconds=timeout_seconds,
    )
    proc = await asyncio.create_subprocess_exec(
        "bash",
        str(script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(_repo_root()),
        start_new_session=True,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        await _kill_process_tree(proc)
        logger.error(
            "local_cluster_script_timeout",
            engine=_engine(),
            action=action,
            cluster=name,
            timeout_seconds=timeout_seconds,
        )
        raise RuntimeError(
            f"{_engine()}-{action} for cluster '{name}' timed out after {timeout_seconds:.0f}s. "
            f"Check Docker Desktop, run `make {_engine()}-down && make {_engine()}-up`, "
            f"and remove a stale lock under /tmp/launchpad-{_engine()}-*.lockdir if needed."
        ) from exc

    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


async def ensure_kind_cluster(*, cluster_name: str | None = None) -> dict[str, str]:
    """Start the local cluster (idempotent) via the active engine. Raises on failure."""
    settings = get_settings()
    engine = _engine()
    if not settings.kind_auto_manage:
        logger.info("local_cluster_auto_manage_disabled", action="up", engine=engine)
        return {"status": "skipped", "reason": "auto_manage_disabled"}

    if not local_cluster_available():
        tool = _cluster_tool()
        install_hint = (
            "Install k3d (https://k3d.io - `brew install k3d`)"
            if tool == "k3d"
            else "Install kind (https://kind.sigs.k8s.io/)"
        )
        raise RuntimeError(
            f"{tool} and kubectl are required for Local ({engine}). {install_hint} and kubectl, then retry."
        )

    name = cluster_name or settings.kind_cluster_name
    context = _context_for(name)

    # Fast path: skip the heavy up script (ingress wait + image import) when Ready.
    if name in await _list_clusters() and await _nodes_ready(context):
        await _rewrite_loopback_kubeconfig_server(context)
        logger.info("local_cluster_already_ready", engine=engine, cluster=name, context=context)
        return {
            "status": "ready",
            "cluster": name,
            "engine": engine,
            "context": context,
            "output": f"{engine} cluster '{name}' already ready (skipped {_script_name('up')})",
        }

    returncode, stdout, stderr = await _run_lifecycle_script(
        "up",
        name,
        extra_env={
            "PREVIEW_NODE_PORT_MIN": str(settings.preview_node_port_min),
            "PREVIEW_NODE_PORT_MAX": str(settings.preview_node_port_max),
            "DEFAULT_WORKLOAD_IMAGE": settings.default_workload_image,
            # First bring-up may preload; keep best-effort but bounded by Python timeout.
            "PRELOAD_IMAGE": "1",
            "K3D_PRELOAD_IMAGE": "1",
            "KIND_PRELOAD_IMAGE": "1",
        },
        timeout_seconds=LOCAL_CLUSTER_UP_TIMEOUT_SECONDS,
    )
    if returncode != 0:
        logger.error(
            "local_cluster_up_failed",
            engine=engine,
            cluster=name,
            returncode=returncode,
            stdout=stdout[-2000:],
            stderr=stderr[-2000:],
        )
        detail = (stderr or stdout).strip() or f"{engine}-up exited {returncode}"
        raise RuntimeError(f"Failed to start {engine} cluster '{name}': {detail[:800]}")

    await _rewrite_loopback_kubeconfig_server(context)
    logger.info("local_cluster_up_ok", engine=engine, cluster=name)
    return {
        "status": "ready",
        "cluster": name,
        "engine": engine,
        "context": context,
        "output": stdout[-1500:],
    }


async def delete_kind_cluster(*, cluster_name: str | None = None) -> dict[str, str]:
    """Delete the local cluster via the active engine. Raises on hard failure."""
    settings = get_settings()
    engine = _engine()
    if not settings.kind_auto_manage:
        logger.info("local_cluster_auto_manage_disabled", action="down", engine=engine)
        return {"status": "skipped", "reason": "auto_manage_disabled"}

    if not local_cluster_available():
        logger.warning("local_cluster_down_skipped", reason="tool_or_kubectl_missing", engine=engine)
        return {"status": "skipped", "reason": "tool_or_kubectl_missing"}

    name = cluster_name or settings.kind_cluster_name
    returncode, stdout, stderr = await _run_lifecycle_script(
        "down",
        name,
        extra_env={},
        timeout_seconds=LOCAL_CLUSTER_DOWN_TIMEOUT_SECONDS,
    )
    if returncode != 0:
        logger.error(
            "local_cluster_down_failed",
            engine=engine,
            cluster=name,
            returncode=returncode,
            stdout=stdout[-2000:],
            stderr=stderr[-2000:],
        )
        detail = (stderr or stdout).strip() or f"{engine}-down exited {returncode}"
        raise RuntimeError(f"Failed to delete {engine} cluster '{name}': {detail[:800]}")

    logger.info("local_cluster_down_ok", engine=engine, cluster=name)
    return {"status": "deleted", "cluster": name, "engine": engine, "output": stdout[-1500:]}


async def probe_kind_cluster(*, cluster_name: str | None = None) -> dict[str, object]:
    """Read-only readiness probe for the active engine (never starts/deletes)."""
    settings = get_settings()
    engine = _engine()
    tool = _cluster_tool()
    name = cluster_name or settings.kind_cluster_name
    context = _context_for(name)
    tool_bin = shutil.which(tool) is not None
    kubectl_bin = shutil.which("kubectl") is not None
    auto_manage = bool(settings.kind_auto_manage)

    cluster_exists = False
    api_reachable = False

    if tool_bin:
        cluster_exists = name in await _list_clusters()

    if kubectl_bin and cluster_exists:
        api_reachable = await _nodes_ready(context)

    engine_label = "k3s" if engine == "k3s" else "kind"
    if not tool_bin or not kubectl_bin:
        status = "tools_missing"
        message = f"Install {tool} and kubectl to use Local ({engine_label}) previews."
        can_launch = False
    elif api_reachable:
        status = "ready"
        message = f"{engine_label} cluster '{name}' is reachable via context {context}."
        can_launch = True
    elif cluster_exists:
        status = "unreachable"
        message = (
            f"{engine_label} cluster '{name}' exists but no node is Ready. Recreating/starting cluster..."
        )
        can_launch = False
    elif auto_manage:
        status = "absent"
        message = (
            f"{engine_label} cluster '{name}' is not running yet. "
            "Launch will start it automatically (~1-2 min first time)."
        )
        can_launch = True
    else:
        status = "absent"
        message = (
            f"{engine_label} cluster '{name}' is not running and auto-manage is disabled. "
            f"Run scripts/{engine}-up.sh or enable KIND_AUTO_MANAGE."
        )
        can_launch = False

    return {
        "status": status,
        "cluster": name,
        "engine": engine,
        "tool": tool,
        "context": context,
        "kind_installed": tool_bin,  # historical key: the active engine's tool
        "kubectl_installed": kubectl_bin,
        "cluster_exists": cluster_exists,
        "api_reachable": api_reachable,
        "auto_manage": auto_manage,
        "message": message,
        "can_launch": can_launch,
    }

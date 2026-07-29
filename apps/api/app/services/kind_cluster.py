"""Manage the local kind cluster used by Dev (kind) / Launch → Local."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _repo_root() -> Path:
    # apps/api/app/services/kind_cluster.py → launchpad/
    return Path(__file__).resolve().parents[4]


def _script_path(name: str) -> Path:
    settings = get_settings()
    configured = (settings.kind_scripts_dir or "").strip()
    if configured:
        return Path(configured).expanduser().resolve() / name
    return _repo_root() / "scripts" / name


def kind_available() -> bool:
    return shutil.which("kind") is not None and shutil.which("kubectl") is not None


async def ensure_kind_cluster(*, cluster_name: str | None = None) -> dict[str, str]:
    """Run ``scripts/kind-up.sh`` (idempotent). Raises RuntimeError on failure."""
    settings = get_settings()
    if not settings.kind_auto_manage:
        logger.info("kind_auto_manage_disabled", action="up")
        return {"status": "skipped", "reason": "kind_auto_manage_disabled"}

    if not kind_available():
        raise RuntimeError(
            "kind and kubectl are required for Dev (kind). "
            "Install kind (https://kind.sigs.k8s.io/) and kubectl, then try again."
        )

    script = _script_path("kind-up.sh")
    if not script.is_file():
        raise RuntimeError(f"kind-up script not found at {script}")

    name = cluster_name or settings.kind_cluster_name
    env = os.environ.copy()
    env["KIND_CLUSTER_NAME"] = name
    env["PREVIEW_NODE_PORT_MIN"] = str(settings.preview_node_port_min)
    env["PREVIEW_NODE_PORT_MAX"] = str(settings.preview_node_port_max)
    env["DEFAULT_WORKLOAD_IMAGE"] = settings.default_workload_image

    logger.info("kind_cluster_up_start", cluster=name, script=str(script))
    proc = await asyncio.create_subprocess_exec(
        "bash",
        str(script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(_repo_root()),
    )
    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        logger.error(
            "kind_cluster_up_failed",
            cluster=name,
            returncode=proc.returncode,
            stdout=stdout[-2000:],
            stderr=stderr[-2000:],
        )
        detail = (stderr or stdout).strip() or f"kind-up exited {proc.returncode}"
        raise RuntimeError(f"Failed to start kind cluster '{name}': {detail[:800]}")

    logger.info("kind_cluster_up_ok", cluster=name)
    return {
        "status": "ready",
        "cluster": name,
        "context": f"kind-{name}",
        "output": stdout[-1500:],
    }


async def delete_kind_cluster(*, cluster_name: str | None = None) -> dict[str, str]:
    """Run ``scripts/kind-down.sh``. Raises RuntimeError on hard failure."""
    settings = get_settings()
    if not settings.kind_auto_manage:
        logger.info("kind_auto_manage_disabled", action="down")
        return {"status": "skipped", "reason": "kind_auto_manage_disabled"}

    if not kind_available():
        logger.warning("kind_cluster_down_skipped", reason="kind_or_kubectl_missing")
        return {"status": "skipped", "reason": "kind_or_kubectl_missing"}

    script = _script_path("kind-down.sh")
    if not script.is_file():
        raise RuntimeError(f"kind-down script not found at {script}")

    name = cluster_name or settings.kind_cluster_name
    env = os.environ.copy()
    env["KIND_CLUSTER_NAME"] = name

    logger.info("kind_cluster_down_start", cluster=name, script=str(script))
    proc = await asyncio.create_subprocess_exec(
        "bash",
        str(script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(_repo_root()),
    )
    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        logger.error(
            "kind_cluster_down_failed",
            cluster=name,
            returncode=proc.returncode,
            stdout=stdout[-2000:],
            stderr=stderr[-2000:],
        )
        detail = (stderr or stdout).strip() or f"kind-down exited {proc.returncode}"
        raise RuntimeError(f"Failed to delete kind cluster '{name}': {detail[:800]}")

    logger.info("kind_cluster_down_ok", cluster=name)
    return {"status": "deleted", "cluster": name, "output": stdout[-1500:]}


async def probe_kind_cluster(*, cluster_name: str | None = None) -> dict[str, object]:
    """Read-only Kind readiness probe (never starts or deletes the cluster)."""
    settings = get_settings()
    name = cluster_name or settings.kind_cluster_name
    context = f"kind-{name}"
    kind_bin = shutil.which("kind") is not None
    kubectl_bin = shutil.which("kubectl") is not None
    auto_manage = bool(settings.kind_auto_manage)

    cluster_exists = False
    api_reachable = False

    if kind_bin:
        try:
            proc = await asyncio.create_subprocess_exec(
                "kind",
                "get",
                "clusters",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, _ = await proc.communicate()
            if proc.returncode == 0:
                clusters = {
                    line.strip()
                    for line in stdout_b.decode("utf-8", errors="replace").splitlines()
                    if line.strip()
                }
                cluster_exists = name in clusters
        except OSError as exc:
            logger.warning("kind_get_clusters_failed", error=str(exc))

    if kubectl_bin and cluster_exists:
        try:
            proc = await asyncio.create_subprocess_exec(
                "kubectl",
                "--context",
                context,
                "cluster-info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode == 0:
                proc_cm = await asyncio.create_subprocess_exec(
                    "kubectl",
                    "--context",
                    context,
                    "get",
                    "pods",
                    "-n",
                    "kube-system",
                    "-l",
                    "component=kube-controller-manager",
                    "-o",
                    "jsonpath={.items[*].status.phase}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_cm, _ = await proc_cm.communicate()
                cm_phase = stdout_cm.decode("utf-8", errors="replace").strip()
                api_reachable = proc_cm.returncode == 0 and ("Running" in cm_phase or not cm_phase)
        except OSError as exc:
            logger.warning("kind_cluster_info_failed", error=str(exc), context=context)

    if not kind_bin or not kubectl_bin:
        status = "tools_missing"
        message = "Install kind and kubectl to use Local (kind) previews."
        can_launch = False
    elif api_reachable:
        status = "ready"
        message = f"Kind cluster '{name}' is reachable via context {context}."
        can_launch = True
    elif cluster_exists:
        status = "unreachable"
        message = (
            f"Kind cluster '{name}' control plane is unhealthy (kube-controller-manager not Running). "
            "Recreating/starting cluster..."
        )
        can_launch = False
    elif auto_manage:
        status = "absent"
        message = (
            f"Kind cluster '{name}' is not running yet. "
            "Launch will start it automatically (~1–2 min first time)."
        )
        can_launch = True
    else:
        status = "absent"
        message = (
            f"Kind cluster '{name}' is not running and auto-manage is disabled. "
            "Run scripts/kind-up.sh or enable KIND_AUTO_MANAGE."
        )
        can_launch = False

    return {
        "status": status,
        "cluster": name,
        "context": context,
        "kind_installed": kind_bin,
        "kubectl_installed": kubectl_bin,
        "cluster_exists": cluster_exists,
        "api_reachable": api_reachable,
        "auto_manage": auto_manage,
        "message": message,
        "can_launch": can_launch,
    }

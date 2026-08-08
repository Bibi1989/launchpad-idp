"""Local Docker Compose preview executor (no remoted Docker socket)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, sanitize_log_message
from app.services.kubernetes import ProvisionedResources

logger = get_logger(__name__)

COMPOSE_FILENAMES: tuple[str, ...] = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

_PROJECT_SAFE = re.compile(r"[^a-z0-9_-]+")


class ComposeDeployError(RuntimeError):
    """Docker Compose preview deploy / teardown failed."""


def find_compose_file(workspace_root: Path) -> Path | None:
    """Return the first compose file under the workspace root, if any."""
    for name in COMPOSE_FILENAMES:
        candidate = workspace_root / name
        if candidate.is_file():
            return candidate
    return None


def compose_project_name(*, namespace: str, environment_id: str) -> str:
    """Stable docker compose project name derived from the environment namespace."""
    raw = (namespace or f"lp-{environment_id[:8]}").strip().lower()
    cleaned = _PROJECT_SAFE.sub("-", raw).strip("-_")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = f"lp-{cleaned}" if cleaned else f"lp{environment_id.replace('-', '')[:12]}"
    return cleaned[:63]


def docker_compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "compose", "version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _run_compose(
    args: list[str],
    *,
    cwd: Path,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose", *args]
    logger.info("compose_exec", cmd=cmd, cwd=str(cwd))
    try:
        return subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ComposeDeployError(
            f"docker compose timed out after {timeout:.0f}s: {' '.join(cmd)}"
        ) from exc
    except OSError as exc:
        raise ComposeDeployError(f"docker compose failed to start: {exc}") from exc


def _preview_url_for_port(settings: Settings, port: int) -> str:
    host = (settings.preview_node_host or "").strip() or "127.0.0.1"
    if "://" in host:
        return f"{host.rstrip('/')}:{port}"
    return f"http://{host}:{port}"


def _first_published_port(
    *,
    project: str,
    compose_file: Path,
    cwd: Path,
) -> int | None:
    completed = _run_compose(
        ["-f", str(compose_file), "-p", project, "ps", "--format", "json"],
        cwd=cwd,
        timeout=60,
    )
    if completed.returncode != 0:
        logger.warning(
            "compose_ps_failed",
            stderr=sanitize_log_message((completed.stderr or "")[:400]),
        )
        return None

    raw = (completed.stdout or "").strip()
    if not raw:
        return None

    rows: list[dict[str, object]] = []
    # Compose v2 may emit one JSON object per line or a JSON array.
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            rows = [item for item in parsed if isinstance(item, dict)]
    else:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)

    for row in rows:
        publishers = row.get("Publishers") or row.get("publishers")
        if not isinstance(publishers, list):
            continue
        for pub in publishers:
            if not isinstance(pub, dict):
                continue
            published = pub.get("PublishedPort") or pub.get("published_port")
            try:
                port = int(published)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if port > 0:
                return port
    return None


def _default_listen_port_from_compose(compose_file: Path) -> int:
    try:
        text = compose_file.read_text(encoding="utf-8")
    except OSError:
        return 8080
    # Prefer host:container mappings like "8080:8080" or "3000:3000".
    match = re.search(r'["\']?(\d{2,5}):(\d{2,5})["\']?', text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return 8080


def deploy_compose(
    *,
    workspace_root: Path,
    namespace: str,
    environment_id: str,
    name: str,
    image: str | None = None,
    settings: Settings | None = None,
) -> ProvisionedResources:
    """Bring up the workspace compose stack and return preview resources."""
    cfg = settings or get_settings()
    root = workspace_root.expanduser().resolve()
    compose_file = find_compose_file(root)
    if compose_file is None:
        raise ComposeDeployError(
            "No docker-compose.yml (or compose.yml) found in the workspace root"
        )

    project = compose_project_name(namespace=namespace, environment_id=environment_id)
    resources = ProvisionedResources(
        namespace=namespace,
        labels={
            "launchpad.io/environment-id": environment_id,
            "launchpad.io/name": name,
            "launchpad.io/deploy-mode": "compose",
            "launchpad.io/compose-project": project,
        },
        image=image,
    )

    if not docker_compose_available():
        port = _default_listen_port_from_compose(compose_file)
        logger.warning(
            "compose_docker_unavailable_simulate",
            environment_id=environment_id,
            project=project,
        )
        resources.simulated = True
        resources.created_workload = True
        resources.node_port = port
        resources.preview_url = _preview_url_for_port(cfg, port)
        return resources

    up = _run_compose(
        ["-f", str(compose_file), "-p", project, "up", "-d", "--build", "--remove-orphans"],
        cwd=root,
        timeout=max(120.0, float(cfg.kubernetes_ready_timeout_seconds or 180)),
    )
    if up.returncode != 0:
        detail = sanitize_log_message((up.stderr or up.stdout or "compose up failed")[:800])
        raise ComposeDeployError(f"docker compose up failed: {detail}")

    port = _first_published_port(project=project, compose_file=compose_file, cwd=root)
    if port is None:
        port = _default_listen_port_from_compose(compose_file)

    resources.created_workload = True
    resources.node_port = port
    resources.preview_url = _preview_url_for_port(cfg, port)
    logger.info(
        "compose_deployed",
        environment_id=environment_id,
        project=project,
        preview_url=resources.preview_url,
        node_port=port,
    )
    return resources


def teardown_compose(
    *,
    workspace_root: Path | None,
    namespace: str,
    environment_id: str,
) -> None:
    """Stop and remove the compose project for an environment."""
    project = compose_project_name(namespace=namespace, environment_id=environment_id)
    if not docker_compose_available():
        logger.info("compose_teardown_skipped_no_docker", project=project)
        return

    cwd = workspace_root.expanduser().resolve() if workspace_root else Path.cwd()
    compose_file = find_compose_file(cwd) if workspace_root else None
    args = ["-p", project, "down", "--remove-orphans", "-v"]
    if compose_file is not None:
        args = ["-f", str(compose_file), *args]

    completed = _run_compose(args, cwd=cwd, timeout=120)
    if completed.returncode != 0:
        # Best-effort: try without -v / file if the first attempt failed.
        logger.warning(
            "compose_teardown_failed",
            project=project,
            stderr=sanitize_log_message((completed.stderr or "")[:400]),
        )
        fallback = _run_compose(
            ["-p", project, "down", "--remove-orphans"],
            cwd=cwd,
            timeout=120,
        )
        if fallback.returncode != 0:
            raise ComposeDeployError(
                sanitize_log_message((fallback.stderr or "compose down failed")[:400])
            )
    logger.info("compose_torn_down", project=project, environment_id=environment_id)


def stop_compose(
    *,
    workspace_root: Path | None,
    namespace: str,
    environment_id: str,
) -> None:
    """Pause a compose preview without removing volumes."""
    project = compose_project_name(namespace=namespace, environment_id=environment_id)
    if not docker_compose_available():
        return
    cwd = workspace_root.expanduser().resolve() if workspace_root else Path.cwd()
    compose_file = find_compose_file(cwd) if workspace_root else None
    args = ["-p", project, "stop"]
    if compose_file is not None:
        args = ["-f", str(compose_file), *args]
    completed = _run_compose(args, cwd=cwd, timeout=90)
    if completed.returncode != 0:
        logger.warning(
            "compose_stop_failed",
            project=project,
            stderr=sanitize_log_message((completed.stderr or "")[:400]),
        )


def start_compose(
    *,
    workspace_root: Path | None,
    namespace: str,
    environment_id: str,
) -> None:
    """Resume a previously stopped compose preview."""
    project = compose_project_name(namespace=namespace, environment_id=environment_id)
    if not docker_compose_available():
        return
    cwd = workspace_root.expanduser().resolve() if workspace_root else Path.cwd()
    compose_file = find_compose_file(cwd) if workspace_root else None
    args = ["-p", project, "start"]
    if compose_file is not None:
        args = ["-f", str(compose_file), *args]
    completed = _run_compose(args, cwd=cwd, timeout=90)
    if completed.returncode != 0:
        logger.warning(
            "compose_start_failed",
            project=project,
            stderr=sanitize_log_message((completed.stderr or "")[:400]),
        )

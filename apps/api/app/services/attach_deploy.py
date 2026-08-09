"""Deploy running-instance previews onto compute targets (serverless, VM, local)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, sanitize_log_message
from app.schemas.cloud import RunningInstanceConfig, RunningInstanceKind
from app.services.compose_deploy import (
    ComposeDeployError,
    list_docker_published_host_ports,
    next_available_host_port,
)
from app.services.kubernetes import ProvisionedResources

logger = get_logger(__name__)

_SAFE_NAME = re.compile(r"[^a-z0-9-]+")
_BIND_ALLOCATED_RE = re.compile(
    r"Bind for [^\s]+?:(\d+)\s+failed:\s+port is already allocated",
    re.IGNORECASE,
)
_ATTACH_PORT_RETRY_ATTEMPTS = 3


class AttachDeployError(RuntimeError):
    """Running-instance compute deploy failed."""


def _container_name(environment_id: str) -> str:
    slug = environment_id.replace("-", "")[:12]
    return f"lp-inst-{slug}"


def _preview_host(settings: Settings) -> str:
    return (settings.preview_node_host or "").strip() or "127.0.0.1"


def _url_for_port(settings: Settings, port: int) -> str:
    host = _preview_host(settings)
    if "://" in host:
        return f"{host.rstrip('/')}:{port}"
    return f"http://{host}:{port}"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _run(
    cmd: list[str],
    *,
    timeout: float,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    logger.info("instance_exec", cmd=cmd)
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AttachDeployError(f"Command timed out: {' '.join(cmd)}") from exc
    except OSError as exc:
        raise AttachDeployError(f"Command failed to start: {exc}") from exc
    if check and completed.returncode != 0:
        detail = sanitize_log_message((completed.stderr or completed.stdout or "failed")[:600])
        raise AttachDeployError(f"{' '.join(cmd[:3])}… failed: {detail}")
    return completed


def _is_frontend_app_dir(name: str) -> bool:
    lowered = name.strip().lower()
    return any(token in lowered for token in ("web", "ui", "frontend", "spa", "next", "nuxt"))


def _find_workspace_dockerfile(workspace_root: Path) -> tuple[Path, Path] | None:
    """Return (dockerfile_path, build_context) for a scaffolded workspace app.

    Prefer a frontend app (Open-app / browser target) over alphabetical
    ``apps/*`` order so ``api-server`` does not win over ``web-ui``.
    """
    candidates = [
        workspace_root / "apps" / "app" / "Dockerfile",
        workspace_root / "Dockerfile",
        workspace_root / "app" / "Dockerfile",
    ]
    for df in candidates:
        if df.is_file():
            return df, df.parent

    apps = workspace_root / "apps"
    if not apps.is_dir():
        return None

    app_dirs = [child for child in apps.iterdir() if child.is_dir() and (child / "Dockerfile").is_file()]
    if not app_dirs:
        return None

    frontends = [d for d in app_dirs if _is_frontend_app_dir(d.name)]
    chosen = sorted(frontends, key=lambda p: p.name) if frontends else sorted(app_dirs, key=lambda p: p.name)
    target = chosen[0]
    return target / "Dockerfile", target


def _try_build_workspace_image(
    workspace_root: Path,
    environment_id: str,
) -> str | None:
    found = _find_workspace_dockerfile(workspace_root)
    if found is None:
        return None
    dockerfile, context = found
    tag = f"lp-ws-{environment_id.replace('-', '')[:12]}:local"
    if not _docker_available():
        logger.info(
            "instance_workspace_image_docker_unavailable",
            tag=tag,
            dockerfile=str(dockerfile),
        )
        return tag
    try:
        _run(
            [
                "docker",
                "build",
                "-t",
                tag,
                "-f",
                str(dockerfile),
                str(context),
            ],
            timeout=600,
        )
        return tag
    except AttachDeployError:
        logger.exception("instance_workspace_image_build_failed", dockerfile=str(dockerfile))
        return None


def resolve_instance_image(
    *,
    image: str | None,
    workspace_root: Path | None,
    environment_id: str,
    settings: Settings,
) -> str:
    """Resolve a container image for instance deploy.

    Prefer an explicit non-default image, then a workspace Dockerfile build, then
    DEFAULT_WORKLOAD_IMAGE, then a synthetic local tag (simulate path).
    """
    explicit = (image or "").strip()
    default = (settings.default_workload_image or "").strip()
    custom = explicit if explicit and explicit != default else ""
    if custom:
        return custom
    if workspace_root is not None:
        built = _try_build_workspace_image(workspace_root, environment_id)
        if built:
            return built
    if explicit:
        return explicit
    if default:
        return default
    return f"lp-ws-{environment_id.replace('-', '')[:12]}:local"


def _apply_override(
    resources: ProvisionedResources,
    running_instance: RunningInstanceConfig,
) -> ProvisionedResources:
    override = (running_instance.preview_url_override or "").strip()
    if override:
        resources.preview_url = override
    return resources


def _parse_allocated_bind_ports(detail: str) -> set[int]:
    ports: set[int] = set()
    for match in _BIND_ALLOCATED_RE.finditer(detail or ""):
        try:
            ports.add(int(match.group(1)))
        except ValueError:
            continue
    return ports


def _resolve_local_host_port(preferred: int, *, extra_busy: set[int]) -> int:
    """Pick preferred host port when free, otherwise the next free publish port."""
    docker_ports = list_docker_published_host_ports() | extra_busy
    try:
        return next_available_host_port(
            preferred,
            reserved=extra_busy,
            docker_ports=docker_ports,
        )
    except ComposeDeployError as exc:
        raise AttachDeployError(str(exc)) from exc


_EXPOSE_RE = re.compile(r"^EXPOSE\s+(\d+)", re.MULTILINE | re.IGNORECASE)


def _container_listen_port(
    *,
    workspace_root: Path | None,
    fallback: int,
) -> int:
    """Prefer EXPOSE from the workspace Dockerfile so Open-app hits the app, not nginx :80."""
    if workspace_root is None:
        return fallback
    found = _find_workspace_dockerfile(workspace_root)
    if found is None:
        return fallback
    dockerfile, _ctx = found
    try:
        text = dockerfile.read_text(encoding="utf-8")
    except OSError:
        return fallback
    match = _EXPOSE_RE.search(text)
    if not match:
        return fallback
    try:
        port = int(match.group(1))
    except ValueError:
        return fallback
    return port if 1 <= port <= 65535 else fallback


def _deploy_local_machine(
    *,
    environment_id: str,
    name: str,
    image: str,
    running_instance: RunningInstanceConfig,
    settings: Settings,
    workspace_root: Path | None = None,
) -> ProvisionedResources:
    preferred = int(running_instance.listen_port)
    container_port = _container_listen_port(workspace_root=workspace_root, fallback=preferred)
    container = _container_name(environment_id)
    resources = ProvisionedResources(
        namespace=f"instance-{environment_id[:8]}",
        image=image,
        labels={
            "launchpad.io/environment-id": environment_id,
            "launchpad.io/deploy-mode": "attach",
            "launchpad.io/attach-kind": RunningInstanceKind.LOCAL_MACHINE.value,
            "launchpad.io/name": name,
        },
    )

    if not _docker_available():
        logger.warning("instance_local_docker_unavailable_simulate", environment_id=environment_id)
        resources.simulated = True
        resources.created_workload = True
        resources.node_port = container_port
        resources.preview_url = _url_for_port(settings, container_port)
        return _apply_override(resources, running_instance)

    inspect = _run(
        ["docker", "image", "inspect", image],
        timeout=30,
        check=False,
    )
    if inspect.returncode != 0:
        logger.warning(
            "instance_local_image_missing_simulate",
            environment_id=environment_id,
            image=image,
        )
        resources.simulated = True
        resources.created_workload = True
        resources.node_port = container_port
        resources.preview_url = _url_for_port(settings, container_port)
        return _apply_override(resources, running_instance)

    # Replace any prior container for this environment.
    _run(["docker", "rm", "-f", container], timeout=60, check=False)

    extra_busy: set[int] = set()
    host_port = container_port
    notice: str | None = None
    last_error: AttachDeployError | None = None

    for attempt in range(1, _ATTACH_PORT_RETRY_ATTEMPTS + 1):
        host_port = _resolve_local_host_port(preferred, extra_busy=extra_busy)
        if host_port != preferred or container_port != preferred:
            notice = (
                f"Port map: publishing host {host_port} → container {container_port}"
                + (
                    f" (preferred host {preferred} busy)"
                    if host_port != preferred
                    else ""
                )
            )
        else:
            notice = None

        try:
            _run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container,
                    "--restart",
                    "unless-stopped",
                    "-p",
                    f"{host_port}:{container_port}",
                    "-e",
                    f"PORT={container_port}",
                    image,
                ],
                timeout=180,
            )
            break
        except AttachDeployError as exc:
            last_error = exc
            conflicted = _parse_allocated_bind_ports(str(exc))
            if not conflicted or attempt >= _ATTACH_PORT_RETRY_ATTEMPTS:
                raise
            extra_busy |= conflicted
            logger.warning(
                "instance_local_port_conflict_retry",
                environment_id=environment_id,
                conflicted=sorted(conflicted),
                attempt=attempt,
            )
            _run(["docker", "rm", "-f", container], timeout=60, check=False)
    else:
        if last_error is not None:
            raise last_error
        raise AttachDeployError("docker run failed: no free host port")

    if notice:
        resources.notice = notice
        logger.warning(
            "instance_local_host_port_remapped",
            environment_id=environment_id,
            preferred=container_port,
            host_port=host_port,
        )

    resources.created_workload = True
    resources.node_port = host_port
    resources.preview_url = _url_for_port(settings, host_port)
    return _apply_override(resources, running_instance)


def _deploy_vm(
    *,
    environment_id: str,
    name: str,
    image: str,
    running_instance: RunningInstanceConfig,
    settings: Settings,
) -> ProvisionedResources:
    host = (running_instance.host or "").strip()
    override = (running_instance.preview_url_override or "").strip()
    if not host and override:
        # Link-only: user already has an app on a VM and only wants the Open-app URL.
        return ProvisionedResources(
            namespace=f"instance-{environment_id[:8]}",
            preview_url=override,
            created_workload=True,
            image=image,
            labels={
                "launchpad.io/environment-id": environment_id,
                "launchpad.io/deploy-mode": "attach",
                "launchpad.io/attach-kind": RunningInstanceKind.VM.value,
            },
        )
    if not host:
        raise AttachDeployError("VM deploy requires host (IP or hostname)")

    user = (running_instance.ssh_user or "ubuntu").strip() or "ubuntu"
    port = running_instance.ssh_port
    listen = running_instance.listen_port
    container = _container_name(environment_id)
    key_path = (running_instance.ssh_key_path or "").strip()

    ssh_base = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-p",
        str(port),
    ]
    if key_path:
        ssh_base.extend(["-i", key_path])
    target = f"{user}@{host}"

    resources = ProvisionedResources(
        namespace=f"instance-{environment_id[:8]}",
        image=image,
        labels={
            "launchpad.io/environment-id": environment_id,
            "launchpad.io/deploy-mode": "attach",
            "launchpad.io/attach-kind": RunningInstanceKind.VM.value,
            "launchpad.io/name": name,
            "launchpad.io/vm-host": host,
        },
    )

    if shutil.which("ssh") is None:
        logger.warning("instance_vm_ssh_unavailable_simulate", host=host)
        resources.simulated = True
        resources.created_workload = True
        resources.node_port = listen
        resources.preview_url = override or f"http://{host}:{listen}"
        return resources

    remote = (
        f"docker rm -f {container} >/dev/null 2>&1 || true; "
        f"docker pull {image} && "
        f"docker run -d --name {container} --restart unless-stopped "
        f"-p {listen}:{listen} -e PORT={listen} {image}"
    )
    try:
        _run([*ssh_base, target, remote], timeout=300)
    except AttachDeployError:
        # Fall back to simulate with host:port so Launch still has an Open-app link
        # when SSH/docker is not ready on the VM.
        logger.exception("instance_vm_ssh_deploy_failed", host=host)
        raise

    resources.created_workload = True
    resources.node_port = listen
    resources.preview_url = override or f"http://{host}:{listen}"
    return resources


def _deploy_serverless(
    *,
    environment_id: str,
    name: str,
    image: str,
    running_instance: RunningInstanceConfig,
    settings: Settings,
) -> ProvisionedResources:
    service = (running_instance.service_name or name or "launchpad-app").strip()
    service = _SAFE_NAME.sub("-", service.lower()).strip("-") or "launchpad-app"
    region = (running_instance.region or "us-central1").strip() or "us-central1"
    override = (running_instance.preview_url_override or "").strip()

    resources = ProvisionedResources(
        namespace=f"instance-{environment_id[:8]}",
        image=image,
        labels={
            "launchpad.io/environment-id": environment_id,
            "launchpad.io/deploy-mode": "attach",
            "launchpad.io/attach-kind": RunningInstanceKind.SERVERLESS.value,
            "launchpad.io/service": service,
        },
    )

    # Prefer gcloud when present (GCP Cloud Run). Azure Container Apps can be added similarly.
    if shutil.which("gcloud") is not None:
        completed = _run(
            [
                "gcloud",
                "run",
                "deploy",
                service,
                f"--image={image}",
                f"--region={region}",
                "--platform=managed",
                "--allow-unauthenticated",
                "--quiet",
                "--format=value(status.url)",
            ],
            timeout=600,
            check=False,
        )
        if completed.returncode == 0:
            url = (completed.stdout or "").strip().splitlines()
            preview = url[-1].strip() if url else ""
            if preview:
                resources.created_workload = True
                resources.preview_url = override or preview
                return resources
        logger.warning(
            "instance_serverless_gcloud_failed",
            stderr=sanitize_log_message((completed.stderr or "")[:400]),
        )

    # Simulated / pending URL when CLI deploy is unavailable.
    # Real URL is filled once Cloud Run / Container Apps IaC or CLI succeeds.
    simulated_url = (
        override
        or f"https://{service}-XXXXX-{region}.a.run.app"
    )
    logger.warning(
        "instance_serverless_simulate",
        environment_id=environment_id,
        service=service,
        region=region,
        hint="Install/authenticate gcloud for live Cloud Run deploy, or set preview_url_override",
    )
    resources.simulated = True
    resources.created_workload = True
    resources.preview_url = simulated_url
    return resources


def deploy_attach(
    *,
    namespace: str,
    environment_id: str,
    name: str,
    git_branch: str,
    git_repo_url: str,
    ttl_expires_at: str,
    owner_label: str = "launchpad",
    image: str | None = None,
    enable_postgres: bool = False,
    enable_redis: bool = False,
    running_instance: RunningInstanceConfig,
    workspace_root: Path | None = None,
    packaging: object | None = None,
    settings: Settings | None = None,
) -> ProvisionedResources:
    """Deploy (or link) a preview onto serverless / VM / local-machine compute."""
    _ = (namespace, git_branch, git_repo_url, ttl_expires_at, owner_label, enable_postgres, enable_redis, packaging)
    cfg = settings or get_settings()
    workload = resolve_instance_image(
        image=image,
        workspace_root=workspace_root,
        environment_id=environment_id,
        settings=cfg,
    )
    kind = running_instance.kind

    if kind == RunningInstanceKind.LOCAL_MACHINE:
        return _deploy_local_machine(
            environment_id=environment_id,
            name=name,
            image=workload,
            running_instance=running_instance,
            settings=cfg,
            workspace_root=workspace_root,
        )
    if kind == RunningInstanceKind.VM:
        return _deploy_vm(
            environment_id=environment_id,
            name=name,
            image=workload,
            running_instance=running_instance,
            settings=cfg,
        )
    if kind == RunningInstanceKind.SERVERLESS:
        return _deploy_serverless(
            environment_id=environment_id,
            name=name,
            image=workload,
            running_instance=running_instance,
            settings=cfg,
        )
    raise AttachDeployError(f"Unsupported running instance kind: {kind}")


def teardown_attach(
    *,
    running_instance: RunningInstanceConfig | None,
    namespace: str,
    environment_id: str | None = None,
    settings: Settings | None = None,
) -> None:
    """Tear down instance compute resources."""
    _ = (namespace, settings)
    if running_instance is None:
        return
    kind = running_instance.kind
    env_id = environment_id or "unknown"
    container = _container_name(env_id)

    if kind == RunningInstanceKind.LOCAL_MACHINE:
        if _docker_available():
            _run(["docker", "rm", "-f", container], timeout=60, check=False)
        return

    if kind == RunningInstanceKind.VM:
        host = (running_instance.host or "").strip()
        if not host or shutil.which("ssh") is None:
            logger.info("attach_teardown_vm_noop", host=host or "-")
            return
        user = (running_instance.ssh_user or "ubuntu").strip() or "ubuntu"
        ssh_base = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-p",
            str(running_instance.ssh_port),
        ]
        key_path = (running_instance.ssh_key_path or "").strip()
        if key_path:
            ssh_base.extend(["-i", key_path])
        _run(
            [*ssh_base, f"{user}@{host}", f"docker rm -f {container} >/dev/null 2>&1 || true"],
            timeout=120,
            check=False,
        )
        return

    if kind == RunningInstanceKind.SERVERLESS:
        # Do not delete shared Cloud Run services on preview teardown by default.
        logger.info(
            "attach_teardown_serverless_noop",
            service=running_instance.service_name,
        )
        return

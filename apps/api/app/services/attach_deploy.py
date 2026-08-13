"""Deploy running-instance previews onto compute targets (serverless, VM, local)."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, sanitize_log_message
from app.schemas.cloud import (
    CloudCredentials,
    CloudProvider,
    ContainerServiceSpec,
    InstanceCodeSource,
    InstanceProcessStrategy,
    InstanceReverseProxy,
    RunningInstanceConfig,
    RunningInstanceKind,
    WorkspaceRuntimeMode,
)
from app.services.service_kind import is_frontend_app_kind, is_frontend_service_name
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


@dataclass(frozen=True)
class _AttachServicePlan:
    name: str
    app_kind: str
    listen_port: int
    expose_preview: bool
    dockerfile: Path
    context: Path


def _env_slug(environment_id: str) -> str:
    return environment_id.replace("-", "")[:12]


def _container_name(environment_id: str) -> str:
    return f"lp-inst-{_env_slug(environment_id)}"


def _service_container_name(environment_id: str, service_name: str) -> str:
    svc = _SAFE_NAME.sub("-", service_name.strip().lower()).strip("-") or "app"
    return f"lp-inst-{_env_slug(environment_id)}-{svc}"[:63]


def _network_name(environment_id: str) -> str:
    return f"lp-net-{_env_slug(environment_id)}"


def _sanitize_svc(name: str) -> str:
    cleaned = _SAFE_NAME.sub("-", (name or "app").strip().lower()).strip("-")
    return cleaned or "app"


def _find_service_dockerfile(
    workspace_root: Path,
    service_name: str,
    dockerfile_path: str | None = None,
) -> tuple[Path, Path] | None:
    slug = _sanitize_svc(service_name)
    candidates: list[Path] = []
    if dockerfile_path:
        candidates.append(workspace_root / dockerfile_path)
    candidates.extend(
        [
            workspace_root / "apps" / slug / "Dockerfile",
            workspace_root / "dockers" / slug / "Dockerfile",
            workspace_root / "dockers" / f"Dockerfile.{slug}",
            workspace_root / slug / "Dockerfile",
        ]
    )
    for df in candidates:
        if df.is_file():
            # dockers/Dockerfile.foo uses workspace root as context when no sibling dir
            context = df.parent if df.parent.name != "dockers" or (df.parent / "package.json").exists() else workspace_root
            if df.name.startswith("Dockerfile.") and df.parent.name == "dockers":
                app_ctx = workspace_root / "apps" / slug
                context = app_ctx if app_ctx.is_dir() else workspace_root
            return df, context
    return None


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
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    logger.info("instance_exec", cmd=cmd)
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise AttachDeployError(f"Command timed out: {' '.join(cmd)}") from exc
    except OSError as exc:
        raise AttachDeployError(f"Command failed to start: {exc}") from exc
    if check and completed.returncode != 0:
        raw = completed.stderr or completed.stdout or "failed"
        # Prefer the end of the log: remote scripts can be long and earlier lines
        # are often just wait-loop noise.
        clipped = raw[-1200:] if len(raw) > 1200 else raw
        detail = sanitize_log_message(clipped)
        raise AttachDeployError(f"{' '.join(cmd[:3])}… failed: {detail}")
    return completed


def _credential_env(
    credentials: CloudCredentials | None,
    *,
    environment_id: str,
) -> dict[str, str]:
    # Shared materialization (GCP SA key file + project env) for gcloud/aws CLIs.
    from app.services.cloud_instance_compute import _credential_env as _cic_credential_env

    return _cic_credential_env(credentials, environment_id=environment_id)


def _is_frontend_app_dir(name: str) -> bool:
    return is_frontend_service_name(name)


def _service_is_frontend(spec: ContainerServiceSpec | _AttachServicePlan) -> bool:
    return is_frontend_app_kind(
        str(getattr(spec, "app_kind", "") or ""),
        name=str(getattr(spec, "name", "") or ""),
    )


def _service_expose_preview(spec: ContainerServiceSpec) -> bool:
    if spec.expose_preview is True:
        return True
    if spec.expose_preview is False:
        return False
    return _service_is_frontend(spec)


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
    cloud_provider: str | None = None,
    credentials: CloudCredentials | None = None,
    region: str | None = None,
) -> str:
    """Resolve a container image for instance deploy.

    Prefer an explicit non-default external image, then build and push to the
    target cloud registry, then a local workspace Dockerfile build, then defaults.
    """
    explicit = (image or "").strip()
    default = (settings.default_workload_image or "").strip()
    custom = explicit if explicit and explicit != default else ""
    provider = (cloud_provider or CloudProvider.LOCAL.value).strip().lower()

    if custom and not (
        provider != CloudProvider.LOCAL.value
        and _is_ephemeral_local_image(custom)
    ):
        return custom

    if (
        provider != CloudProvider.LOCAL.value
        and workspace_root is not None
    ):
        from app.services.cloud_instance_compute import (
            CloudInstanceComputeError,
            build_and_push_cloud_image,
            is_ephemeral_local_image,
        )

        try:
            return build_and_push_cloud_image(
                workspace_root=workspace_root,
                environment_id=environment_id,
                cloud_provider=provider,
                credentials=credentials,
                region=(region or "us-central1").strip() or "us-central1",
            )
        except CloudInstanceComputeError as exc:
            if custom and not is_ephemeral_local_image(custom):
                return custom
            raise AttachDeployError(str(exc)) from exc

    if workspace_root is not None:
        built = _try_build_workspace_image(workspace_root, environment_id)
        if built:
            return built
    if custom:
        return custom
    if explicit:
        return explicit
    if default:
        return default
    return f"lp-ws-{environment_id.replace('-', '')[:12]}:local"


def _is_ephemeral_local_image(image: str) -> bool:
    from app.services.cloud_instance_compute import is_ephemeral_local_image

    return is_ephemeral_local_image(image)


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


def _build_attach_service_plans(
    workspace_root: Path | None,
    services: list[ContainerServiceSpec] | None,
) -> list[_AttachServicePlan]:
    if workspace_root is None or not services:
        return []
    plans: list[_AttachServicePlan] = []
    for spec in services:
        found = _find_service_dockerfile(
            workspace_root,
            spec.name,
            dockerfile_path=spec.dockerfile_path,
        )
        if found is None:
            logger.warning(
                "attach_service_dockerfile_missing",
                service=spec.name,
                workspace=str(workspace_root),
            )
            continue
        dockerfile, context = found
        plans.append(
            _AttachServicePlan(
                name=_sanitize_svc(spec.name),
                app_kind="frontend" if _service_is_frontend(spec) else "backend",
                listen_port=int(spec.listen_port),
                expose_preview=_service_expose_preview(spec),
                dockerfile=dockerfile,
                context=context,
            )
        )
    # Always expose at least one preview target (prefer frontend).
    if plans and not any(p.expose_preview for p in plans):
        front = next((p for p in plans if p.app_kind == "frontend"), plans[0])
        plans = [
            _AttachServicePlan(
                name=p.name,
                app_kind=p.app_kind,
                listen_port=p.listen_port,
                expose_preview=p.name == front.name,
                dockerfile=p.dockerfile,
                context=p.context,
            )
            for p in plans
        ]
    return plans


def _endpoint_dict(
    *,
    name: str,
    app_kind: str,
    url: str,
    port: int | None,
    exposed: bool = True,
) -> dict[str, object]:
    return {
        "name": name,
        "app_kind": app_kind,
        "url": url,
        "port": port,
        "exposed": exposed,
    }


def _set_primary_preview(
    resources: ProvisionedResources,
    endpoints: list[dict[str, object]],
) -> None:
    resources.preview_endpoints = endpoints
    primary = next(
        (e for e in endpoints if e.get("app_kind") == "frontend" and e.get("exposed")),
        None,
    )
    if primary is None:
        primary = next((e for e in endpoints if e.get("exposed")), None)
    if primary is None and endpoints:
        primary = endpoints[0]
    if primary is not None:
        resources.preview_url = str(primary.get("url") or "") or None
        port = primary.get("port")
        resources.node_port = int(port) if isinstance(port, int) else None


def _teardown_local_containers(environment_id: str) -> None:
    if not _docker_available():
        return
    slug = _env_slug(environment_id)
    # Legacy single-container name + per-service names.
    _run(["docker", "rm", "-f", _container_name(environment_id)], timeout=60, check=False)
    listed = _run(
        ["docker", "ps", "-aq", "--filter", f"name=lp-inst-{slug}"],
        timeout=30,
        check=False,
    )
    ids = [line.strip() for line in (listed.stdout or "").splitlines() if line.strip()]
    if ids:
        _run(["docker", "rm", "-f", *ids], timeout=120, check=False)
    _run(["docker", "network", "rm", _network_name(environment_id)], timeout=30, check=False)


def _build_service_image(plan: _AttachServicePlan, environment_id: str) -> str:
    tag = f"lp-ws-{_env_slug(environment_id)}-{plan.name}:local"
    if not _docker_available():
        return tag
    _run(
        [
            "docker",
            "build",
            "-t",
            tag,
            "-f",
            str(plan.dockerfile),
            str(plan.context),
        ],
        timeout=600,
    )
    return tag


def _run_service_container(
    *,
    environment_id: str,
    plan: _AttachServicePlan,
    image: str,
    network: str,
    preferred_host: int,
    container_port: int,
    env_vars: dict[str, str],
    publish: bool,
    extra_busy: set[int],
) -> tuple[int | None, str | None]:
    """Start one service container. Returns (host_port|None, notice|None)."""
    container = _service_container_name(environment_id, plan.name)
    _run(["docker", "rm", "-f", container], timeout=60, check=False)

    if not publish:
        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--restart",
            "unless-stopped",
            "--network",
            network,
            "--network-alias",
            plan.name,
            "-e",
            f"PORT={container_port}",
        ]
        for key, value in env_vars.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(image)
        _run(cmd, timeout=180)
        return None, None

    host_port = preferred_host
    notice: str | None = None
    last_error: AttachDeployError | None = None
    for attempt in range(1, _ATTACH_PORT_RETRY_ATTEMPTS + 1):
        host_port = _resolve_local_host_port(preferred_host, extra_busy=extra_busy)
        if host_port != preferred_host or container_port != preferred_host:
            notice = (
                f"{plan.name}: host {host_port} → container {container_port}"
                + (f" (preferred {preferred_host} busy)" if host_port != preferred_host else "")
            )
        else:
            notice = None
        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--restart",
            "unless-stopped",
            "--network",
            network,
            "--network-alias",
            plan.name,
            "-p",
            f"{host_port}:{container_port}",
            "-e",
            f"PORT={container_port}",
        ]
        for key, value in env_vars.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(image)
        try:
            _run(cmd, timeout=180)
            return host_port, notice
        except AttachDeployError as exc:
            last_error = exc
            conflicted = _parse_allocated_bind_ports(str(exc))
            if not conflicted or attempt >= _ATTACH_PORT_RETRY_ATTEMPTS:
                raise
            extra_busy |= conflicted
            _run(["docker", "rm", "-f", container], timeout=60, check=False)
    if last_error is not None:
        raise last_error
    raise AttachDeployError(f"docker run failed for {plan.name}")


def _deploy_local_machine_multi(
    *,
    environment_id: str,
    name: str,
    running_instance: RunningInstanceConfig,
    settings: Settings,
    plans: list[_AttachServicePlan],
) -> ProvisionedResources:
    resources = ProvisionedResources(
        namespace=f"instance-{environment_id[:8]}",
        labels={
            "launchpad.io/environment-id": environment_id,
            "launchpad.io/deploy-mode": "attach",
            "launchpad.io/attach-kind": RunningInstanceKind.LOCAL_MACHINE.value,
            "launchpad.io/name": name,
        },
    )
    host_preferred_frontend = int(running_instance.listen_port)
    backends = [p for p in plans if p.app_kind == "backend"]
    frontends = [p for p in plans if p.app_kind == "frontend"]
    primary_backend = backends[0] if backends else None

    if not _docker_available():
        resources.simulated = True
        resources.created_workload = True
        endpoints: list[dict[str, object]] = []
        for plan in plans:
            if not plan.expose_preview:
                continue
            preferred = (
                host_preferred_frontend
                if plan.app_kind == "frontend"
                else int(plan.listen_port)
            )
            endpoints.append(
                _endpoint_dict(
                    name=plan.name,
                    app_kind=plan.app_kind,
                    url=_url_for_port(settings, preferred),
                    port=preferred,
                )
            )
        _set_primary_preview(resources, endpoints)
        resources.image = f"lp-ws-{_env_slug(environment_id)}-multi:local"
        return _apply_override(resources, running_instance)

    network = _network_name(environment_id)
    _teardown_local_containers(environment_id)
    _run(["docker", "network", "create", network], timeout=30, check=False)

    # Start backends first so frontends can resolve API aliases.
    ordered = [*backends, *frontends] if frontends or backends else list(plans)
    extra_busy: set[int] = set()
    notices: list[str] = []
    endpoints = []
    primary_image: str | None = None

    backend_ports: dict[str, int] = {}
    for plan in ordered:
        image = _build_service_image(plan, environment_id)
        if plan.app_kind == "frontend" or primary_image is None:
            primary_image = image
        container_port = _container_listen_port(
            workspace_root=plan.context if plan.context.is_dir() else None,
            fallback=plan.listen_port,
        )
        if plan.app_kind == "backend":
            backend_ports[plan.name] = container_port
        env_vars: dict[str, str] = {}
        if plan.app_kind == "frontend" and primary_backend is not None:
            be_port = backend_ports.get(
                primary_backend.name,
                primary_backend.listen_port,
            )
            api_url = f"http://{primary_backend.name}:{be_port}"
            env_vars.update(
                {
                    "API_URL": api_url,
                    "BACKEND_URL": api_url,
                    "NEXT_PUBLIC_API_URL": api_url,
                    "NUXT_PUBLIC_API_URL": api_url,
                }
            )
        preferred_host = (
            host_preferred_frontend
            if plan.app_kind == "frontend"
            else int(plan.listen_port)
        )
        host_port, notice = _run_service_container(
            environment_id=environment_id,
            plan=plan,
            image=image,
            network=network,
            preferred_host=preferred_host,
            container_port=container_port,
            env_vars=env_vars,
            publish=plan.expose_preview,
            extra_busy=extra_busy,
        )
        if notice:
            notices.append(notice)
        if host_port is not None:
            extra_busy.add(host_port)
            endpoints.append(
                _endpoint_dict(
                    name=plan.name,
                    app_kind=plan.app_kind,
                    url=_url_for_port(settings, host_port),
                    port=host_port,
                )
            )

    resources.created_workload = True
    resources.image = primary_image
    if notices:
        resources.notice = "; ".join(notices)
    _set_primary_preview(resources, endpoints)
    return _apply_override(resources, running_instance)


def _deploy_local_machine(
    *,
    environment_id: str,
    name: str,
    image: str,
    running_instance: RunningInstanceConfig,
    settings: Settings,
    workspace_root: Path | None = None,
    services: list[ContainerServiceSpec] | None = None,
) -> ProvisionedResources:
    plans = _build_attach_service_plans(workspace_root, services)
    # Multi-service instance: always run FE+BE on a shared network when the wizard
    # listed 2+ services (or we discovered 2+ Dockerfiles).
    if len(plans) >= 2 or (services is not None and len(services) >= 2 and len(plans) >= 1):
        if len(plans) < 2 and services is not None and len(services) >= 2:
            missing = [
                s.name
                for s in services
                if _sanitize_svc(s.name) not in {p.name for p in plans}
            ]
            logger.warning(
                "attach_multi_partial_plans",
                environment_id=environment_id,
                found=[p.name for p in plans],
                missing=missing,
            )
        if len(plans) >= 2:
            return _deploy_local_machine_multi(
                environment_id=environment_id,
                name=name,
                running_instance=running_instance,
                settings=settings,
                plans=plans,
            )

    preferred = int(running_instance.listen_port)
    if len(plans) == 1:
        plan = plans[0]
        built = _build_service_image(plan, environment_id) if _docker_available() else image
        image = built
        container_port = plan.listen_port
        if plan.context.is_dir():
            container_port = _container_listen_port(
                workspace_root=plan.context,
                fallback=plan.listen_port,
            )
        app_kind = plan.app_kind
        service_name = plan.name
    else:
        container_port = _container_listen_port(
            workspace_root=workspace_root,
            fallback=preferred,
        )
        app_kind = "frontend"
        service_name = "app"

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
        resources.node_port = preferred
        resources.preview_url = _url_for_port(settings, preferred)
        _set_primary_preview(
            resources,
            [
                _endpoint_dict(
                    name=service_name,
                    app_kind=app_kind,
                    url=resources.preview_url or "",
                    port=preferred,
                )
            ],
        )
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
        resources.node_port = preferred
        resources.preview_url = _url_for_port(settings, preferred)
        _set_primary_preview(
            resources,
            [
                _endpoint_dict(
                    name=service_name,
                    app_kind=app_kind,
                    url=resources.preview_url or "",
                    port=preferred,
                )
            ],
        )
        return _apply_override(resources, running_instance)

    _teardown_local_containers(environment_id)

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
            preferred_host=preferred,
            container_port=container_port,
            host_port=host_port,
        )

    resources.created_workload = True
    resources.node_port = host_port
    resources.preview_url = _url_for_port(settings, host_port)
    _set_primary_preview(
        resources,
        [
            _endpoint_dict(
                name=service_name,
                app_kind=app_kind,
                url=resources.preview_url or "",
                port=host_port,
            )
        ],
    )
    return _apply_override(resources, running_instance)


def _normalize_cloud_provider(cloud_provider: str | None) -> str:
    raw = cloud_provider
    if hasattr(raw, "value"):
        raw = getattr(raw, "value")
    return (str(raw or CloudProvider.LOCAL.value)).strip().lower()


def _infer_cloud_provider(
    cloud_provider: str | None,
    credentials: CloudCredentials | None,
) -> str:
    """Prefer explicit provider; if missing/local, infer from vault credentials."""
    provider = _normalize_cloud_provider(cloud_provider)
    if provider != CloudProvider.LOCAL.value:
        return provider
    if credentials is None:
        return provider
    from app.core.secrets import has_aws_auth, has_gcp_auth

    if has_gcp_auth(credentials):
        return CloudProvider.GCP.value
    if has_aws_auth(credentials):
        return CloudProvider.AWS.value
    if credentials.azure_client_id and credentials.azure_client_secret:
        return CloudProvider.AZURE.value
    return provider


def resolve_attach_cloud_provider(
    *,
    environment_provider: str | None = None,
    workspace_provider: str | None = None,
    wizard_cloud_provider: str | None = None,
    credentials: CloudCredentials | None = None,
) -> str:
    """Resolve cloud provider for attach deploy (env → workspace → wizard → creds)."""
    for candidate in (environment_provider, workspace_provider, wizard_cloud_provider):
        provider = _normalize_cloud_provider(candidate)
        if provider != CloudProvider.LOCAL.value:
            return provider
    return _infer_cloud_provider(CloudProvider.LOCAL.value, credentials)


def _deploy_vm(
    *,
    environment_id: str,
    name: str,
    image: str | None,
    running_instance: RunningInstanceConfig,
    settings: Settings,
    cloud_provider: str | None = None,
    credentials: CloudCredentials | None = None,
    workspace_root: Path | None = None,
    git_repo_url: str = "",
    git_branch: str = "main",
    org_slug: str | None = None,
    create_vpc: bool = False,
    create_subnets: bool = False,
    gcp_project_id: str | None = None,
) -> ProvisionedResources:
    from app.services.cloud_instance_compute import CloudInstanceComputeError, provision_cloud_vm

    host = (running_instance.host or "").strip()
    override = (running_instance.preview_url_override or "").strip()
    provider = resolve_attach_cloud_provider(
        environment_provider=cloud_provider,
        credentials=credentials,
    )
    strategy = running_instance.process_strategy
    cloud_providers = {
        CloudProvider.GCP.value,
        CloudProvider.AWS.value,
        CloudProvider.AZURE.value,
    }

    # Cloud instance mode creates the VM for you. Host is only required for
    # attach-to-existing-SSH-target (provider=local / pre-set host).
    if not host and provider in cloud_providers:
        try:
            running_instance = provision_cloud_vm(
                running_instance=running_instance,
                environment_id=environment_id,
                environment_name=name,
                cloud_provider=provider,
                credentials=credentials,
                listen_port=running_instance.listen_port,
                org_slug=org_slug,
                create_vpc=create_vpc,
                create_subnets=create_subnets,
                gcp_project_id=gcp_project_id,
            )
            host = (running_instance.host or "").strip()
        except CloudInstanceComputeError as exc:
            raise AttachDeployError(str(exc)) from exc

    if not host and override:
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
        if provider in cloud_providers:
            raise AttachDeployError(
                f"Cloud VM provisioning for {provider} did not return an IP address"
            )
        # No host and no cloud provider that can create one (provider=local/unknown):
        # a remote VM has nowhere to attach. For a one-click preview, run the app via
        # local Docker on the operator host (same as local_machine) so the preview
        # still comes up. A real remote VM deploy needs either a host (attach to an
        # existing SSH target) or a cloud provider (gcp/aws/azure creates the VM).
        logger.warning(
            "attach_vm_without_host_fallback_local",
            environment_id=environment_id,
            provider=provider,
        )
        return _deploy_local_machine(
            environment_id=environment_id,
            name=name,
            image=image or "",
            running_instance=running_instance.model_copy(
                update={"kind": RunningInstanceKind.LOCAL_MACHINE}
            ),
            settings=settings,
            workspace_root=workspace_root,
        )

    listen = running_instance.listen_port
    resources = ProvisionedResources(
        namespace=f"instance-{environment_id[:8]}",
        image=image,
        labels={
            "launchpad.io/environment-id": environment_id,
            "launchpad.io/deploy-mode": "attach",
            "launchpad.io/attach-kind": RunningInstanceKind.VM.value,
            "launchpad.io/name": name,
            "launchpad.io/vm-host": host,
            "launchpad.io/process-strategy": strategy.value,
            "launchpad.io/code-source": running_instance.code_source.value,
            "launchpad.io/cloud-provider": provider,
        },
    )

    if strategy != InstanceProcessStrategy.DOCKER:
        return _deploy_vm_native(
            environment_id=environment_id,
            name=name,
            host=host,
            running_instance=running_instance,
            settings=settings,
            cloud_provider=provider,
            credentials=credentials,
            workspace_root=workspace_root,
            git_repo_url=git_repo_url,
            git_branch=git_branch,
            resources=resources,
            override=override,
        )

    return _deploy_vm_docker(
        environment_id=environment_id,
        name=name,
        image=image or "",
        host=host,
        running_instance=running_instance,
        settings=settings,
        cloud_provider=provider,
        credentials=credentials,
        resources=resources,
        override=override,
        listen=listen,
    )


def _wrap_remote_bash(command: str) -> str:
    """Run remote scripts via base64 so long multiline payloads survive SSH quoting.

    Avoid ``bash -x`` here: xtrace floods stderr and hides the real failure when
    we clip command output for errors.
    """
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    return f"echo {encoded} | base64 -d | bash -euo pipefail"


def _remote_shell(
    *,
    running_instance: RunningInstanceConfig,
    host: str,
    cloud_provider: str,
    credentials: CloudCredentials | None,
    environment_id: str,
    command: str,
    timeout: float = 600,
) -> None:
    provider = (cloud_provider or CloudProvider.LOCAL.value).strip().lower()
    instance_name = (running_instance.service_name or "").strip()
    zone = (running_instance.region or "").strip()
    remote_cmd = _wrap_remote_bash(command)
    if (
        provider == CloudProvider.GCP.value
        and instance_name
        and zone
        and shutil.which("gcloud") is not None
    ):
        env = _credential_env(credentials, environment_id=environment_id)
        _run(
            [
                "gcloud",
                "compute",
                "ssh",
                instance_name,
                f"--zone={zone}",
                "--command",
                remote_cmd,
                "--quiet",
            ],
            timeout=timeout,
            env=env,
        )
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
    if shutil.which("ssh") is None:
        raise AttachDeployError("ssh CLI is required for native VM deploy")
    _run([*ssh_base, f"{user}@{host}", remote_cmd], timeout=timeout)


def _wait_for_vm_ssh(
    *,
    running_instance: RunningInstanceConfig,
    host: str,
    cloud_provider: str,
    credentials: CloudCredentials | None,
    environment_id: str,
    attempts: int = 18,
) -> None:
    last_err: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            _remote_shell(
                running_instance=running_instance,
                host=host,
                cloud_provider=cloud_provider,
                credentials=credentials,
                environment_id=environment_id,
                command="echo launchpad-ready",
                timeout=60,
            )
            return
        except AttachDeployError as exc:
            last_err = exc
            import time

            time.sleep(10)
    raise AttachDeployError(
        f"VM SSH not ready after provisioning: {last_err or 'timeout'}"
    )


def _wait_for_vm_host_ready(
    *,
    running_instance: RunningInstanceConfig,
    host: str,
    cloud_provider: str,
    credentials: CloudCredentials | None,
    environment_id: str,
) -> None:
    """Wait until the VM can install packages (startup done / apt free)."""
    script = """
set -euo pipefail
apt_busy() {
  if command -v fuser >/dev/null 2>&1; then
    if sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \\
      || sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1 \\
      || sudo fuser /var/lib/dpkg/lock >/dev/null 2>&1; then
      return 0
    fi
  fi
  return 1
}
# Fast path: tools already present and apt is free (common on retry/reuse).
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  if ! apt_busy; then
    echo "host already package-ready"
    exit 0
  fi
fi
echo "waiting for launchpad vm bootstrap"
for i in $(seq 1 72); do
  if [ -f /var/lib/launchpad/vm-ready ] && ! apt_busy; then
    echo "vm-ready present"
    exit 0
  fi
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 && ! apt_busy; then
    echo "node ready before marker"
    exit 0
  fi
  sleep 5
done
echo "bootstrap wait timed out; continuing with best-effort package install"
exit 0
"""
    _remote_shell(
        running_instance=running_instance,
        host=host,
        cloud_provider=cloud_provider,
        credentials=credentials,
        environment_id=environment_id,
        command=script,
        timeout=480,
    )


def _app_dir_for_env(environment_id: str) -> str:
    return f"/opt/launchpad/{_env_slug(environment_id)}"


def _resolve_app_workdir_rel(workspace_root: Path | None) -> str:
    """Relative app directory inside the synced workspace (monorepo-aware)."""
    if workspace_root is None or not workspace_root.is_dir():
        return "."
    if (workspace_root / "package.json").is_file():
        return "."
    if (workspace_root / "requirements.txt").is_file() or (
        workspace_root / "pyproject.toml"
    ).is_file():
        return "."

    apps = workspace_root / "apps"
    if apps.is_dir():
        node_apps = [
            child
            for child in sorted(apps.iterdir())
            if child.is_dir() and (child / "package.json").is_file()
        ]
        if node_apps:
            frontends = [c for c in node_apps if _is_frontend_app_dir(c.name)]
            chosen = frontends[0] if frontends else node_apps[0]
            return f"apps/{chosen.name}"
        py_apps = [
            child
            for child in sorted(apps.iterdir())
            if child.is_dir()
            and (
                (child / "requirements.txt").is_file()
                or (child / "pyproject.toml").is_file()
            )
        ]
        if py_apps:
            return f"apps/{py_apps[0].name}"
    return "."


def _detect_start_command(workspace_root: Path | None, *, workdir_rel: str = ".") -> str:
    root = workspace_root
    if root is not None and workdir_rel not in {"", "."}:
        candidate = root / workdir_rel
        if candidate.is_dir():
            root = candidate
    if root is None:
        return "npm start"
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") if isinstance(data, dict) else None
            if isinstance(scripts, dict):
                if scripts.get("start"):
                    return "npm start"
                if scripts.get("preview"):
                    return "npm run preview -- --host 0.0.0.0 --port ${PORT}"
                if scripts.get("dev"):
                    return "npm run dev -- --host 0.0.0.0 --port ${PORT}"
        except Exception:
            pass
        return "npm start"
    if (root / "requirements.txt").is_file() or (root / "pyproject.toml").is_file():
        if (root / "main.py").is_file():
            return "python3 -m uvicorn main:app --host 0.0.0.0 --port ${PORT}"
        if (root / "app" / "main.py").is_file():
            return "python3 -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"
        return "python3 -m uvicorn main:app --host 0.0.0.0 --port ${PORT}"
    return "npm start"


def _vm_ensure_host_packages_script(*, strategy: InstanceProcessStrategy) -> str:
    """Idempotent host bootstrap: wait for locks, retry apt, install only if missing.

    Fast-path skips apt entirely when node/npm (and strategy tools) already exist.
    That avoids racing GCP first-boot startup / unattended-upgrades locks.
    """
    need_docker = strategy == InstanceProcessStrategy.DOCKER
    need_pm2 = strategy == InstanceProcessStrategy.PM2
    lines = [
        "set -euo pipefail",
        "export DEBIAN_FRONTEND=noninteractive",
        "export PATH=\"/usr/local/bin:$PATH\"",
        "apt_busy() {",
        "  if command -v fuser >/dev/null 2>&1; then",
        "    if sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \\",
        "      || sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1 \\",
        "      || sudo fuser /var/lib/dpkg/lock >/dev/null 2>&1; then",
        "      return 0",
        "    fi",
        "  fi",
        "  return 1",
        "}",
        "wait_apt_locks() {",
        "  local i",
        "  for i in $(seq 1 36); do",
        "    if ! apt_busy; then",
        "      return 0",
        "    fi",
        "    echo \"apt locked; waiting ${i}/36\" >&2",
        "    sleep 5",
        "  done",
        "  echo \"apt still locked after wait; holders:\" >&2",
        "  sudo fuser -v /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock "
        "/var/lib/dpkg/lock 2>&1 || true",
        "  return 1",
        "}",
        "apt_retry() {",
        "  local n=0",
        "  until \"$@\"; do",
        "    n=$((n+1))",
        "    if [ \"$n\" -ge 6 ]; then",
        "      echo \"apt command failed after retries: $*\" >&2",
        "      return 1",
        "    fi",
        "    echo \"apt busy; retry $n/6\" >&2",
        "    wait_apt_locks || true",
        "    sleep $((n * 5))",
        "  done",
        "}",
        "host_ready=1",
        "command -v node >/dev/null 2>&1 || host_ready=0",
        "command -v npm >/dev/null 2>&1 || host_ready=0",
        "command -v git >/dev/null 2>&1 || host_ready=0",
        "command -v curl >/dev/null 2>&1 || host_ready=0",
        "command -v python3 >/dev/null 2>&1 || host_ready=0",
    ]
    if need_pm2:
        lines.append("command -v pm2 >/dev/null 2>&1 || host_ready=0")
    if need_docker:
        lines.append("command -v docker >/dev/null 2>&1 || host_ready=0")
    lines.extend(
        [
            "if [ \"$host_ready\" = \"1\" ] && ! apt_busy; then",
            "  echo \"host packages already present; skipping apt\"",
            "else",
            "  wait_apt_locks",
            "  apt_retry sudo apt-get update -y",
            "  apt_retry sudo apt-get install -y --no-install-recommends \\",
            "    ca-certificates curl git build-essential python3 python3-pip python3-venv \\",
            "    ufw psmisc",
            "  if ! command -v npm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then",
            "    wait_apt_locks",
            "    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -",
            "    wait_apt_locks",
            "    apt_retry sudo apt-get install -y nodejs",
            "  fi",
            "fi",
            "node --version",
            "npm --version",
        ]
    )
    if need_pm2:
        lines.extend(
            [
                "if ! command -v pm2 >/dev/null 2>&1; then",
                "  sudo npm install -g pm2",
                "fi",
                "pm2 --version",
            ]
        )
    if need_docker:
        lines.extend(
            [
                "if ! command -v docker >/dev/null 2>&1; then",
                "  wait_apt_locks",
                "  curl -fsSL https://get.docker.com | sudo sh",
                "  sudo systemctl enable --now docker",
                "fi",
                "sudo usermod -aG docker \"$(whoami)\" || true",
            ]
        )
    lines.extend(
        [
            "if command -v ufw >/dev/null 2>&1; then",
            "  sudo ufw allow OpenSSH || true",
            "  sudo ufw allow ${PORT}/tcp || true",
            "  echo y | sudo ufw enable || true",
            "fi",
            "sudo mkdir -p /var/lib/launchpad",
            "sudo touch /var/lib/launchpad/vm-ready",
        ]
    )
    return "\n".join(lines) + "\n"


def _vm_reverse_proxy_script(*, proxy: InstanceReverseProxy, listen: int) -> str:
    """Install and configure an nginx/Caddy edge that proxies :80 -> app listen_port.

    Returns "" for InstanceReverseProxy.NONE. Best-effort and idempotent - a proxy
    failure must not fail the deploy (the app is still reachable on listen_port).
    """
    if proxy == InstanceReverseProxy.NGINX:
        conf = (
            "server {\n"
            "  listen 80 default_server;\n"
            "  server_name _;\n"
            "  location / {\n"
            f"    proxy_pass http://127.0.0.1:{listen};\n"
            "    proxy_http_version 1.1;\n"
            "    proxy_set_header Host $host;\n"
            "    proxy_set_header X-Real-IP $remote_addr;\n"
            "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "    proxy_set_header X-Forwarded-Proto $scheme;\n"
            "    proxy_set_header Upgrade $http_upgrade;\n"
            '    proxy_set_header Connection "upgrade";\n'
            "  }\n"
            "}\n"
        )
        return (
            "echo '==> Configuring nginx reverse proxy (:80 -> app)'\n"
            "if ! command -v nginx >/dev/null 2>&1; then\n"
            "  (sudo apt-get update -y && sudo apt-get install -y nginx) "
            "|| (command -v yum >/dev/null 2>&1 && sudo yum install -y nginx) || true\n"
            "fi\n"
            "sudo rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true\n"
            "sudo mkdir -p /etc/nginx/conf.d\n"
            f"sudo tee /etc/nginx/conf.d/launchpad.conf >/dev/null <<'NGINXEOF'\n{conf}NGINXEOF\n"
            "sudo nginx -t && (sudo systemctl restart nginx || sudo service nginx restart) || true\n"
        )
    if proxy == InstanceReverseProxy.CADDY:
        caddyfile = f":80 {{\n  reverse_proxy 127.0.0.1:{listen}\n}}\n"
        return (
            "echo '==> Configuring Caddy reverse proxy (:80 -> app)'\n"
            "if ! command -v caddy >/dev/null 2>&1; then\n"
            "  sudo apt-get update -y && sudo apt-get install -y debian-keyring "
            "debian-archive-keyring apt-transport-https curl || true\n"
            "  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' "
            "| sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg || true\n"
            "  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' "
            "| sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null || true\n"
            "  sudo apt-get update -y && sudo apt-get install -y caddy || true\n"
            "fi\n"
            "sudo mkdir -p /etc/caddy\n"
            f"sudo tee /etc/caddy/Caddyfile >/dev/null <<'CADDYEOF'\n{caddyfile}CADDYEOF\n"
            "sudo systemctl restart caddy || sudo service caddy restart || true\n"
        )
    return ""


def _native_bootstrap_and_start(
    *,
    strategy: InstanceProcessStrategy,
    app_dir: str,
    workdir_rel: str,
    listen: int,
    unit: str,
    start_command: str,
    reverse_proxy: InstanceReverseProxy = InstanceReverseProxy.NONE,
) -> str:
    """Shell script executed on the VM after code is present."""
    work = "." if workdir_rel in {"", "."} else workdir_rel.strip().lstrip("./")
    app_cwd = app_dir if work == "." else f"{app_dir}/{work}"
    # Expand PORT in start commands that use ${PORT}.
    start_expanded = start_command.replace("${PORT}", str(listen)).replace("$PORT", str(listen))
    ensure = _vm_ensure_host_packages_script(strategy=strategy).replace(
        "${PORT}",
        str(listen),
    )

    install_app = (
        f"cd {app_cwd}\n"
        f"export PORT={listen}\n"
        "export HOST=0.0.0.0\n"
        "export NODE_ENV=production\n"
        "export PATH=\"/usr/local/bin:$HOME/.local/bin:$PATH\"\n"
        "if [ -f package.json ]; then\n"
        "  if [ -f package-lock.json ]; then npm ci || npm install; "
        "elif [ -f pnpm-lock.yaml ] && command -v pnpm >/dev/null 2>&1; then pnpm install --frozen-lockfile || pnpm install; "
        "elif [ -f yarn.lock ] && command -v yarn >/dev/null 2>&1; then yarn install --frozen-lockfile || yarn install; "
        "else npm install; fi\n"
        "  if grep -q '\"build\"' package.json; then npm run build || true; fi\n"
        "elif [ -f requirements.txt ]; then\n"
        "  python3 -m pip install --user -r requirements.txt\n"
        "  export PATH=\"$HOME/.local/bin:$PATH\"\n"
        "elif [ -f pyproject.toml ]; then\n"
        "  python3 -m pip install --user .\n"
        "  export PATH=\"$HOME/.local/bin:$PATH\"\n"
        "fi\n"
    )

    if strategy == InstanceProcessStrategy.PM2:
        return (
            ensure
            + install_app
            + f"pm2 delete {unit} >/dev/null 2>&1 || true\n"
            + "if [ -f ecosystem.config.cjs ]; then\n"
            + "  pm2 start ecosystem.config.cjs\n"
            + "elif [ -f ../ecosystem.config.cjs ]; then\n"
            + "  pm2 start ../ecosystem.config.cjs\n"
            + f"else\n"
            + f"  pm2 start bash --name {unit} -- -lc {json.dumps(start_expanded)}\n"
            + "fi\n"
            + "pm2 save || true\n"
            + "sudo env PATH=$PATH pm2 startup systemd -u \"$(whoami)\" --hp \"$HOME\" || true\n"
            + f"pm2 describe {unit} >/dev/null 2>&1 || pm2 ls\n"
            + _vm_reverse_proxy_script(proxy=reverse_proxy, listen=listen)
        )

    if strategy == InstanceProcessStrategy.SYSTEMD:
        unit_file = f"/etc/systemd/system/{unit}.service"
        exec_start = start_expanded.replace('"', '\\"')
        return (
            ensure
            + install_app
            + f"sudo tee {unit_file} >/dev/null <<'EOF'\n"
            + "[Unit]\nDescription=Launchpad preview\nAfter=network-online.target\n"
            + "Wants=network-online.target\n\n"
            + "[Service]\nType=simple\n"
            + f"WorkingDirectory={app_cwd}\n"
            + f"Environment=PORT={listen}\n"
            + "Environment=HOST=0.0.0.0\n"
            + "Environment=NODE_ENV=production\n"
            + "Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
            + f'ExecStart=/bin/bash -lc "{exec_start}"\n'
            + "Restart=always\nRestartSec=3\n\n"
            + "[Install]\nWantedBy=multi-user.target\n"
            + "EOF\n"
            + "sudo systemctl daemon-reload\n"
            + f"sudo systemctl enable --now {unit}.service\n"
            + f"sudo systemctl restart {unit}.service\n"
            + f"sudo systemctl --no-pager --full status {unit}.service || true\n"
            + _vm_reverse_proxy_script(proxy=reverse_proxy, listen=listen)
        )

    # Docker strategy on VM is handled by _deploy_vm_docker; keep a safe fallback.
    return (
        ensure
        + install_app
        + f"echo 'Unsupported native strategy {strategy.value}; packages installed.'\n"
    )


def _sync_workspace_over_ssh(
    *,
    workspace_root: Path,
    app_dir: str,
    running_instance: RunningInstanceConfig,
    host: str,
    cloud_provider: str,
    credentials: CloudCredentials | None,
    environment_id: str,
) -> None:
    provider = (cloud_provider or CloudProvider.LOCAL.value).strip().lower()
    instance_name = (running_instance.service_name or "").strip()
    zone = (running_instance.region or "").strip()
    _remote_shell(
        running_instance=running_instance,
        host=host,
        cloud_provider=cloud_provider,
        credentials=credentials,
        environment_id=environment_id,
        command=f"sudo mkdir -p {app_dir} && sudo chown -R $(whoami) {app_dir}",
        timeout=120,
    )
    if (
        provider == CloudProvider.GCP.value
        and instance_name
        and zone
        and shutil.which("gcloud") is not None
    ):
        env = _credential_env(credentials, environment_id=environment_id)
        # gcloud scp recursive into remote app dir
        _run(
            [
                "gcloud",
                "compute",
                "scp",
                "--recurse",
                "--compress",
                f"--zone={zone}",
                str(workspace_root) + "/.",
                f"{instance_name}:{app_dir}/",
                "--quiet",
            ],
            timeout=900,
            env=env,
        )
        return

    user = (running_instance.ssh_user or "ubuntu").strip() or "ubuntu"
    if shutil.which("rsync") is not None:
        ssh_opts = f"ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p {running_instance.ssh_port}"
        key_path = (running_instance.ssh_key_path or "").strip()
        if key_path:
            ssh_opts += f" -i {key_path}"
        _run(
            [
                "rsync",
                "-az",
                "--delete",
                "-e",
                ssh_opts,
                f"{workspace_root}/",
                f"{user}@{host}:{app_dir}/",
            ],
            timeout=900,
        )
        return
    if shutil.which("scp") is None:
        raise AttachDeployError("rsync or scp is required to copy code over SSH")
    scp = ["scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-P", str(running_instance.ssh_port), "-r"]
    key_path = (running_instance.ssh_key_path or "").strip()
    if key_path:
        scp.extend(["-i", key_path])
    _run([*scp, f"{workspace_root}/.", f"{user}@{host}:{app_dir}/"], timeout=900)


def _clone_repo_on_vm(
    *,
    git_repo_url: str,
    git_branch: str,
    app_dir: str,
    running_instance: RunningInstanceConfig,
    host: str,
    cloud_provider: str,
    credentials: CloudCredentials | None,
    environment_id: str,
) -> None:
    repo = (git_repo_url or "").strip()
    if not repo:
        raise AttachDeployError(
            "GitHub code source requires a git repository URL on the environment"
        )
    branch = (git_branch or "main").strip() or "main"
    # Escape for remote shell
    safe_repo = repo.replace("'", "'\"'\"'")
    cmd = (
        "set -euo pipefail\n"
        f"sudo mkdir -p {app_dir} && sudo chown -R $(whoami) {app_dir}\n"
        f"if [ -d {app_dir}/.git ]; then\n"
        f"  cd {app_dir} && git fetch --all && git checkout {branch} && git pull --ff-only origin {branch}\n"
        "else\n"
        f"  rm -rf {app_dir}/* {app_dir}/.[!.]* 2>/dev/null || true\n"
        f"  git clone --branch {branch} --depth 1 '{safe_repo}' {app_dir}\n"
        "fi\n"
    )
    _remote_shell(
        running_instance=running_instance,
        host=host,
        cloud_provider=cloud_provider,
        credentials=credentials,
        environment_id=environment_id,
        command=cmd,
        timeout=900,
    )


def _deploy_vm_native(
    *,
    environment_id: str,
    name: str,
    host: str,
    running_instance: RunningInstanceConfig,
    settings: Settings,
    cloud_provider: str,
    credentials: CloudCredentials | None,
    workspace_root: Path | None,
    git_repo_url: str,
    git_branch: str,
    resources: ProvisionedResources,
    override: str,
) -> ProvisionedResources:
    _ = settings
    listen = running_instance.listen_port
    strategy = running_instance.process_strategy
    code_source = running_instance.code_source or InstanceCodeSource.SSH
    app_dir = _app_dir_for_env(environment_id)
    unit = _SAFE_NAME.sub("-", name.lower()).strip("-")[:48] or "launchpad-app"

    try:
        _wait_for_vm_ssh(
            running_instance=running_instance,
            host=host,
            cloud_provider=cloud_provider,
            credentials=credentials,
            environment_id=environment_id,
        )
        _wait_for_vm_host_ready(
            running_instance=running_instance,
            host=host,
            cloud_provider=cloud_provider,
            credentials=credentials,
            environment_id=environment_id,
        )
        if code_source == InstanceCodeSource.GITHUB:
            _clone_repo_on_vm(
                git_repo_url=git_repo_url,
                git_branch=git_branch,
                app_dir=app_dir,
                running_instance=running_instance,
                host=host,
                cloud_provider=cloud_provider,
                credentials=credentials,
                environment_id=environment_id,
            )
        else:
            if workspace_root is None or not workspace_root.is_dir():
                raise AttachDeployError(
                    "SSH code source requires a linked workspace on disk"
                )
            _sync_workspace_over_ssh(
                workspace_root=workspace_root,
                app_dir=app_dir,
                running_instance=running_instance,
                host=host,
                cloud_provider=cloud_provider,
                credentials=credentials,
                environment_id=environment_id,
            )

        workdir_rel = _resolve_app_workdir_rel(workspace_root)
        start_cmd = _detect_start_command(workspace_root, workdir_rel=workdir_rel)
        bootstrap = _native_bootstrap_and_start(
            strategy=strategy,
            app_dir=app_dir,
            workdir_rel=workdir_rel,
            listen=listen,
            unit=unit,
            start_command=start_cmd,
            reverse_proxy=running_instance.reverse_proxy,
        )
        _remote_shell(
            running_instance=running_instance,
            host=host,
            cloud_provider=cloud_provider,
            credentials=credentials,
            environment_id=environment_id,
            command=bootstrap,
            timeout=1800,
        )
    except AttachDeployError:
        logger.exception(
            "instance_vm_native_deploy_failed",
            host=host,
            strategy=strategy.value,
            code_source=code_source.value,
        )
        raise

    resources.created_workload = True
    resources.node_port = listen
    if override:
        resources.preview_url = override
    elif running_instance.reverse_proxy != InstanceReverseProxy.NONE:
        # nginx/Caddy front the app on :80.
        resources.preview_url = f"http://{host}"
    else:
        resources.preview_url = f"http://{host}:{listen}"
    resources.image = None
    return _apply_override(resources, running_instance)


def _deploy_vm_docker(
    *,
    environment_id: str,
    name: str,
    image: str,
    host: str,
    running_instance: RunningInstanceConfig,
    settings: Settings,
    cloud_provider: str,
    credentials: CloudCredentials | None,
    resources: ProvisionedResources,
    override: str,
    listen: int,
) -> ProvisionedResources:
    _ = (name, settings)
    if not image:
        raise AttachDeployError("Docker VM deploy requires a container image")
    user = (running_instance.ssh_user or "ubuntu").strip() or "ubuntu"
    port = running_instance.ssh_port
    container = _container_name(environment_id)
    key_path = (running_instance.ssh_key_path or "").strip()
    provider = (cloud_provider or CloudProvider.LOCAL.value).strip().lower()

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

    if shutil.which("ssh") is None and shutil.which("gcloud") is None:
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
    instance_name = (running_instance.service_name or "").strip()
    zone = (running_instance.region or "").strip()
    try:
        if (
            provider == CloudProvider.GCP.value
            and instance_name
            and zone
            and shutil.which("gcloud") is not None
        ):
            env = _credential_env(credentials, environment_id=environment_id)
            _run(
                [
                    "gcloud",
                    "compute",
                    "ssh",
                    instance_name,
                    f"--zone={zone}",
                    "--command",
                    remote,
                    "--quiet",
                ],
                timeout=600,
                env=env,
            )
        else:
            _run([*ssh_base, target, remote], timeout=300)
    except AttachDeployError:
        logger.exception("instance_vm_ssh_deploy_failed", host=host)
        raise

    resources.created_workload = True
    resources.node_port = listen
    resources.preview_url = override or f"http://{host}:{listen}"
    return resources


def _deploy_gcp_cloud_run(
    *,
    service: str,
    image: str,
    region: str,
    environment_id: str,
    environment_name: str,
    override: str,
    resources: ProvisionedResources,
    credentials: CloudCredentials | None,
) -> ProvisionedResources:
    env = _credential_env(credentials, environment_id=environment_id)
    if shutil.which("gcloud") is None:
        return resources
    label_env = _SAFE_NAME.sub("-", environment_id.lower()).strip("-")[:63]
    label_name = _SAFE_NAME.sub("-", (environment_name or "preview").lower()).strip("-")[:32]
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
            (
                f"--labels=launchpad-environment-id={label_env},"
                f"launchpad-env-name={label_name or 'preview'},"
                "launchpad-managed=true"
            ),
        ],
        timeout=600,
        check=False,
        env=env,
    )
    if completed.returncode == 0:
        url = (completed.stdout or "").strip().splitlines()
        preview = url[-1].strip() if url else ""
        if preview:
            resources.created_workload = True
            resources.preview_url = override or preview
            resources.simulated = False
            return resources
    logger.warning(
        "instance_serverless_gcloud_failed",
        stderr=sanitize_log_message((completed.stderr or "")[:400]),
    )
    return resources


def _deploy_aws_app_runner(
    *,
    service: str,
    image: str,
    region: str,
    environment_id: str,
    override: str,
    resources: ProvisionedResources,
    credentials: CloudCredentials | None,
) -> ProvisionedResources:
    env = _credential_env(credentials, environment_id=environment_id)
    env.setdefault("AWS_DEFAULT_REGION", region)
    env.setdefault("AWS_REGION", region)
    if shutil.which("aws") is None:
        return resources
    repo_type = "ECR_PUBLIC" if image.startswith("public.ecr.") else "ECR"
    source_cfg = (
        f"ImageRepository={{ImageIdentifier={image},ImageRepositoryType={repo_type}}},"
        "AutoDeploymentsEnabled=false"
    )
    completed = _run(
        [
            "aws",
            "apprunner",
            "create-service",
            "--service-name",
            service,
            "--source-configuration",
            source_cfg,
            "--instance-configuration",
            "Cpu=1 vCPU,Memory=2 GB",
            "--query",
            "Service.ServiceUrl",
            "--output",
            "text",
        ],
        timeout=600,
        check=False,
        env=env,
    )
    if completed.returncode == 0:
        preview = (completed.stdout or "").strip()
        if preview and preview.startswith("http"):
            resources.created_workload = True
            resources.preview_url = override or preview
            resources.simulated = False
            return resources
    logger.warning(
        "instance_serverless_apprunner_failed",
        stderr=sanitize_log_message((completed.stderr or "")[:400]),
    )
    return resources


def _deploy_azure_container_apps(
    *,
    service: str,
    image: str,
    region: str,
    environment_id: str,
    override: str,
    resources: ProvisionedResources,
    credentials: CloudCredentials | None,
) -> ProvisionedResources:
    env = _credential_env(credentials, environment_id=environment_id)
    if shutil.which("az") is None:
        return resources
    rg = "launchpad-preview"
    sub = (credentials.azure_subscription_id if credentials else None) or env.get(
        "AZURE_SUBSCRIPTION_ID",
    )
    if sub and len(str(sub)) >= 8:
        rg = f"lp-{str(sub)[-8:].lower()}"
    completed = _run(
        [
            "az",
            "containerapp",
            "up",
            "--name",
            service,
            "--resource-group",
            rg,
            "--location",
            region,
            "--image",
            image,
            "--ingress",
            "external",
            "--target-port",
            "8080",
            "--query",
            "properties.configuration.ingress.fqdn",
            "--output",
            "tsv",
        ],
        timeout=900,
        check=False,
        env=env,
    )
    if completed.returncode == 0:
        fqdn = (completed.stdout or "").strip()
        if fqdn:
            resources.created_workload = True
            resources.preview_url = override or f"https://{fqdn}"
            resources.simulated = False
            return resources
    logger.warning(
        "instance_serverless_azure_failed",
        stderr=sanitize_log_message((completed.stderr or "")[:400]),
    )
    return resources


def _deploy_cloudflare_workers(
    *,
    service: str,
    image: str,
    region: str,
    environment_id: str,
    override: str,
    resources: ProvisionedResources,
    credentials: CloudCredentials | None,
    workspace_root: Path | None,
) -> ProvisionedResources:
    _ = (image, region)
    env = _credential_env(credentials, environment_id=environment_id)
    if workspace_root and shutil.which("wrangler") is not None:
        wrangler_toml = workspace_root / "wrangler.toml"
        if wrangler_toml.is_file():
            completed = _run(
                ["wrangler", "deploy", "--name", service],
                timeout=600,
                check=False,
                env=env,
                cwd=str(workspace_root),
            )
            if completed.returncode == 0:
                preview = override or f"https://{service}.workers.dev"
                resources.created_workload = True
                resources.preview_url = preview
                resources.simulated = False
                return resources
            logger.warning(
                "instance_serverless_wrangler_failed",
                stderr=sanitize_log_message((completed.stderr or "")[:400]),
            )
    return resources


def _deploy_serverless(
    *,
    environment_id: str,
    name: str,
    image: str,
    running_instance: RunningInstanceConfig,
    settings: Settings,
    cloud_provider: str | None = None,
    credentials: CloudCredentials | None = None,
    workspace_root: Path | None = None,
    org_slug: str | None = None,
) -> ProvisionedResources:
    from app.services.cloud_instance_compute import cloud_resource_name

    service = cloud_resource_name(
        environment_id=environment_id,
        environment_name=name,
        base_name=running_instance.service_name or name,
        org_slug=org_slug,
        max_len=49,
    )
    region = (running_instance.region or "us-central1").strip() or "us-central1"
    override = (running_instance.preview_url_override or "").strip()
    provider = (cloud_provider or CloudProvider.GCP.value).strip().lower()
    if provider == CloudProvider.LOCAL.value:
        provider = CloudProvider.GCP.value

    resources = ProvisionedResources(
        namespace=f"instance-{environment_id[:8]}",
        image=image,
        labels={
            "launchpad.io/environment-id": environment_id,
            "launchpad.io/deploy-mode": "attach",
            "launchpad.io/attach-kind": RunningInstanceKind.SERVERLESS.value,
            "launchpad.io/service": service,
            "launchpad.io/cloud-provider": provider,
        },
    )

    if provider == CloudProvider.GCP.value:
        resources = _deploy_gcp_cloud_run(
            service=service,
            image=image,
            region=region,
            environment_id=environment_id,
            environment_name=name,
            override=override,
            resources=resources,
            credentials=credentials,
        )
    elif provider == CloudProvider.AWS.value:
        if region == "us-central1":
            region = "us-east-1"
        resources = _deploy_aws_app_runner(
            service=service,
            image=image,
            region=region,
            environment_id=environment_id,
            override=override,
            resources=resources,
            credentials=credentials,
        )
    elif provider == CloudProvider.AZURE.value:
        if region == "us-central1":
            region = "eastus"
        resources = _deploy_azure_container_apps(
            service=service,
            image=image,
            region=region,
            environment_id=environment_id,
            override=override,
            resources=resources,
            credentials=credentials,
        )
    elif provider == CloudProvider.CLOUDFLARE.value:
        resources = _deploy_cloudflare_workers(
            service=service,
            image=image,
            region=region,
            environment_id=environment_id,
            override=override,
            resources=resources,
            credentials=credentials,
            workspace_root=workspace_root,
        )

    if resources.preview_url and not resources.simulated:
        return resources

    simulated_url = override or {
        CloudProvider.GCP.value: f"https://{service}-XXXXX-{region}.a.run.app",
        CloudProvider.AWS.value: f"https://{service}.awsapprunner.com",
        CloudProvider.AZURE.value: f"https://{service}.{region}.azurecontainerapps.io",
        CloudProvider.CLOUDFLARE.value: f"https://{service}.workers.dev",
    }.get(provider, f"https://{service}.example.com")
    logger.warning(
        "instance_serverless_simulate",
        environment_id=environment_id,
        service=service,
        region=region,
        provider=provider,
        hint="Install/authenticate cloud CLI for live deploy, or set preview_url_override",
    )
    resources.simulated = True
    resources.created_workload = True
    resources.preview_url = simulated_url
    return resources


def load_workspace_credentials(encrypted: str | None) -> CloudCredentials | None:
    if not encrypted:
        return None
    from app.core.secrets import decrypt_secret

    try:
        return CloudCredentials.model_validate_json(decrypt_secret(encrypted))
    except Exception:
        return None


def prepare_attach_deploy(
    *,
    running_instance: RunningInstanceConfig,
    cloud_provider: str | None,
    environment_name: str,
    encrypted_credentials: str | None,
    runtime_mode: WorkspaceRuntimeMode | None = None,
    workspace_provider: str | None = None,
    wizard_cloud_provider: str | None = None,
) -> tuple[RunningInstanceConfig, CloudCredentials | None, str]:
    from app.services.cloud_promote import resolve_attach_running_instance

    creds = load_workspace_credentials(encrypted_credentials)
    provider = resolve_attach_cloud_provider(
        environment_provider=cloud_provider,
        workspace_provider=workspace_provider,
        wizard_cloud_provider=wizard_cloud_provider,
        credentials=creds,
    )
    instance = resolve_attach_running_instance(
        running_instance,
        cloud_provider=provider,
        environment_name=environment_name,
        runtime_mode=runtime_mode,
    )
    return instance, creds, provider


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
    services: list[ContainerServiceSpec] | None = None,
    cloud_provider: str | None = None,
    credentials: CloudCredentials | None = None,
    org_slug: str | None = None,
    workspace_provider: str | None = None,
    wizard_cloud_provider: str | None = None,
    create_vpc: bool = False,
    create_subnets: bool = False,
    gcp_project_id: str | None = None,
) -> ProvisionedResources:
    """Deploy (or link) a preview onto serverless / VM / local-machine compute."""
    _ = (namespace, ttl_expires_at, owner_label, enable_postgres, enable_redis, packaging)
    cfg = settings or get_settings()

    kind = running_instance.kind
    strategy = running_instance.process_strategy
    provider = resolve_attach_cloud_provider(
        environment_provider=cloud_provider,
        workspace_provider=workspace_provider,
        wizard_cloud_provider=wizard_cloud_provider,
        credentials=credentials,
    )

    # Local Live Launch always uses Docker containers for one-click preview.
    # Cloud VMs honor the user's process_strategy (pm2/systemd/docker).
    if (
        kind == RunningInstanceKind.LOCAL_MACHINE
        and strategy != InstanceProcessStrategy.DOCKER
    ):
        logger.warning(
            "attach_process_strategy_coerced_to_docker",
            environment_id=environment_id,
            kind=kind.value,
            requested_strategy=strategy.value,
        )
        running_instance = running_instance.model_copy(
            update={"process_strategy": InstanceProcessStrategy.DOCKER}
        )
        strategy = InstanceProcessStrategy.DOCKER

    needs_image = (
        kind == RunningInstanceKind.SERVERLESS
        or strategy == InstanceProcessStrategy.DOCKER
        or kind == RunningInstanceKind.LOCAL_MACHINE
    )
    workload: str | None = None
    if needs_image:
        # Only build/push registry images for docker strategy or serverless.
        # Native pm2/systemd cloud VMs never touch Artifact Registry / ECR.
        push_provider = provider if (
            kind == RunningInstanceKind.SERVERLESS
            or (
                kind == RunningInstanceKind.VM
                and strategy == InstanceProcessStrategy.DOCKER
                and provider != CloudProvider.LOCAL.value
            )
        ) else None
        workload = resolve_instance_image(
            image=image,
            workspace_root=workspace_root,
            environment_id=environment_id,
            settings=cfg,
            cloud_provider=push_provider,
            credentials=credentials if push_provider else None,
            region=running_instance.region,
        )

    if kind == RunningInstanceKind.LOCAL_MACHINE:
        return _deploy_local_machine(
            environment_id=environment_id,
            name=name,
            image=workload or "",
            running_instance=running_instance,
            settings=cfg,
            workspace_root=workspace_root,
            services=services,
        )
    if kind == RunningInstanceKind.VM:
        return _deploy_vm(
            environment_id=environment_id,
            name=name,
            image=workload,
            running_instance=running_instance,
            settings=cfg,
            cloud_provider=provider,
            credentials=credentials,
            workspace_root=workspace_root,
            git_repo_url=git_repo_url,
            git_branch=git_branch,
            org_slug=org_slug,
            create_vpc=create_vpc,
            create_subnets=create_subnets,
            gcp_project_id=gcp_project_id,
        )
    if kind == RunningInstanceKind.SERVERLESS:
        return _deploy_serverless(
            environment_id=environment_id,
            name=name,
            image=workload or "",
            running_instance=running_instance,
            settings=cfg,
            cloud_provider=provider,
            credentials=credentials,
            workspace_root=workspace_root,
            org_slug=org_slug,
        )
    raise AttachDeployError(f"Unsupported running instance kind: {kind}")


def _teardown_serverless(
    *,
    running_instance: RunningInstanceConfig,
    environment_id: str,
    environment_name: str,
    cloud_provider: str | None,
    credentials: CloudCredentials | None,
    org_slug: str | None = None,
) -> None:
    from app.services.cloud_instance_compute import cloud_resource_name

    unique = cloud_resource_name(
        environment_id=environment_id,
        environment_name=environment_name,
        base_name=running_instance.service_name or environment_name,
        org_slug=org_slug,
        max_len=49,
    )
    legacy = cloud_resource_name(
        environment_id=environment_id,
        environment_name=environment_name,
        base_name=running_instance.service_name or environment_name,
        max_len=49,
    )
    candidates: list[str] = []
    for name in (unique, legacy, (running_instance.service_name or "").strip()):
        if name and name not in candidates:
            candidates.append(name)
    region = (running_instance.region or "us-central1").strip() or "us-central1"
    provider = (cloud_provider or CloudProvider.GCP.value).strip().lower()
    env = _credential_env(credentials, environment_id=environment_id)
    gcp_region = region.rsplit("-", 1)[0] if region.count("-") >= 2 else region

    if provider == CloudProvider.GCP.value and shutil.which("gcloud"):
        from app.services.cloud_instance_compute import _gcloud_project_args

        project_args = _gcloud_project_args(env)
        for service in candidates:
            deleted = _run(
                [
                    "gcloud",
                    "run",
                    "services",
                    "delete",
                    service,
                    f"--region={gcp_region}",
                    "--quiet",
                    *project_args,
                ],
                timeout=300,
                check=False,
                env=env,
            )
            if deleted.returncode == 0:
                return
        label = _SAFE_NAME.sub("-", environment_id.lower()).strip("-")[:63]
        listed = _run(
            [
                "gcloud",
                "run",
                "services",
                "list",
                f"--region={gcp_region}",
                "--filter",
                f"metadata.labels.launchpad-environment-id={label}",
                "--format=value(metadata.name)",
            ],
            timeout=120,
            check=False,
            env=env,
        )
        for candidate in (listed.stdout or "").splitlines():
            name = candidate.strip()
            if not name:
                continue
            _run(
                [
                    "gcloud",
                    "run",
                    "services",
                    "delete",
                    name,
                    f"--region={gcp_region}",
                    "--quiet",
                ],
                timeout=300,
                check=False,
                env=env,
            )
        return

    if provider == CloudProvider.AWS.value and shutil.which("aws"):
        env.setdefault("AWS_DEFAULT_REGION", region if region != "us-central1" else "us-east-1")
        for service in candidates:
            listed = _run(
                [
                    "aws",
                    "apprunner",
                    "list-services",
                    "--query",
                    f"ServiceSummaryList[?ServiceName=='{service}'].ServiceArn",
                    "--output",
                    "text",
                ],
                timeout=120,
                check=False,
                env=env,
            )
            arn = (listed.stdout or "").strip()
            if arn:
                _run(
                    ["aws", "apprunner", "delete-service", "--service-arn", arn],
                    timeout=300,
                    check=False,
                    env=env,
                )
                return
        return

    if provider == CloudProvider.AZURE.value and shutil.which("az"):
        rg = "launchpad-preview"
        sub = (credentials.azure_subscription_id if credentials else None) or env.get(
            "AZURE_SUBSCRIPTION_ID",
        )
        if sub and len(str(sub)) >= 8:
            rg = f"lp-{str(sub)[-8:].lower()}"
        loc = region if region != "us-central1" else "eastus"
        for service in candidates:
            _run(
                [
                    "az",
                    "containerapp",
                    "delete",
                    "--name",
                    service,
                    "--resource-group",
                    rg,
                    "--yes",
                ],
                timeout=300,
                check=False,
                env=env,
            )
        logger.info(
            "attach_teardown_azure_containerapp",
            services=candidates,
            resource_group=rg,
            location=loc,
        )
        return

    if provider == CloudProvider.CLOUDFLARE.value and shutil.which("wrangler"):
        for service in candidates:
            _run(
                ["wrangler", "delete", service],
                timeout=180,
                check=False,
                env=env,
            )
        return

    logger.info(
        "attach_teardown_serverless_noop",
        services=candidates,
        provider=provider,
        environment_id=environment_id,
    )


def teardown_attach(
    *,
    running_instance: RunningInstanceConfig | None,
    namespace: str,
    environment_id: str | None = None,
    environment_name: str | None = None,
    settings: Settings | None = None,
    cloud_provider: str | None = None,
    credentials: CloudCredentials | None = None,
    org_slug: str | None = None,
    workspace_provider: str | None = None,
    wizard_cloud_provider: str | None = None,
) -> None:
    """Tear down instance compute resources."""
    _ = (namespace, settings)
    if running_instance is None:
        return
    kind = running_instance.kind
    env_id = environment_id or "unknown"
    env_name = (environment_name or env_id).strip()
    container = _container_name(env_id)
    provider = resolve_attach_cloud_provider(
        environment_provider=cloud_provider,
        workspace_provider=workspace_provider,
        wizard_cloud_provider=wizard_cloud_provider,
        credentials=credentials,
    )

    if kind == RunningInstanceKind.LOCAL_MACHINE:
        _teardown_local_containers(env_id)
        return

    if kind == RunningInstanceKind.VM:
        if provider != CloudProvider.LOCAL.value:
            from app.services.cloud_instance_compute import teardown_cloud_vm

            teardown_cloud_vm(
                running_instance=running_instance,
                environment_id=env_id,
                environment_name=env_name,
                cloud_provider=provider,
                credentials=credentials,
                org_slug=org_slug,
            )
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
        _teardown_serverless(
            running_instance=running_instance,
            environment_id=env_id,
            environment_name=env_name,
            cloud_provider=provider,
            credentials=credentials,
            org_slug=org_slug,
        )
        return

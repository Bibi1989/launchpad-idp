"""Local Docker Compose preview executor (no remoted Docker socket)."""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import yaml

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

# Written beside the workspace compose file for preview deploys. Preferred host
# ports are kept when free; otherwise the next free port is chosen and the user
# is notified (Launchpad Adminer :8080, Postgres :5432, Redis :6379, etc.).
PREVIEW_COMPOSE_FILENAME = "docker-compose.launchpad-preview.yml"
_HOST_PORT_SEARCH_SPAN = 200

_PROJECT_SAFE = re.compile(r"[^a-z0-9_-]+")
_EXPOSE_RE = re.compile(r"^EXPOSE\s+(\d+)", re.MULTILINE)
_HEALTHCHECK_PATH_RE = re.compile(
    r"http://127\.0\.0\.1:\d+(/[A-Za-z0-9._~/-]*)",
)
_IPV4_PREFIX_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


class ComposeDeployError(RuntimeError):
    """Docker Compose preview deploy / teardown failed."""


def find_compose_file(workspace_root: Path) -> Path | None:
    """Return the first compose file under the workspace root, if any."""
    for name in COMPOSE_FILENAMES:
        candidate = workspace_root / name
        if candidate.is_file():
            return candidate
    return None


def _infer_app_kind_from_name(name: str) -> str:
    from app.services.service_kind import is_frontend_service_name

    return "frontend" if is_frontend_service_name(name) else "backend"


def discover_scaffolded_app_services(workspace_root: Path) -> list[dict[str, object]]:
    """Return compose service specs for each ``apps/<slug>/Dockerfile`` tree."""
    apps_dir = workspace_root / "apps"
    if not apps_dir.is_dir():
        return []

    services: list[dict[str, object]] = []
    for child in sorted(apps_dir.iterdir()):
        dockerfile = child / "Dockerfile"
        if not child.is_dir() or not dockerfile.is_file():
            continue
        try:
            text = dockerfile.read_text(encoding="utf-8")
        except OSError:
            continue
        port = 8080
        expose = _EXPOSE_RE.search(text)
        if expose:
            try:
                port = int(expose.group(1))
            except ValueError:
                port = 8080
        health_path = "/health"
        health = _HEALTHCHECK_PATH_RE.search(text)
        if health and health.group(1):
            health_path = health.group(1)
        app_kind = _infer_app_kind_from_name(child.name)
        services.append(
            {
                "name": child.name,
                "listen_port": port,
                "dockerfile_path": "Dockerfile",
                "context": f"apps/{child.name}",
                "health_path": health_path,
                "app_kind": app_kind,
                "expose_preview": app_kind == "frontend",
            }
        )
    return services


def compose_needs_core_scaffold_repair(compose_text: str, services: list[dict[str, object]]) -> bool:
    """True when compose still points at broken client dockers/* + context '.'."""
    if not services:
        return False
    normalized = compose_text.replace("\r\n", "\n")
    if "context: ." in normalized and (
        "dockerfile: dockers/" in normalized or "dockerfile: Dockerfile." in normalized
    ):
        return True
    for service in services:
        context = str(service.get("context") or "")
        if context and f"context: {context}" not in normalized:
            return True
    return False


def repair_compose_for_scaffolded_apps(
    workspace_root: Path,
    *,
    connection_env: dict[str, str] | None = None,
) -> Path | None:
    """Rewrite compose when CoreScaffold apps exist but compose uses repo-root context.

    Older provision flows wrote ``apps/<slug>/`` correctly, then the web client
    overwrote ``docker-compose.yml`` with ``context: .`` + ``dockers/Dockerfile.*``,
    which fails with ``COPY package.json: not found``.

    ``connection_env`` (inter-service HTTP/gRPC targets derived from the connection
    graph) is merged into every service so linked repos configured to talk to each
    other are actually wired when the compose stack is regenerated.
    """
    services = discover_scaffolded_app_services(workspace_root)
    if not services:
        return None

    if connection_env:
        for service in services:
            merged = dict(service.get("extra_env") or {})
            for key, value in connection_env.items():
                merged.setdefault(key, value)
            service["extra_env"] = merged

    compose_file = find_compose_file(workspace_root)
    existing = ""
    if compose_file is not None:
        try:
            existing = compose_file.read_text(encoding="utf-8")
        except OSError:
            existing = ""
    if compose_file is not None and not compose_needs_core_scaffold_repair(existing, services):
        return compose_file

    from app.services.dockerfile_scaffold import scaffold_docker_compose_services

    content = scaffold_docker_compose_services(services)
    target = compose_file or (workspace_root / "docker-compose.yml")
    target.write_text(content, encoding="utf-8")
    logger.info(
        "compose_repaired_for_core_apps",
        path=str(target),
        services=[str(s.get("name")) for s in services],
    )
    return target


def compose_project_name(*, namespace: str, environment_id: str) -> str:
    """Stable docker compose project name derived from the environment namespace."""
    raw = (namespace or f"lp-{environment_id[:8]}").strip().lower()
    cleaned = _PROJECT_SAFE.sub("-", raw).strip("-_")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = f"lp-{cleaned}" if cleaned else f"lp{environment_id.replace('-', '')[:12]}"
    return cleaned[:63]


_DOCKER_HOST_PORT_RE = re.compile(
    r"(?:0\.0\.0\.0|\[::\]|127\.0\.0\.1|\[::1\]):(\d+)->",
)
_BIND_ALLOCATED_RE = re.compile(
    r"Bind for [^\s]+?:(\d+)\s+failed:\s+port is already allocated",
    re.IGNORECASE,
)


def list_docker_published_host_ports() -> set[int]:
    """Return host ports already published by running Docker containers."""
    if shutil.which("docker") is None:
        return set()
    try:
        completed = subprocess.run(
            ["docker", "ps", "--format", "{{.Ports}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if completed.returncode != 0:
        return set()
    ports: set[int] = set()
    for line in (completed.stdout or "").splitlines():
        for match in _DOCKER_HOST_PORT_RE.finditer(line):
            try:
                ports.add(int(match.group(1)))
            except ValueError:
                continue
    return ports


def is_host_port_available(port: int, *, docker_ports: set[int] | None = None) -> bool:
    """Return True when the host port is free for Docker publish."""
    if port <= 0 or port > 65535:
        return False
    occupied = docker_ports if docker_ports is not None else list_docker_published_host_ports()
    if port in occupied:
        return False
    # Catch dev servers bound to loopback that docker publish would still collide with.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return False
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False


def next_available_host_port(
    preferred: int,
    *,
    reserved: set[int] | None = None,
    docker_ports: set[int] | None = None,
    span: int = _HOST_PORT_SEARCH_SPAN,
) -> int:
    """Return ``preferred`` when free, otherwise the next free port."""
    taken = reserved if reserved is not None else set()
    occupied = docker_ports if docker_ports is not None else list_docker_published_host_ports()
    start = max(1, int(preferred))
    for offset in range(0, max(1, span) + 1):
        candidate = start + offset
        if candidate > 65535:
            break
        if candidate in taken:
            continue
        if is_host_port_available(candidate, docker_ports=occupied):
            return candidate
    raise ComposeDeployError(
        f"No free host port near {preferred} (searched {span} ports). "
        "Free a local listener or change the compose ports mapping."
    )


def _parse_allocated_bind_ports(stderr: str, stdout: str) -> set[int]:
    blob = f"{stderr or ''}\n{stdout or ''}"
    ports: set[int] = set()
    for match in _BIND_ALLOCATED_RE.finditer(blob):
        try:
            ports.add(int(match.group(1)))
        except ValueError:
            continue
    return ports


def _parse_publish_port_entry(
    entry: object,
) -> tuple[int | None, int | None, str | None, str | None, dict[str, Any] | None]:
    """Return (preferred_host, container, host_ip, proto, long_form_dict)."""
    if isinstance(entry, int):
        return entry, entry, None, None, None
    if isinstance(entry, str):
        raw = entry.strip()
        if not raw or raw.startswith("$"):
            return None, None, None, None, None
        mapping, _, proto = raw.partition("/")
        proto_out = proto or None
        parts = mapping.split(":")
        try:
            if len(parts) == 1:
                container = int(parts[0])
                return container, container, None, proto_out, None
            if len(parts) == 2:
                left, right = parts
                if _IPV4_PREFIX_RE.match(left):
                    container = int(right)
                    return container, container, left, proto_out, None
                return int(left), int(right), None, proto_out, None
            if len(parts) == 3:
                host_ip, host_port, container = parts
                return int(host_port), int(container), host_ip, proto_out, None
        except ValueError:
            return None, None, None, None, None
        return None, None, None, None, None
    if isinstance(entry, dict):
        try:
            target = entry.get("target")
            published = entry.get("published", target)
            container = int(target) if target is not None else None
            preferred = int(published) if published is not None else container
        except (TypeError, ValueError):
            return None, None, None, None, dict(entry)
        raw_ip = entry.get("host_ip")
        ip = raw_ip.strip() if isinstance(raw_ip, str) and raw_ip.strip() else None
        proto = entry.get("protocol")
        proto_out = str(proto) if proto else None
        return preferred, container, ip, proto_out, dict(entry)
    return None, None, None, None, None


def _format_publish_port_entry(
    *,
    host_port: int,
    container_port: int,
    host_ip: str | None,
    proto: str | None,
    long_form: dict[str, Any] | None,
) -> object:
    if long_form is not None:
        out = dict(long_form)
        out["target"] = container_port
        out["published"] = host_port
        if host_ip:
            out["host_ip"] = host_ip
        if proto:
            out["protocol"] = proto
        return out
    if host_ip:
        mapping = f"{host_ip}:{host_port}:{container_port}"
    else:
        mapping = f"{host_port}:{container_port}"
    return f"{mapping}/{proto}" if proto else mapping


def remap_compose_publish_ports(
    data: dict[str, Any],
    *,
    extra_busy: set[int] | None = None,
    primary_host_preference: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Assign free host ports (preferred, else next) and drop fixed container names."""
    services = data.get("services")
    if not isinstance(services, dict):
        return data, []

    docker_ports = list_docker_published_host_ports()
    if extra_busy:
        docker_ports |= set(extra_busy)

    preview_names = _compose_preview_service_names_from_data(data)
    reserved: set[int] = set()
    notes: list[str] = []
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        service.pop("container_name", None)
        ports = service.get("ports")
        if not isinstance(ports, list):
            continue
        is_primary = service_name.lower() in preview_names or (
            not preview_names and service_name == next(iter(services.keys()), "")
        )
        rewritten: list[object] = []
        for entry in ports:
            preferred, container, host_ip, proto, long_form = _parse_publish_port_entry(entry)
            if preferred is None or container is None:
                rewritten.append(entry)
                continue
            if is_primary and primary_host_preference is not None:
                search_from = int(primary_host_preference)
            elif preferred == 0:
                search_from = container
            else:
                search_from = preferred
            chosen = next_available_host_port(
                search_from,
                reserved=reserved,
                docker_ports=docker_ports,
            )
            reserved.add(chosen)
            if chosen != search_from:
                notes.append(
                    f"service '{service_name}': host port {search_from} in use, "
                    f"using {chosen} instead"
                )
            elif is_primary and primary_host_preference is not None and chosen != preferred:
                notes.append(
                    f"service '{service_name}': host port {preferred} remapped to {chosen} "
                    f"(container {container})"
                )
            rewritten.append(
                _format_publish_port_entry(
                    host_port=chosen,
                    container_port=container,
                    host_ip=host_ip,
                    proto=proto,
                    long_form=long_form,
                )
            )
        service["ports"] = rewritten
    return data, notes


def _compose_preview_service_names_from_data(data: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    services = data.get("services")
    if not isinstance(services, dict):
        return names
    for raw in _compose_preview_service_names_from_services(services):
        names.add(raw.lower())
    return names


def _compose_preview_service_names_from_services(services: dict[str, Any]) -> list[str]:
    preview: list[str] = []
    frontends: list[str] = []
    others: list[str] = []
    for name, spec in services.items():
        svc_name = str(name)
        if svc_name.lower() in _DATASTORE_SERVICE_NAMES:
            continue
        if not isinstance(spec, dict):
            others.append(svc_name)
            continue
        meta = spec.get("x-launchpad") if isinstance(spec.get("x-launchpad"), dict) else {}
        labels = spec.get("labels") or []
        label_map: dict[str, str] = {}
        if isinstance(labels, dict):
            label_map = {str(k): str(v) for k, v in labels.items()}
        elif isinstance(labels, list):
            for item in labels:
                if isinstance(item, str) and "=" in item:
                    key, _, value = item.partition("=")
                    label_map[key.strip()] = value.strip()
        app_kind = str(
            meta.get("app_kind")
            or label_map.get("launchpad.io/app-kind")
            or _infer_app_kind_from_name(svc_name)
        ).lower()
        preview_target = str(
            meta.get("preview_target")
            or label_map.get("launchpad.io/preview-target")
            or ""
        ).lower() in {"true", "1", "yes"}
        if preview_target:
            preview.append(svc_name)
        elif app_kind == "frontend":
            frontends.append(svc_name)
        else:
            others.append(svc_name)
    ordered = preview + [n for n in frontends if n not in preview]
    ordered.extend(n for n in others if n not in ordered)
    return ordered


def prepare_preview_compose(
    compose_file: Path,
    *,
    extra_busy: set[int] | None = None,
    primary_host_preference: int | None = None,
) -> tuple[Path, list[str]]:
    """Write a preview compose file with conflict-free host port publishes.

    When a preferred host port (e.g. ``8080:8080``) is already allocated, the
    next free port is used and a user-facing note is returned.
    """
    try:
        raw = compose_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ComposeDeployError(f"Unable to read compose file: {exc}") from exc

    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ComposeDeployError(f"Invalid compose YAML: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ComposeDeployError("Compose file must be a YAML mapping with services")

    # Deep-ish copy via dump/load so retries do not mutate a shared dict.
    prepared_src = yaml.safe_load(yaml.safe_dump(loaded))
    if not isinstance(prepared_src, dict):
        raise ComposeDeployError("Compose file must be a YAML mapping with services")

    prepared, notes = remap_compose_publish_ports(
        prepared_src,
        extra_busy=extra_busy,
        primary_host_preference=primary_host_preference,
    )
    dest = compose_file.parent / PREVIEW_COMPOSE_FILENAME
    try:
        dest.write_text(
            yaml.safe_dump(
                prepared,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ComposeDeployError(f"Unable to write preview compose file: {exc}") from exc
    return dest, notes


def _compose_up_error_detail(stderr: str, stdout: str) -> str:
    blob = f"{stderr or ''}\n{stdout or ''}".strip() or "compose up failed"
    detail = sanitize_log_message(blob[:800])
    lower = detail.lower()
    if "port is already allocated" in lower or "address already in use" in lower:
        detail = (
            f"{detail} Hint: a host port raced after allocation. Retry provision; "
            "Launchpad remaps busy ports to the next free one."
        )
    return detail


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


_DATASTORE_SERVICE_NAMES = frozenset(
    {"postgres", "postgresql", "mysql", "mariadb", "mongodb", "mongo", "redis"}
)


def _compose_preview_service_names(compose_file: Path) -> list[str]:
    """Ordered preferred service names for Open-app (preview_target, then frontend)."""
    try:
        raw = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []
    services = raw.get("services")
    if not isinstance(services, dict):
        return []
    return _compose_preview_service_names_from_services(services)


def _row_service_name(row: dict[str, object]) -> str:
    for key in ("Service", "service", "Name", "name"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            # Compose project prefixes like "proj-web-ui-1" - match by suffix token.
            return value.strip()
    return ""


def _published_ports_from_row(row: dict[str, object]) -> list[int]:
    ports: list[int] = []
    publishers = row.get("Publishers") or row.get("publishers")
    if not isinstance(publishers, list):
        return ports
    for pub in publishers:
        if not isinstance(pub, dict):
            continue
        published = pub.get("PublishedPort") or pub.get("published_port")
        try:
            port = int(published)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if port > 0:
            ports.append(port)
    return ports


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

    preferred = _compose_preview_service_names(compose_file)
    # Score rows: match preferred service names (suffix/contains), skip datastores.
    def row_rank(row: dict[str, object]) -> tuple[int, int]:
        name = _row_service_name(row).lower()
        base = name.rsplit("-", 1)[0] if name else ""
        for idx, pref in enumerate(preferred):
            pref_l = pref.lower()
            if pref_l == name or name.endswith(f"-{pref_l}") or pref_l in name or pref_l in base:
                return (0, idx)
        if any(ds in name for ds in _DATASTORE_SERVICE_NAMES):
            return (2, 999)
        return (1, 999)

    ranked = sorted(rows, key=row_rank)
    for row in ranked:
        name = _row_service_name(row).lower()
        if any(ds in name for ds in _DATASTORE_SERVICE_NAMES):
            continue
        ports = _published_ports_from_row(row)
        if ports:
            return ports[0]
    return None


def _default_listen_port_from_compose(compose_file: Path) -> int:
    try:
        text = compose_file.read_text(encoding="utf-8")
    except OSError:
        return 8080
    preferred = _compose_preview_service_names(compose_file)
    if preferred:
        # Look for the preferred service's host:container mapping first.
        for name in preferred:
            pattern = rf"{re.escape(name)}:[\s\S]*?ports:\s*\n(?:\s*-\s*[\"']?(?:\d+\.\d+\.\d+\.\d+:)?(\d{{1,5}}):\d{{2,5}}[\"']?\s*\n)+"
            match = re.search(pattern, text)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    pass
    # Prefer host:container mappings like "8080:8080" or "0:3000".
    match = re.search(r'["\']?(?:\d{1,3}(?:\.\d{1,3}){3}:)?(\d{1,5}):(\d{2,5})["\']?', text)
    if match:
        try:
            host_port = int(match.group(1))
            container_port = int(match.group(2))
        except ValueError:
            return 8080
        return container_port if host_port == 0 else host_port
    return 8080


def deploy_compose(
    *,
    workspace_root: Path,
    namespace: str,
    environment_id: str,
    name: str,
    image: str | None = None,
    settings: Settings | None = None,
    primary_host_preference: int | None = None,
    connection_env: dict[str, str] | None = None,
) -> ProvisionedResources:
    """Bring up the workspace compose stack and return preview resources."""
    cfg = settings or get_settings()
    root = workspace_root.expanduser().resolve()
    compose_file = repair_compose_for_scaffolded_apps(
        root, connection_env=connection_env
    ) or find_compose_file(root)
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

    up_timeout = max(300.0, float(cfg.preview_build_timeout_seconds or 900))
    extra_busy: set[int] = set()
    all_notes: list[str] = []
    preview_compose: Path | None = None
    up: subprocess.CompletedProcess[str] | None = None

    for attempt in range(1, 4):
        preview_compose, port_notes = prepare_preview_compose(
            compose_file,
            extra_busy=extra_busy or None,
            primary_host_preference=primary_host_preference,
        )
        for note in port_notes:
            if note not in all_notes:
                all_notes.append(note)
        if all_notes:
            resources.notice = "Port remap: " + "; ".join(all_notes)
            logger.warning(
                "compose_host_ports_remapped",
                environment_id=environment_id,
                project=project,
                notes=all_notes,
                attempt=attempt,
            )

        # Drop a prior partial project so leftover binds do not block retries.
        _run_compose(
            ["-f", str(preview_compose), "-p", project, "down", "--remove-orphans"],
            cwd=root,
            timeout=60,
        )
        # Also tear down by project name in case a previous attempt used the
        # workspace compose file (fixed container_name / 8080 publish).
        _run_compose(
            ["-p", project, "down", "--remove-orphans"],
            cwd=root,
            timeout=60,
        )

        up = _run_compose(
            [
                "-f",
                str(preview_compose),
                "-p",
                project,
                "up",
                "-d",
                "--build",
                "--remove-orphans",
            ],
            cwd=root,
            timeout=up_timeout,
        )
        if up.returncode == 0:
            break

        conflicted = _parse_allocated_bind_ports(up.stderr or "", up.stdout or "")
        if not conflicted or attempt >= 3:
            detail = _compose_up_error_detail(up.stderr or "", up.stdout or "")
            raise ComposeDeployError(f"docker compose up failed: {detail}")

        extra_busy |= conflicted
        logger.warning(
            "compose_up_port_conflict_retry",
            environment_id=environment_id,
            project=project,
            conflicted=sorted(conflicted),
            attempt=attempt,
        )

    assert preview_compose is not None
    assert up is not None and up.returncode == 0

    port = _first_published_port(project=project, compose_file=preview_compose, cwd=root)
    if port is None:
        port = _default_listen_port_from_compose(preview_compose)

    resources.created_workload = True
    resources.node_port = port
    resources.preview_url = _preview_url_for_port(cfg, port)
    logger.info(
        "compose_deployed",
        environment_id=environment_id,
        project=project,
        preview_url=resources.preview_url,
        node_port=port,
        compose_file=str(preview_compose),
        notice=resources.notice,
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
    compose_file = _resolve_compose_file_for_project(cwd) if workspace_root else None
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


def _resolve_compose_file_for_project(cwd: Path) -> Path | None:
    preview = cwd / PREVIEW_COMPOSE_FILENAME
    if preview.is_file():
        return preview
    return find_compose_file(cwd)


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
    compose_file = _resolve_compose_file_for_project(cwd) if workspace_root else None
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
    compose_file = _resolve_compose_file_for_project(cwd) if workspace_root else None
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

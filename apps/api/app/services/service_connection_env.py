"""Translate a service communication graph into connection environment variables.

Given the detected inter-service comms (``ServiceComms``) plus any operator-defined
connections (``ExplicitConnection``), derive the environment variables a service needs
to reach its peers at runtime:

- gRPC edges -> ``<TARGET>_GRPC_TARGET`` / ``<TARGET>_GRPC_ADDR`` (``service:port``)
- HTTP edges -> ``<TARGET>_URL`` (``http://service:port``)

Message-broker connectivity (Kafka / RabbitMQ) is intentionally NOT emitted here: it is
provisioned via ``WorkloadDependenciesConfig`` and injected through the shared workload
secret (see ``workload_dependencies.dependency_secret_string_data``), so every service
receives ``KAFKA_BROKERS`` / ``RABBITMQ_URL`` the same way it receives ``DATABASE_URL``.

The returned map is flat (``ENV_KEY -> value``) and safe to merge into a workspace's
shared ``.env``: gRPC/HTTP keys are namespaced by target service name, so only the
calling service actually reads its own targets.
"""

from __future__ import annotations

import re

from app.services.comm_detector import CommKind, ServiceComms
from app.services.service_graph import (
    ExplicitConnection,
    NodeType,
    build_service_graph,
)

_DEFAULT_GRPC_PORT = 50051
_DEFAULT_HTTP_PORT = 8080

# When a FRONTEND service is connected (HTTP) to a backend, expose the backend URL under
# the env var name that framework conventionally reads for its public API base. Keys are
# tried in order and set only if unset, so a user override always wins. Frontend build
# tools only expose vars with these prefixes to client code (NEXT_PUBLIC_/VITE_/…).
_FRONTEND_API_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "nextjs": ("NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_API_BASE_URL"),
    "react_vite": ("VITE_API_URL", "VITE_API_BASE_URL"),
    "vuejs": ("VITE_API_URL", "VUE_APP_API_URL"),
    "svelte": ("PUBLIC_API_URL", "VITE_API_URL"),
    "angular": ("NG_APP_API_URL", "API_URL"),
    "nuxtjs": ("NUXT_PUBLIC_API_BASE", "NUXT_PUBLIC_API_URL"),
}


def frontend_api_env_keys(framework: str | None) -> tuple[str, ...]:
    """Conventional public API-base env keys for a frontend framework (empty if unknown)."""
    return _FRONTEND_API_ENV_KEYS.get((framework or "").strip().lower(), ())


def env_prefix(service: str) -> str:
    """``orders-api`` -> ``ORDERS_API`` (a stable env-var namespace)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", service).strip("_").upper()


def build_connection_env(
    comms: list[ServiceComms],
    connections: list[ExplicitConnection] | None = None,
    *,
    service_ports: dict[str, int] | None = None,
    default_grpc_port: int = _DEFAULT_GRPC_PORT,
    default_http_port: int = _DEFAULT_HTTP_PORT,
    frameworks: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return ``ENV_KEY -> value`` for gRPC/HTTP inter-service targets in the graph.

    When ``frameworks`` maps a source service to a frontend framework and it has an HTTP
    edge to a backend, the backend URL is ALSO exposed under that framework's conventional
    public API-base key (e.g. ``NEXT_PUBLIC_API_URL`` / ``VITE_API_URL``), so the frontend
    reads it without manual wiring.
    """
    if not comms:
        return {}
    graph = build_service_graph(comms, explicit_connections=connections or [])
    nodes = {node.id: node for node in graph.nodes}
    ports = dict(service_ports or {})
    frameworks = frameworks or {}
    env: dict[str, str] = {}

    for edge in graph.edges:
        target = nodes.get(edge.target)
        if target is None or target.type != NodeType.SERVICE:
            continue
        prefix = env_prefix(target.label)
        if not prefix:
            continue
        if edge.protocol == CommKind.GRPC:
            port = ports.get(target.label) or default_grpc_port
            addr = f"{target.label}:{port}"
            env[f"{prefix}_GRPC_TARGET"] = addr
            env.setdefault(f"{prefix}_GRPC_ADDR", addr)
        elif edge.protocol == CommKind.HTTP:
            port = ports.get(target.label) or default_http_port
            url = f"http://{target.label}:{port}"
            env.setdefault(f"{prefix}_URL", url)
            # If the SOURCE is a frontend, publish the backend URL under the framework's
            # conventional public key so the frontend can call the backend out of the box.
            source = nodes.get(edge.source)
            if source is not None:
                for key in frontend_api_env_keys(frameworks.get(source.label)):
                    env.setdefault(key, url)

    return env


def _linked_repo_service_name(entry: dict) -> str:
    """Stable service name for a linked-repo snapshot entry."""
    base = str(entry.get("full_name") or "").strip()
    leaf = base.split("/")[-1] if base else ""
    if not leaf:
        url = str(entry.get("git_repo_url") or "").strip().rstrip("/")
        leaf = url.split("/")[-1]
        if leaf.endswith(".git"):
            leaf = leaf[:-4]
    return re.sub(r"[^a-z0-9-]+", "-", leaf.lower()).strip("-")[:63]


def _infer_framework_from_service_name(name: str) -> str:
    """Best-effort frontend framework hint from a service/repo name."""
    lowered = (name or "").strip().lower()
    if not lowered:
        return ""
    if "nuxt" in lowered:
        return "nuxtjs"
    if "next" in lowered:
        return "nextjs"
    if "vue" in lowered:
        return "vuejs"
    if "angular" in lowered:
        return "angular"
    if "svelte" in lowered:
        return "svelte"
    if any(token in lowered for token in ("frontend", "web-ui", "web", "client", "ui")):
        return "react_vite"
    return ""


def _frameworks_from_snapshot(snapshot: dict) -> dict[str, str]:
    frameworks: dict[str, str] = {}
    detection = snapshot.get("detection") if isinstance(snapshot.get("detection"), dict) else {}
    for svc in (detection or {}).get("services", []):
        if isinstance(svc, dict) and svc.get("name"):
            frameworks[str(svc["name"])] = str(svc.get("framework") or "")
    for entry in snapshot.get("linked_repos") or []:
        if not isinstance(entry, dict):
            continue
        name = _linked_repo_service_name(entry)
        if name and name not in frameworks:
            frameworks[name] = _infer_framework_from_service_name(name)
    for entry in snapshot.get("repos") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("service") or "").strip()
        if name and name not in frameworks:
            frameworks[name] = _infer_framework_from_service_name(name)
    return frameworks


def _service_ports_from_snapshot(snapshot: dict) -> dict[str, int]:
    ports: dict[str, int] = {}
    detection = snapshot.get("detection") if isinstance(snapshot.get("detection"), dict) else {}
    for svc in (detection or {}).get("services", []):
        if not isinstance(svc, dict) or not svc.get("name"):
            continue
        raw_port = svc.get("port")
        if isinstance(raw_port, int) and raw_port > 0:
            ports[str(svc["name"])] = raw_port
        elif isinstance(raw_port, str) and raw_port.strip().isdigit():
            ports[str(svc["name"])] = int(raw_port.strip())
    return ports


def connection_env_from_snapshot(snapshot: dict | None) -> dict[str, str]:
    """Derive inter-service connection env from a persisted wizard snapshot.

    Reads ``service_comms`` (detected) and ``service_connections`` (operator-defined)
    and returns the flat ``ENV_KEY -> value`` map from :func:`build_connection_env`.
    Tolerant of malformed persisted entries. Returns ``{}`` when there is nothing to wire.
    """
    if not isinstance(snapshot, dict):
        return {}

    comms: list[ServiceComms] = []
    for entry in snapshot.get("service_comms") or []:
        if not isinstance(entry, dict):
            continue
        try:
            comms.append(ServiceComms.model_validate(entry))
        except Exception:  # noqa: BLE001, S112 - tolerate malformed persisted data
            continue

    connections: list[ExplicitConnection] = []
    for entry in snapshot.get("service_connections") or []:
        if not isinstance(entry, dict):
            continue
        try:
            connections.append(ExplicitConnection.model_validate(entry))
        except Exception:  # noqa: BLE001, S112 - tolerate malformed persisted data
            continue

    existing_services = {c.service for c in comms}
    for entry in snapshot.get("linked_repos") or []:
        if not isinstance(entry, dict):
            continue
        name = _linked_repo_service_name(entry)
        if name and name not in existing_services:
            comms.append(ServiceComms(service=name, capabilities=[]))
            existing_services.add(name)
    for entry in snapshot.get("repos") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("service") or "").strip()
        if name and name not in existing_services:
            comms.append(ServiceComms(service=name, capabilities=[]))
            existing_services.add(name)
    for conn in connections:
        for name in (conn.source, conn.target):
            cleaned = str(name or "").strip()
            if cleaned and cleaned not in existing_services:
                comms.append(ServiceComms(service=cleaned, capabilities=[]))
                existing_services.add(cleaned)

    if not comms or not connections:
        return {}

    frameworks = _frameworks_from_snapshot(snapshot)
    service_ports = _service_ports_from_snapshot(snapshot)
    # Only plain service connectors inject inter-service URLs; CORS connectors are
    # applied to the target's CORS policy at deploy (see cors_origins_from_snapshot).
    service_conns = [c for c in connections if getattr(c, "kind", "service") != "cors"]
    env = build_connection_env(
        comms,
        service_conns,
        frameworks=frameworks,
        service_ports=service_ports or None,
    )
    # Apply explicit env-key overrides: expose the target URL under a custom key.
    for conn in service_conns:
        expose_as = (getattr(conn, "expose_as", None) or "").strip()
        if not expose_as:
            continue
        target = str(conn.target or "").strip()
        if not target:
            continue
        port = service_ports.get(target) or _DEFAULT_HTTP_PORT
        env[expose_as] = f"http://{target}:{port}"

    # Explicit CORS connectors add allowed origins to the shared CORS var. The
    # deploy layer later appends the live preview frontend origin to the same var
    # (manifest_deploy.merge_preview_cors_origin), so the two compose cleanly.
    cors_by_target = cors_origins_from_snapshot(snapshot)
    all_origins: list[str] = []
    for origins in cors_by_target.values():
        for origin in origins:
            if origin not in all_origins:
                all_origins.append(origin)
    if all_origins:
        existing = [p.strip() for p in (env.get("CORS_ALLOWED_ORIGINS") or "").split(",") if p.strip()]
        for origin in all_origins:
            if origin not in existing:
                existing.append(origin)
        env["CORS_ALLOWED_ORIGINS"] = ",".join(existing)
    return env


def frontend_api_path_from_snapshot(snapshot: dict | None) -> str:
    """Operator-configured API path for the frontend->backend connector.

    Returns "" (the base URL, the default) unless a service connector sets a path.
    Normalized to a leading-slash, no-trailing-slash path (e.g. ``/api``).
    """
    if not isinstance(snapshot, dict):
        return ""
    for entry in snapshot.get("service_connections") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("kind") or "service").strip().lower() == "cors":
            continue
        raw = str(entry.get("api_path") or "").strip().strip("/")
        if raw:
            return f"/{raw}"
    return ""


def cors_origins_from_snapshot(snapshot: dict | None) -> dict[str, list[str]]:
    """Explicit CORS origins per target service, from ``cors`` connectors.

    Returns ``{target_service: [origin, ...]}``. Connectors without an explicit
    ``cors_origin`` are skipped here; the live preview frontend origin is still
    wired automatically by the deploy layer (merge_preview_cors_origin).
    """
    if not isinstance(snapshot, dict):
        return {}
    out: dict[str, list[str]] = {}
    for entry in snapshot.get("service_connections") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("kind") or "service").strip().lower() != "cors":
            continue
        target = str(entry.get("target") or "").strip()
        origin = str(entry.get("cors_origin") or "").strip()
        if not target or not origin:
            continue
        out.setdefault(target, [])
        if origin not in out[target]:
            out[target].append(origin)
    return out


def custom_env_from_snapshot(snapshot: dict | None) -> dict[str, str]:
    """Read the user-defined ``env_vars`` (key/value) from a wizard snapshot into a flat map."""
    if not isinstance(snapshot, dict):
        return {}
    out: dict[str, str] = {}
    for entry in snapshot.get("env_vars") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        if key:
            out[key] = str(entry.get("value") if entry.get("value") is not None else "")
    return out

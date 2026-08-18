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
    if not comms:
        return {}

    connections: list[ExplicitConnection] = []
    for entry in snapshot.get("service_connections") or []:
        if not isinstance(entry, dict):
            continue
        try:
            connections.append(ExplicitConnection.model_validate(entry))
        except Exception:  # noqa: BLE001, S112 - tolerate malformed persisted data
            continue

    # Service -> framework (from detection), used to expose the backend URL to a frontend
    # under its framework's conventional key (NEXT_PUBLIC_/VITE_/NUXT_PUBLIC_/...).
    frameworks: dict[str, str] = {}
    detection = snapshot.get("detection") if isinstance(snapshot.get("detection"), dict) else {}
    for svc in (detection or {}).get("services", []):
        if isinstance(svc, dict) and svc.get("name"):
            frameworks[str(svc["name"])] = str(svc.get("framework") or "")

    return build_connection_env(comms, connections, frameworks=frameworks)


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

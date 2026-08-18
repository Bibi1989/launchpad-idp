"""Message-broker dependency wiring + graph-derived connection env (#5)."""

from __future__ import annotations

import pytest

from app.schemas.cloud import (
    DependencyPlacement,
    LocalCloudConfig,
    MessageBrokerDependency,
    WorkloadDependenciesConfig,
)
from app.services.comm_detector import (
    CommKind,
    CommRole,
    ServiceCapability,
    ServiceComms,
)
from app.services.repo_import import RepoImportService
from app.services.service_connection_env import build_connection_env, env_prefix
from app.services.service_graph import ExplicitConnection
from app.services.workload_dependencies import (
    DataStoreKind,
    _in_cluster_kinds,
    dependency_secret_string_data,
    in_cluster_manifest_files,
    validate_managed_dependencies,
)


def _kafka_peer(name: str) -> ServiceComms:
    return ServiceComms(
        service=name,
        capabilities=[ServiceCapability(kind=CommKind.KAFKA, role=CommRole.PEER, evidence="dep")],
    )


# --- broker secret env -------------------------------------------------------


def test_kafka_in_cluster_emits_broker_env() -> None:
    deps = WorkloadDependenciesConfig(kafka=MessageBrokerDependency(enabled=True))
    data = dependency_secret_string_data(deps, name="shop")
    assert data["KAFKA_BROKERS"] == "kafka:9092"
    assert data["KAFKA_BOOTSTRAP_SERVERS"] == "kafka:9092"


def test_rabbitmq_external_uses_byo_url() -> None:
    deps = WorkloadDependenciesConfig(
        rabbitmq=MessageBrokerDependency(
            enabled=True,
            placement=DependencyPlacement.EXTERNAL,
            connection_url="amqps://u:p@cloudamqp:5671/vh",
        )
    )
    data = dependency_secret_string_data(deps, name="shop")
    assert data["RABBITMQ_URL"] == "amqps://u:p@cloudamqp:5671/vh"
    assert data["AMQP_URL"] == "amqps://u:p@cloudamqp:5671/vh"


def test_external_broker_without_url_emits_nothing() -> None:
    deps = WorkloadDependenciesConfig(
        kafka=MessageBrokerDependency(enabled=True, placement=DependencyPlacement.EXTERNAL)
    )
    data = dependency_secret_string_data(deps, name="shop")
    assert "KAFKA_BROKERS" not in data


def test_broker_manifests_and_kinds() -> None:
    deps = WorkloadDependenciesConfig(
        kafka=MessageBrokerDependency(enabled=True),
        rabbitmq=MessageBrokerDependency(enabled=True),
    )
    kinds = _in_cluster_kinds(deps)
    assert DataStoreKind.KAFKA in kinds and DataStoreKind.RABBITMQ in kinds
    files = in_cluster_manifest_files(ns="ns", name="shop", kinds=kinds)
    assert "kafka-deployment.yaml" in files
    assert "rabbitmq-deployment.yaml" in files
    assert "kafka:9092" not in files["kafka-deployment.yaml"] or "PLAINTEXT://kafka:9092" in files["kafka-deployment.yaml"]


def test_managed_broker_rejected() -> None:
    deps = WorkloadDependenciesConfig(
        kafka=MessageBrokerDependency(enabled=True, placement=DependencyPlacement.MANAGED)
    )
    with pytest.raises(ValueError, match="Managed Kafka"):
        validate_managed_dependencies(LocalCloudConfig(), deps)


def test_datastore_managed_validation_unaffected() -> None:
    # An in-cluster broker enabled alongside no managed datastore must validate cleanly.
    deps = WorkloadDependenciesConfig(kafka=MessageBrokerDependency(enabled=True))
    validate_managed_dependencies(LocalCloudConfig(), deps)


# --- graph-derived connection env -------------------------------------------


def test_grpc_target_env() -> None:
    comms = [
        ServiceComms(
            service="orders",
            capabilities=[ServiceCapability(kind=CommKind.GRPC, role=CommRole.CLIENT, evidence="dep")],
        ),
        ServiceComms(
            service="inventory",
            capabilities=[ServiceCapability(kind=CommKind.GRPC, role=CommRole.SERVER, evidence="proto")],
        ),
    ]
    env = build_connection_env(comms, [], service_ports={"inventory": 50055})
    assert env["INVENTORY_GRPC_TARGET"] == "inventory:50055"
    assert env["INVENTORY_GRPC_ADDR"] == "inventory:50055"


def test_http_explicit_connection_env() -> None:
    comms = [_kafka_peer("web"), _kafka_peer("api")]
    conns = [ExplicitConnection(source="web", target="api", protocol=CommKind.HTTP)]
    env = build_connection_env(comms, conns, service_ports={"api": 3000})
    assert env["API_URL"] == "http://api:3000"


def test_frontend_gets_backend_url_under_framework_key() -> None:
    comms = [_kafka_peer("web"), _kafka_peer("api")]
    conns = [ExplicitConnection(source="web", target="api", protocol=CommKind.HTTP)]

    # Next.js frontend -> NEXT_PUBLIC_* keys point at the backend.
    nextjs = build_connection_env(comms, conns, frameworks={"web": "nextjs", "api": "fastapi"})
    assert nextjs["API_URL"] == "http://api:8080"
    assert nextjs["NEXT_PUBLIC_API_URL"] == "http://api:8080"

    # Vite (React Router) frontend -> VITE_* keys.
    vite = build_connection_env(comms, conns, frameworks={"web": "react_vite"})
    assert vite["VITE_API_URL"] == "http://api:8080"

    # Nuxt -> NUXT_PUBLIC_* keys.
    nuxt = build_connection_env(comms, conns, frameworks={"web": "nuxtjs"})
    assert nuxt["NUXT_PUBLIC_API_BASE"] == "http://api:8080"

    # No framework -> only the generic key (no framework-specific keys).
    generic = build_connection_env(comms, conns)
    assert generic["API_URL"] == "http://api:8080"
    assert "NEXT_PUBLIC_API_URL" not in generic and "VITE_API_URL" not in generic


def test_empty_comms_no_env() -> None:
    assert build_connection_env([], []) == {}


def test_env_prefix_sanitizes() -> None:
    assert env_prefix("orders-api") == "ORDERS_API"
    assert env_prefix("billing.svc") == "BILLING_SVC"


# --- repo_import connection wiring helper ------------------------------------


def test_connection_wiring_detects_brokers_and_env() -> None:
    multi_repo = {
        "service_comms": [
            _kafka_peer("orders").model_dump(),
            {
                "service": "inventory",
                "capabilities": [
                    {"kind": "grpc", "role": "server", "evidence": "proto"},
                ],
            },
            {
                "service": "orders",
                "capabilities": [
                    {"kind": "grpc", "role": "client", "evidence": "dep"},
                ],
            },
        ],
        "service_connections": [],
    }
    # DetectedService-like objects only need name + port.
    from types import SimpleNamespace

    services = [SimpleNamespace(name="inventory", port=50051), SimpleNamespace(name="orders", port=8080)]
    broker_kinds, env = RepoImportService._connection_wiring(multi_repo, services)  # type: ignore[arg-type]
    assert "kafka" in broker_kinds
    assert env.get("INVENTORY_GRPC_TARGET") == "inventory:50051"


def test_connection_wiring_empty_for_single_repo() -> None:
    assert RepoImportService._connection_wiring({}, []) == (set(), {})

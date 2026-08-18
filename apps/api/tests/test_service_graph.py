"""Inter-service communication detection + connection graph for multi-repo workspaces."""

from __future__ import annotations

from pathlib import Path

from app.services.comm_detector import (
    CommKind,
    CommRole,
    ServiceCapability,
    ServiceComms,
    detect_service_comms,
)
from app.services.service_graph import (
    ExplicitConnection,
    NodeType,
    build_service_graph,
    graph_to_mermaid,
)

# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def test_detects_kafka_from_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"kafkajs": "^2.0.0", "express": "^4"}}', encoding="utf-8"
    )
    comms = detect_service_comms(tmp_path, service_name="orders")
    assert CommKind.KAFKA in comms.kinds()


def test_detects_rabbitmq_and_redis_from_python(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("aio-pika==9.0\nredis==5.0\n", encoding="utf-8")
    comms = detect_service_comms(tmp_path, service_name="worker")
    kinds = comms.kinds()
    assert CommKind.RABBITMQ in kinds
    assert CommKind.REDIS in kinds


def test_detects_grpc_server_from_proto(tmp_path: Path) -> None:
    (tmp_path / "api.proto").write_text('syntax = "proto3";', encoding="utf-8")
    (tmp_path / "go.mod").write_text("require google.golang.org/grpc v1.60.0\n", encoding="utf-8")
    comms = detect_service_comms(tmp_path, service_name="catalog")
    roles = {(c.kind, c.role) for c in comms.capabilities}
    assert (CommKind.GRPC, CommRole.SERVER) in roles
    assert (CommKind.GRPC, CommRole.CLIENT) in roles


def test_detects_from_env_hints(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("KAFKA_BROKERS=localhost:9092\n", encoding="utf-8")
    comms = detect_service_comms(tmp_path, service_name="ingest")
    assert CommKind.KAFKA in comms.kinds()


def test_no_signals_is_empty(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    comms = detect_service_comms(tmp_path, service_name="plain")
    assert comms.capabilities == []


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #


def _peer(kind: CommKind) -> ServiceCapability:
    return ServiceCapability(kind=kind, role=CommRole.PEER, evidence="test")


def test_kafka_participants_share_a_broker_node() -> None:
    services = [
        ServiceComms(service="orders", capabilities=[_peer(CommKind.KAFKA)]),
        ServiceComms(service="billing", capabilities=[_peer(CommKind.KAFKA)]),
    ]
    graph = build_service_graph(services)
    broker = next(n for n in graph.nodes if n.type == NodeType.BROKER)
    assert broker.label == "kafka"
    targets = {e.target for e in graph.edges if e.protocol == CommKind.KAFKA}
    assert targets == {broker.id}
    assert {e.source for e in graph.edges} == {"orders", "billing"}
    assert all(e.configured is False for e in graph.edges)  # auto-inferred


def test_single_grpc_server_wires_clients() -> None:
    services = [
        ServiceComms(service="catalog", capabilities=[
            ServiceCapability(kind=CommKind.GRPC, role=CommRole.SERVER, evidence="proto"),
        ]),
        ServiceComms(service="web", capabilities=[
            ServiceCapability(kind=CommKind.GRPC, role=CommRole.CLIENT, evidence="dep"),
        ]),
    ]
    graph = build_service_graph(services)
    grpc_edges = [e for e in graph.edges if e.protocol == CommKind.GRPC]
    assert len(grpc_edges) == 1
    assert grpc_edges[0].source == "web"
    assert grpc_edges[0].target == "catalog"


def test_explicit_connection_is_added_and_marked_configured() -> None:
    services = [
        ServiceComms(service="web", capabilities=[]),
        ServiceComms(service="api", capabilities=[]),
    ]
    graph = build_service_graph(
        services,
        explicit_connections=[ExplicitConnection(source="web", target="api", protocol=CommKind.HTTP)],
    )
    http_edges = [e for e in graph.edges if e.protocol == CommKind.HTTP]
    assert len(http_edges) == 1
    assert http_edges[0].configured is True


def test_mermaid_render_contains_nodes_and_edges() -> None:
    services = [
        ServiceComms(service="orders", capabilities=[_peer(CommKind.KAFKA)]),
        ServiceComms(service="billing", capabilities=[_peer(CommKind.KAFKA)]),
    ]
    mermaid = graph_to_mermaid(build_service_graph(services, frameworks={"orders": "fastapi"}))
    assert mermaid.startswith("flowchart LR")
    assert "orders" in mermaid and "billing" in mermaid
    assert "kafka" in mermaid
    assert "|kafka|" in mermaid
    assert "fastapi" in mermaid


# --------------------------------------------------------------------------- #
# Datastore / broker infra nodes (from enabled dependencies)
# --------------------------------------------------------------------------- #


def test_infra_kinds_add_datastore_nodes() -> None:
    services = [ServiceComms(service="orders", capabilities=[])]
    graph = build_service_graph(services, infra_kinds=["postgres", "redis"])
    by_id = {n.id: n for n in graph.nodes}
    assert by_id["bus_postgres"].type == NodeType.DATASTORE
    assert by_id["bus_postgres"].label == "postgres"
    assert by_id["bus_redis"].type == NodeType.DATASTORE
    # No auto edges - operator wires them.
    assert graph.edges == []


def test_infra_broker_node_type() -> None:
    graph = build_service_graph([], infra_kinds=["kafka"])
    assert next(n for n in graph.nodes if n.id == "bus_kafka").type == NodeType.BROKER


def test_service_can_connect_to_datastore_node() -> None:
    services = [ServiceComms(service="orders", capabilities=[])]
    graph = build_service_graph(
        services,
        infra_kinds=["postgres"],
        explicit_connections=[
            ExplicitConnection(source="orders", target="bus_postgres", protocol=CommKind.POSTGRES)
        ],
    )
    # The connection targets the datastore node, not a new service node.
    assert not any(n.id == "bus_postgres" and n.type == NodeType.SERVICE for n in graph.nodes)
    edge = next(e for e in graph.edges if e.protocol == CommKind.POSTGRES)
    assert edge.source == "orders" and edge.target == "bus_postgres"
    assert edge.configured is True


def test_infra_kind_reuses_detected_redis_node() -> None:
    # Redis detected from comms AND enabled as a dependency -> single node.
    services = [ServiceComms(service="orders", capabilities=[_peer(CommKind.REDIS)])]
    graph = build_service_graph(services, infra_kinds=["redis"])
    redis_nodes = [n for n in graph.nodes if n.label == "redis"]
    assert len(redis_nodes) == 1

"""Build a service-connection graph from detected + user-configured comms.

Turns per-service communication capabilities (:mod:`comm_detector`) into a graph
of nodes (services, brokers, datastores) and edges (kafka/rabbitmq/grpc/redis/http
links). Auto-infers the obvious links (a Kafka bus connecting every Kafka
participant; a lone gRPC server wired to its clients) and merges any explicit
connections the operator configured. Also renders a Mermaid diagram for the UI.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field

from app.services.comm_detector import CommKind, CommRole, ServiceComms

_ID_RE = re.compile(r"[^a-zA-Z0-9_]")
_BUS_KINDS = (CommKind.KAFKA, CommKind.RABBITMQ, CommKind.REDIS)
_BROKER_KINDS = frozenset({CommKind.KAFKA, CommKind.RABBITMQ})


def infra_node_id(kind: str) -> str:
    """Stable node id for a broker/datastore, shared by detection and dependencies."""
    return f"bus_{kind}"


class NodeType(str, Enum):
    SERVICE = "service"
    BROKER = "broker"       # kafka / rabbitmq
    DATASTORE = "datastore"  # redis / postgres / mysql / mariadb / mongodb


class GraphNode(BaseModel):
    id: str
    label: str
    type: NodeType
    framework: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    protocol: CommKind
    configured: bool = False  # True = operator-defined, False = auto-inferred

    def key(self) -> tuple[str, str, str]:
        return (self.source, self.target, self.protocol.value)


class ExplicitConnection(BaseModel):
    """An operator-configured edge between two services (by name)."""

    source: str
    target: str
    protocol: CommKind


class ServiceGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


def _node_id(name: str) -> str:
    return _ID_RE.sub("_", name.strip()).strip("_").lower() or "node"


def build_service_graph(
    services: list[ServiceComms],
    *,
    explicit_connections: list[ExplicitConnection] | None = None,
    frameworks: dict[str, str] | None = None,
    infra_kinds: list[str] | None = None,
) -> ServiceGraph:
    """Assemble nodes + edges from detected capabilities and operator connections.

    ``infra_kinds`` are the workspace's enabled dependency kinds (postgres, mysql,
    mariadb, mongodb, redis, kafka, rabbitmq). Each becomes a broker/datastore node
    so operators can wire services to it in the graph editor even when the code scan
    did not surface an explicit client.
    """
    frameworks = frameworks or {}
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def ensure_service(name: str) -> str:
        nid = _node_id(name)
        if nid not in nodes:
            nodes[nid] = GraphNode(
                id=nid, label=name, type=NodeType.SERVICE, framework=frameworks.get(name)
            )
        return nid

    def resolve_node(name: str) -> str:
        """Resolve a connection endpoint to an existing node (service or infra)."""
        nid = _node_id(name)
        if nid in nodes:  # existing service OR broker/datastore node id
            return nid
        return ensure_service(name)

    def add_edge(source: str, target: str, protocol: CommKind, *, configured: bool) -> None:
        if source == target:
            return
        edge = GraphEdge(source=source, target=target, protocol=protocol, configured=configured)
        if edge.key() not in seen_edges:
            seen_edges.add(edge.key())
            edges.append(edge)

    for sc in services:
        ensure_service(sc.service)

    # Bus protocols: one broker/datastore node, every participant links to it.
    for kind in _BUS_KINDS:
        participants = [sc for sc in services if kind in sc.kinds()]
        if not participants:
            continue
        broker_id = infra_node_id(kind.value)
        nodes[broker_id] = GraphNode(
            id=broker_id,
            label=kind.value,
            type=NodeType.DATASTORE if kind == CommKind.REDIS else NodeType.BROKER,
        )
        for sc in participants:
            add_edge(_node_id(sc.service), broker_id, kind, configured=False)

    # Enabled dependency infra (databases, caches, brokers): show a node per kind so
    # services can be wired to it. Reuses any node already created from detection.
    for raw_kind in infra_kinds or []:
        kind_str = str(raw_kind).strip().lower()
        if not kind_str:
            continue
        node_id = infra_node_id(kind_str)
        if node_id in nodes:
            continue
        is_broker = kind_str in {k.value for k in _BROKER_KINDS}
        nodes[node_id] = GraphNode(
            id=node_id,
            label=kind_str,
            type=NodeType.BROKER if is_broker else NodeType.DATASTORE,
        )

    # gRPC: only auto-wire when exactly one server exists (target is unambiguous).
    servers = [
        sc.service
        for sc in services
        if any(c.kind == CommKind.GRPC and c.role == CommRole.SERVER for c in sc.capabilities)
    ]
    clients = [
        sc.service
        for sc in services
        if any(c.kind == CommKind.GRPC and c.role == CommRole.CLIENT for c in sc.capabilities)
    ]
    if len(servers) == 1:
        target_id = _node_id(servers[0])
        for client in clients:
            add_edge(_node_id(client), target_id, CommKind.GRPC, configured=False)

    # Operator-configured connections always win (added, and marked configured).
    # Endpoints may be services or infra nodes (e.g. a service wired to postgres).
    for conn in explicit_connections or []:
        src = resolve_node(conn.source)
        dst = resolve_node(conn.target)
        # If this pair was auto-inferred, promote it to configured.
        auto_key = (src, dst, conn.protocol.value)
        if auto_key in seen_edges:
            for edge in edges:
                if edge.key() == auto_key:
                    edge.configured = True
        else:
            add_edge(src, dst, conn.protocol, configured=True)

    return ServiceGraph(nodes=list(nodes.values()), edges=edges)


_MERMAID_OPEN = {
    NodeType.SERVICE: '["',
    NodeType.BROKER: '{{"',
    NodeType.DATASTORE: '[("',
}
_MERMAID_CLOSE = {
    NodeType.SERVICE: '"]',
    NodeType.BROKER: '"}}',
    NodeType.DATASTORE: '")]',
}


def _node_subtitle(node: GraphNode) -> str:
    """A short muted line under the node name (framework or role)."""
    if node.type == NodeType.SERVICE:
        return node.framework or "service"
    if node.type == NodeType.BROKER:
        return "broker"
    return "cache" if node.label == "redis" else "database"


def graph_to_mermaid(graph: ServiceGraph) -> str:
    """Render the graph as a Mermaid ``flowchart LR`` for the dashboard.

    Each node carries its type as a class (``service`` / ``datastore`` / ``broker``)
    plus a subtitle line, so the UI can style them as modern accent-colored cards.
    """
    lines = ["flowchart LR"]
    for node in graph.nodes:
        subtitle = _node_subtitle(node)
        label = f"{node.label}<br/><small>{subtitle}</small>"
        lines.append(f"  {node.id}{_MERMAID_OPEN[node.type]}{label}{_MERMAID_CLOSE[node.type]}")
    for edge in graph.edges:
        arrow = "-->" if edge.configured else "-.->"
        lines.append(f"  {edge.source} {arrow}|{edge.protocol.value}| {edge.target}")
    for node in graph.nodes:
        lines.append(f"  class {node.id} {node.type.value}")
    # Per-type accent palette (the UI adds rounding, depth, and typography on top).
    lines.append("  classDef service fill:#161f27,stroke:#2dd4bf,stroke-width:2px,color:#eaf3f5")
    lines.append("  classDef datastore fill:#141c28,stroke:#5aa2f0,stroke-width:2px,color:#e2edfb")
    lines.append("  classDef broker fill:#20190f,stroke:#f0a94b,stroke-width:2px,color:#fbe7c6")
    return "\n".join(lines)

"""Workspace service-graph API: rebuild graph from persisted comms + connections."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.provisioning import ProvisioningService


def _row(snapshot: dict) -> SimpleNamespace:
    return SimpleNamespace(id="w1", wizard_config_json=json.dumps(snapshot))


def _svc() -> ProvisioningService:
    return ProvisioningService(session=MagicMock())


_KAFKA_SNAPSHOT = {
    "repos": ["https://github.com/acme/orders.git", "https://github.com/acme/billing.git"],
    "detection": {
        "services": [
            {"name": "orders", "framework": "fastapi"},
            {"name": "billing", "framework": "express"},
        ]
    },
    "service_comms": [
        {"service": "orders", "capabilities": [{"kind": "kafka", "role": "peer", "evidence": "dep"}]},
        {"service": "billing", "capabilities": [{"kind": "kafka", "role": "peer", "evidence": "dep"}]},
    ],
    "service_connections": [],
}


def test_build_graph_from_persisted_comms() -> None:
    resp = _svc()._build_service_graph_response(_row(_KAFKA_SNAPSHOT))
    assert resp.repos == _KAFKA_SNAPSHOT["repos"]
    kafka_edges = [e for e in resp.edges if e["protocol"] == "kafka"]
    assert len(kafka_edges) == 2  # both services -> kafka bus
    assert "flowchart LR" in resp.mermaid
    # Broker node present.
    assert any(n["type"] == "broker" and n["label"] == "kafka" for n in resp.nodes)


def test_explicit_connection_is_included_and_configured() -> None:
    snapshot = dict(_KAFKA_SNAPSHOT)
    snapshot["service_connections"] = [
        {"source": "orders", "target": "billing", "protocol": "http"}
    ]
    resp = _svc()._build_service_graph_response(_row(snapshot))
    http_edges = [e for e in resp.edges if e["protocol"] == "http"]
    assert len(http_edges) == 1
    assert http_edges[0]["configured"] is True
    assert http_edges[0]["source"] == "orders"


def test_empty_or_missing_snapshot_is_safe() -> None:
    assert _svc()._build_service_graph_response(SimpleNamespace(id="w", wizard_config_json=None)).nodes == []
    assert _svc()._build_service_graph_response(_row({})).edges == []
    # Malformed JSON must not raise.
    bad = SimpleNamespace(id="w", wizard_config_json="{not json")
    assert _svc()._build_service_graph_response(bad).mermaid.startswith("flowchart")


def test_linked_repos_surface_as_service_nodes() -> None:
    snapshot = {
        "linked_repos": [
            {"kind": "gitlab", "git_repo_url": "https://github.com/acme/frontend.git", "git_branch": "main"},
            {"kind": "github", "git_repo_url": "https://github.com/acme/backend.git", "git_branch": "main", "full_name": "acme/backend", "installation_id": 1},
        ],
        "dependencies": {"postgres": {"enabled": True}, "redis": {"enabled": True}},
    }
    resp = _svc()._build_service_graph_response(_row(snapshot))
    service_nodes = {n["label"] for n in resp.nodes if n["type"] == "service"}
    assert service_nodes == {"frontend", "backend"}
    datastores = {n["label"] for n in resp.nodes if n["type"] == "datastore"}
    assert datastores == {"postgres", "redis"}


def test_legacy_single_repo_surfaces_as_service_node() -> None:
    snapshot = {
        "git_repo_url": "https://gitlab.com/acme/checkout.git",
        "git_branch": "main",
        "dependencies": {"postgres": {"enabled": True}},
    }
    resp = _svc()._build_service_graph_response(_row(snapshot))
    assert any(n["type"] == "service" and n["label"] == "checkout" for n in resp.nodes)

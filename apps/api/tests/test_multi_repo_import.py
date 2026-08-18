"""Multi-repo workspace import: service merge (collision-safe) + connection graph."""

from __future__ import annotations

from pathlib import Path

from pkg.detector.models import DetectedService

from app.services.comm_detector import CommKind
from app.services.multi_repo_import import (
    RepoDetection,
    assemble_multi_repo,
    merge_repo_services,
)


def _svc(name: str, *, path: str = ".", framework: str = "generic") -> DetectedService:
    return DetectedService(id=name, name=name, path=path, framework=framework)


def test_merge_single_repo_unchanged() -> None:
    dets = [RepoDetection(name="orders", root_dir=Path("/x"), services=[_svc("api"), _svc("worker")])]
    merged = merge_repo_services(dets)
    assert [s.name for s in merged] == ["api", "worker"]


def test_merge_prefixes_only_colliding_names() -> None:
    dets = [
        RepoDetection(name="orders", root_dir=Path("/a"), services=[_svc("api"), _svc("web")]),
        RepoDetection(name="billing", root_dir=Path("/b"), services=[_svc("api")]),
    ]
    merged = merge_repo_services(dets)
    names = {s.name for s in merged}
    # "api" collided -> both prefixed; "web" unique -> kept.
    assert names == {"orders-api", "web", "billing-api"}
    # ids track names for downstream K8s resource naming.
    assert all(s.id == s.name for s in merged)


def test_assemble_builds_kafka_graph_across_repos(tmp_path: Path) -> None:
    orders = tmp_path / "orders"
    billing = tmp_path / "billing"
    orders.mkdir()
    billing.mkdir()
    (orders / "package.json").write_text('{"dependencies":{"kafkajs":"^2"}}', encoding="utf-8")
    (billing / "requirements.txt").write_text("kafka-python==2.0\n", encoding="utf-8")

    dets = [
        RepoDetection(name="orders", root_dir=orders, services=[_svc("orders", framework="express")]),
        RepoDetection(name="billing", root_dir=billing, services=[_svc("billing", framework="fastapi")]),
    ]
    assembly = assemble_multi_repo(dets)

    assert {s.name for s in assembly.services} == {"orders", "billing"}
    kafka_edges = [e for e in assembly.graph.edges if e.protocol == CommKind.KAFKA]
    assert len(kafka_edges) == 2  # both services -> kafka broker
    assert "flowchart LR" in assembly.mermaid
    assert "kafka" in assembly.mermaid


def test_assemble_detects_per_service_subpath(tmp_path: Path) -> None:
    # Monorepo-style: service lives in apps/api and only it uses rabbitmq.
    repo = tmp_path / "mono"
    (repo / "apps" / "api").mkdir(parents=True)
    (repo / "apps" / "web").mkdir(parents=True)
    (repo / "apps" / "api" / "requirements.txt").write_text("aio-pika==9\n", encoding="utf-8")
    (repo / "apps" / "web" / "package.json").write_text('{"dependencies":{"react":"^18"}}', encoding="utf-8")

    dets = [
        RepoDetection(
            name="mono",
            root_dir=repo,
            services=[_svc("api", path="apps/api"), _svc("web", path="apps/web")],
        )
    ]
    assembly = assemble_multi_repo(dets)
    rabbit_edges = [e for e in assembly.graph.edges if e.protocol == CommKind.RABBITMQ]
    # Only the api service participates in rabbitmq.
    assert len(rabbit_edges) == 1
    assert rabbit_edges[0].source == "api"

"""Multi-repo workspace assembly: merge per-repo detections + build the graph.

A workspace can import several repositories (microservices). This merges the
per-repo :class:`DetectionResult` service lists into one namespaced list
(collision-safe), runs the communication detector per service, and assembles the
inter-service connection graph.

Pure and importer-agnostic: callers clone each repo (via the existing
``GitImporter``) and pass ``RepoDetection`` records here, so this layer is fully
unit-testable without git or a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pkg.detector.models import DetectedService

from app.core.logging import get_logger
from app.services.comm_detector import ServiceComms, detect_service_comms
from app.services.service_graph import (
    ExplicitConnection,
    ServiceGraph,
    build_service_graph,
    graph_to_mermaid,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class RepoDetection:
    """One imported repo: its short name, clone root, and detected services.

    ``mount_prefix`` is where this repo lives relative to the shared workspace
    root once assembled (e.g. ``apps/billing`` per repo in a multi-repo import,
    ``""`` for a single-repo import that stays at the root). Service ``path``
    values (clone-relative) are rewritten with this prefix so the generator finds
    each service inside the multi-repo tree, while communication detection still
    reads the real clone dir.
    """

    name: str
    root_dir: Path
    services: list[DetectedService] = field(default_factory=list)
    mount_prefix: str = ""


@dataclass(slots=True)
class MultiRepoAssembly:
    services: list[DetectedService]
    graph: ServiceGraph
    mermaid: str
    comms: list[ServiceComms] = field(default_factory=list)


def _slug(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9-]", "-", value.strip().lower()).strip("-")[:63] or "svc"


def merge_repo_services(detections: list[RepoDetection]) -> list[DetectedService]:
    """Flatten per-repo services into one list, renaming only on name collision.

    Single-repo (or no collisions) keeps names/ids unchanged so existing behavior
    is preserved. When two repos expose the same service name, both are prefixed
    with their repo name (``orders-api`` / ``billing-api``) to stay K8s-safe.
    """
    # Count name occurrences across all repos to know which collide.
    counts: dict[str, int] = {}
    for det in detections:
        for svc in det.services:
            counts[svc.name] = counts.get(svc.name, 0) + 1

    merged: list[DetectedService] = []
    used: set[str] = set()
    for det in detections:
        repo_slug = _slug(det.name)
        for svc in det.services:
            name = svc.name
            if counts.get(svc.name, 0) > 1:
                name = _slug(f"{repo_slug}-{svc.name}")
            # Guarantee uniqueness even if prefixing still collides.
            base = name
            n = 2
            while name in used:
                name = _slug(f"{base}-{n}")
                n += 1
            used.add(name)
            path = _mounted_path(det.mount_prefix, svc.path)
            update: dict[str, object] = {}
            if name != svc.name:
                update["name"] = name
                update["id"] = name
            if path != svc.path:
                update["path"] = path
            merged.append(svc.model_copy(update=update) if update else svc)
    return merged


def _mounted_path(mount_prefix: str, svc_path: str) -> str:
    """Combine a repo's workspace mount prefix with a clone-relative service path."""
    prefix = (mount_prefix or "").strip("/")
    rel = (svc_path or ".").strip("/")
    if not prefix:
        return svc_path
    if rel in {"", "."}:
        return prefix
    return f"{prefix}/{rel}"


def assemble_multi_repo(
    detections: list[RepoDetection],
    *,
    explicit_connections: list[ExplicitConnection] | None = None,
) -> MultiRepoAssembly:
    """Merge services and build the inter-service connection graph + Mermaid."""
    merged = merge_repo_services(detections)

    # Map merged service -> (repo root, service subpath) for per-service comm scan.
    comms: list[ServiceComms] = []
    frameworks: dict[str, str] = {}
    # Re-walk in the same order merge produced, pairing merged names to their repo/path.
    idx = 0
    for det in detections:
        for svc in det.services:
            merged_svc = merged[idx]
            idx += 1
            frameworks[merged_svc.name] = svc.framework
            svc_root = det.root_dir / svc.path if svc.path not in {"", "."} else det.root_dir
            detected = detect_service_comms(svc_root, service_name=merged_svc.name)
            comms.append(detected)

    graph = build_service_graph(
        comms, explicit_connections=explicit_connections, frameworks=frameworks
    )
    logger.info(
        "multi_repo_assembled",
        repos=len(detections),
        services=len(merged),
        edges=len(graph.edges),
    )
    return MultiRepoAssembly(
        services=merged, graph=graph, mermaid=graph_to_mermaid(graph), comms=comms
    )

"""Regression tests: workspaces with in-cluster datastores must be previewable.

Two failures made any postgres/redis workspace fail on preview/apply:
1. The preview ResourceQuota (requests.memory=512Mi) rejected datastore pods, so
   the app hung in PodInitializing on its wait-for-db init container.
2. The zero-trust NetworkPolicy allowed egress only to DNS, so on a
   policy-enforcing CNI the app could never reach postgres/redis.
"""

from __future__ import annotations

import yaml

from app.core.config import Settings
from app.services.k8s_spec import (
    build_preview_network_policy,
    render_workspace_network_policy_yaml,
)
from app.services.workload_dependencies import DataStoreKind, init_container_wait_blocks


def _to_mi(value: str) -> int:
    return int(float(value[:-2]) * 1024) if value.endswith("Gi") else int(float(value[:-2]))


def test_default_quota_fits_app_plus_datastores() -> None:
    # app(128Mi) + postgres(256Mi) + redis(128Mi) + mysql(256Mi) + mongodb(256Mi)
    # ~= 1024Mi of requests must fit under the default namespace quota.
    s = Settings()
    assert _to_mi(s.kubernetes_memory_request) >= 1024
    # Old value (512Mi) was the bug.
    assert _to_mi(s.kubernetes_memory_request) > 512


def test_wait_for_db_init_containers_have_small_resources() -> None:
    blocks = init_container_wait_blocks([DataStoreKind.POSTGRES, DataStoreKind.REDIS])
    # Parse the init container list (wrap into a minimal pod fragment).
    doc = yaml.safe_load("initContainers:" + blocks)
    inits = doc["initContainers"]
    assert len(inits) == 2
    for c in inits:
        # Small explicit requests so they don't inflate the app pod to the
        # LimitRange default (256Mi), which pushed the namespace over quota.
        assert c["resources"]["requests"]["memory"] == "16Mi"
        assert c["resources"]["limits"]["memory"] == "32Mi"


def test_preview_network_policy_allows_same_namespace_egress() -> None:
    policy = build_preview_network_policy(namespace="lp-demo", labels={}, listen_ports=[8000])
    egress = policy.spec.egress
    # DNS rule + an intra-namespace pod rule (empty podSelector, no namespaceSelector).
    intra = [
        rule
        for rule in egress
        for peer in (rule.to or [])
        if peer.pod_selector is not None and peer.namespace_selector is None
    ]
    assert intra, "NetworkPolicy must allow egress to same-namespace datastore pods"


def test_generated_network_policy_allows_same_namespace_egress() -> None:
    np = yaml.safe_load(
        render_workspace_network_policy_yaml(
            namespace="lp-demo", environment_name="demo", common_labels="    app: app"
        )
    )
    egress = np["spec"]["egress"]
    has_intra = any(
        any("podSelector" in peer and "namespaceSelector" not in peer for peer in (rule.get("to") or []))
        for rule in egress
    )
    assert has_intra, "generated NetworkPolicy must allow same-namespace (datastore) egress"

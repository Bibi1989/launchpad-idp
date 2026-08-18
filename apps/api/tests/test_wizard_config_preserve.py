"""Workspace update must preserve multi-repo + service-graph state in wizard_config."""

from __future__ import annotations

import json

from app.schemas.cloud import (
    CloudCredentials,
    IaCEngine,
    LocalCloudConfig,
    LocalResources,
    ProvisioningWizardRequest,
)
from app.services.provisioning import ProvisioningService


def _request() -> ProvisioningWizardRequest:
    return ProvisioningWizardRequest(
        name="graph-ws",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources(cluster_name="launchpad")),
        credentials=CloudCredentials(),
    )


def test_update_preserves_service_graph_and_repos() -> None:
    prior = {
        "name": "graph-ws",
        "repos": ["https://github.com/acme/orders.git", "https://github.com/acme/billing.git"],
        "service_comms": [{"service": "orders", "capabilities": []}],
        "service_connections": [{"source": "orders", "target": "billing", "protocol": "http"}],
        "service_graph": {"nodes": [], "edges": []},
        "service_graph_mermaid": "flowchart LR",
        "linked_repos": [{"kind": "gitlab", "git_repo_url": "https://gitlab.com/a/b.git", "git_branch": "main"}],
        "detection": {"services": [{"name": "orders"}]},
    }
    # A plain workspace update carries none of these fields.
    out = json.loads(ProvisioningService._wizard_config_json(_request(), preserve=prior))
    assert out["repos"] == prior["repos"]
    assert out["service_comms"] == prior["service_comms"]
    assert out["service_connections"] == prior["service_connections"]
    assert out["service_graph_mermaid"] == "flowchart LR"
    assert out["linked_repos"] == prior["linked_repos"]
    assert out["detection"] == prior["detection"]


def test_no_preserve_keeps_payload_clean() -> None:
    out = json.loads(ProvisioningService._wizard_config_json(_request()))
    assert "service_comms" not in out
    assert out["name"] == "graph-ws"

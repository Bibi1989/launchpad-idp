from __future__ import annotations

import json
from pathlib import Path

from app.schemas.cloud import (
    WorkspaceArtifactsMode,
    GcpResources,
    IaCEngine,
    KubernetesPackaging,
    ProvisioningWizardRequest,
    GcpCloudConfig,
    CloudCredentials,
)
from app.services.iac_generator import IaCGenerator


def test_wizard_snapshot_roundtrip(tmp_path: Path) -> None:
    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="demo-stack",
        iac_engine=IaCEngine.TERRAFORM,
        artifact_mode=WorkspaceArtifactsMode.BOTH,
        cloud=GcpCloudConfig(
            resources=GcpResources(project_id="my-project", gke=True),
        ),
        credentials=CloudCredentials(),
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
    )
    workspace = tmp_path / "demo-stack"
    workspace.mkdir()
    gen.write_wizard_snapshot(workspace, request)
    snapshot = gen.read_wizard_snapshot(workspace)
    assert snapshot is not None
    assert snapshot["name"] == "demo-stack"
    assert snapshot["iac_engine"] == "terraform"
    assert snapshot["artifact_mode"] == "both"
    assert "credentials" not in snapshot
    cloud = snapshot["cloud"]
    assert isinstance(cloud, dict)
    assert cloud["provider"] == "gcp"
    resources = cloud["resources"]
    assert isinstance(resources, dict)
    assert resources["gke"] is True
    assert resources["project_id"] == "my-project"


def test_regenerate_rewrites_snapshot(tmp_path: Path) -> None:
    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="regen-stack",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=GcpCloudConfig(resources=GcpResources(project_id="proj-a")),
        credentials=CloudCredentials(),
    )
    bundle = gen.generate(request)
    root = Path(bundle.root_dir)
    assert (root / ".launchpad" / "wizard.json").is_file()

    updated = ProvisioningWizardRequest(
        name="regen-stack",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=GcpCloudConfig(
            resources=GcpResources(project_id="proj-b", cloud_run=True),
        ),
        credentials=CloudCredentials(),
    )
    files = gen.regenerate(root, updated)
    assert any("infra/terraform" in path or path.startswith("infra/") for path in files)
    snapshot = json.loads((root / ".launchpad" / "wizard.json").read_text(encoding="utf-8"))
    assert snapshot["cloud"]["resources"]["project_id"] == "proj-b"
    assert snapshot["cloud"]["resources"]["cloud_run"] is True

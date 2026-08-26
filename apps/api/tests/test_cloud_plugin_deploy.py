"""Tests for cloud plugin defaults and attach VM ordering."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.schemas.cloud import (
    CloudPluginTarget,
    CloudProvider,
    GcpCloudConfig,
    GcpResources,
    IaCEngine,
    InstanceProcessStrategy,
    ProvisioningWizardRequest,
    RunningInstanceConfig,
    RunningInstanceKind,
    WorkspaceRuntimeMode,
)
from app.services.attach_deploy import deploy_attach
from app.services.cloud_plugin_defaults import apply_cloud_plugin_defaults


def test_apply_cloud_plugin_enables_gce_and_region() -> None:
    request = ProvisioningWizardRequest(
        name="demo",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=GcpCloudConfig(
            provider=CloudProvider.GCP,
            resources=GcpResources(project_id="demo-proj"),
        ),
        runtime_mode=WorkspaceRuntimeMode.KUBERNETES,
    )
    plugin = CloudPluginTarget(provider="gcp-gce", service="gce", region="europe-west3")
    updated = apply_cloud_plugin_defaults(request, plugin)
    assert updated.cloud.resources.compute_instance is True
    assert updated.cloud.resources.region == "europe-west3"
    assert updated.runtime_mode == WorkspaceRuntimeMode.RUNNING_INSTANCE
    assert updated.running_instance.region == "europe-west3"
    assert updated.cloud_plugin == plugin


def test_attach_docker_cloud_vm_provisions_before_image_build(tmp_path: Path) -> None:
    """Docker cloud VMs should exist in GCP before a long registry build starts."""
    (tmp_path / "Dockerfile").write_text("FROM nginx:alpine\n", encoding="utf-8")
    settings = Settings.model_construct(default_workload_image="nginx:latest")
    order: list[str] = []

    def fake_provision(**_kwargs):
        order.append("provision")
        return RunningInstanceConfig(
            kind=RunningInstanceKind.VM,
            host="34.1.2.3",
            listen_port=8080,
            region="europe-west3-a",
        )

    def fake_resolve(*_a, **_k):
        order.append("image")
        return "europe-west3-docker.pkg.dev/demo/app/img:tag"

    with (
        patch(
            "app.services.cloud_instance_compute.provision_cloud_vm",
            side_effect=fake_provision,
        ),
        patch("app.services.attach_deploy.resolve_instance_image", side_effect=fake_resolve),
        patch("app.services.attach_deploy._wait_for_vm_ssh", return_value=None),
        patch("app.services.attach_deploy._deploy_vm_docker") as mock_docker,
    ):
        from app.services.kubernetes import ProvisionedResources

        mock_docker.return_value = ProvisionedResources(
            namespace="instance-aaaaaaaa",
            preview_url="http://34.1.2.3:8080",
        )
        deploy_attach(
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            git_branch="main",
            git_repo_url="",
            ttl_expires_at=None,
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.VM,
                process_strategy=InstanceProcessStrategy.DOCKER,
                listen_port=8080,
                region="europe-west3",
            ),
            workspace_root=tmp_path,
            settings=settings,
            cloud_provider="gcp",
        )
    assert order == ["provision", "image"]

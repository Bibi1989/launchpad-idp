"""Tests for workspace runtime-mode matrix and preview deploy plan resolver."""

from __future__ import annotations

import pytest

from app.schemas.cloud import (
    AwsCloudConfig,
    AwsResources,
    AzureCloudConfig,
    AzureResources,
    CloudProvider,
    ContainerScaffoldConfig,
    GcpCloudConfig,
    GcpResources,
    KubernetesPackaging,
    LocalCloudConfig,
    LocalResources,
    RunningInstanceConfig,
    RunningInstanceKind,
    WorkspaceArtifactsMode,
    WorkspaceRuntimeMode,
    WorkspaceWizardConfig,
    WorkloadDependenciesConfig,
    DataStoreDependency,
    DependencyPlacement,
    IaCEngine,
)
from app.schemas.k8s import DeployMode
from app.services.preview_deploy_plan import resolve_preview_deploy_plan
from app.services.runtime_mode import (
    RuntimeModeViolation,
    ensure_autocreate_vm_resources,
    normalize_artifacts_for_runtime_mode,
    validate_runtime_mode,
)
from app.schemas.cloud import ProvisioningWizardRequest, CloudCredentials


def test_ensure_autocreate_vm_enables_gcp_compute_instance() -> None:
    cloud = GcpCloudConfig(
        provider=CloudProvider.GCP,
        resources=GcpResources(project_id="demo", compute_instance=False),
    )
    patched = ensure_autocreate_vm_resources(
        cloud,
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
        running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM),
    )
    assert isinstance(patched, GcpCloudConfig)
    assert patched.resources.compute_instance is True


def test_wizard_request_forces_compute_instance_for_gcp_vm() -> None:
    req = ProvisioningWizardRequest(
        name="vm-preview",
        iac_engine=IaCEngine.PULUMI,
        cloud=GcpCloudConfig(
            provider=CloudProvider.GCP,
            resources=GcpResources(project_id="demo", compute_instance=False),
        ),
        credentials=CloudCredentials(),
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
        running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM),
        artifact_mode=WorkspaceArtifactsMode.IAC_ONLY,
        kubernetes_packaging=KubernetesPackaging.NONE,
    )
    assert isinstance(req.cloud, GcpCloudConfig)
    assert req.cloud.resources.compute_instance is True
    assert req.ansible.enabled is False
    assert req.config_tool.value == "cloud-init"


def test_wizard_request_enables_ansible_when_config_tool_is_ansible() -> None:
    req = ProvisioningWizardRequest(
        name="vm-ansible",
        iac_engine=IaCEngine.PULUMI,
        cloud=GcpCloudConfig(
            provider=CloudProvider.GCP,
            resources=GcpResources(project_id="demo", compute_instance=False),
        ),
        credentials=CloudCredentials(),
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
        running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM),
        artifact_mode=WorkspaceArtifactsMode.IAC_ONLY,
        kubernetes_packaging=KubernetesPackaging.NONE,
        config_tool="ansible",
    )
    assert req.ansible.enabled is True


def test_ensure_ansible_for_cloud_vm_without_host() -> None:
    from app.schemas.cloud import AnsibleConfig
    from app.services.runtime_mode import ensure_ansible_for_vm_runtime

    skipped = ensure_ansible_for_vm_runtime(
        AnsibleConfig(enabled=False),
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
        running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM),
    )
    assert skipped.enabled is False

    patched = ensure_ansible_for_vm_runtime(
        AnsibleConfig(enabled=False),
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
        running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM),
        config_tool="ansible",
    )
    assert patched.enabled is True


def test_compose_local_only() -> None:
    local = LocalCloudConfig(resources=LocalResources())
    validate_runtime_mode(local, WorkspaceRuntimeMode.DOCKER_COMPOSE)

    gcp = GcpCloudConfig(resources=GcpResources(project_id="demo-proj", gke=True))
    with pytest.raises(RuntimeModeViolation):
        validate_runtime_mode(gcp, WorkspaceRuntimeMode.DOCKER_COMPOSE)


def test_normalize_compose_forces_container_scaffold() -> None:
    cloud = LocalCloudConfig(resources=LocalResources())
    artifact, packaging, scaffold, _ = normalize_artifacts_for_runtime_mode(
        cloud=cloud,
        runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
        artifact_mode=WorkspaceArtifactsMode.MANIFEST_ONLY,
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
        container_scaffold=ContainerScaffoldConfig(enabled=False),
    )
    assert artifact == WorkspaceArtifactsMode.IAC_ONLY
    assert packaging == KubernetesPackaging.NONE
    assert scaffold.enabled is True
    assert scaffold.generate_docker_compose is True


def test_wizard_request_compose_skips_kind_packaging() -> None:
    req = ProvisioningWizardRequest(
        name="compose-demo",
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
        artifact_mode=WorkspaceArtifactsMode.MANIFEST_ONLY,
    )
    assert req.runtime_mode == WorkspaceRuntimeMode.DOCKER_COMPOSE
    assert req.kubernetes_packaging == KubernetesPackaging.NONE
    assert req.container_scaffold.enabled is True


def test_wizard_request_rejects_cloud_compose() -> None:
    with pytest.raises(Exception):
        ProvisioningWizardRequest(
            name="bad-compose",
            cloud=GcpCloudConfig(resources=GcpResources(project_id="demo-proj")),
            credentials=CloudCredentials(),
            runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
        )


def test_running_instance_vm_without_host_ok_for_autocreate_providers() -> None:
    # VM without a host no longer fails validation for providers that can supply one:
    # local falls back to a local Docker preview; GCP/AWS auto-create the VM.
    vm = RunningInstanceConfig(kind=RunningInstanceKind.VM)
    for cloud in (
        LocalCloudConfig(resources=LocalResources()),
        GcpCloudConfig(resources=GcpResources(project_id="demo-proj")),
        AwsCloudConfig(resources=AwsResources()),
    ):
        # Should not raise.
        validate_runtime_mode(cloud, WorkspaceRuntimeMode.RUNNING_INSTANCE, vm)


def test_running_instance_vm_requires_host_for_azure() -> None:
    # Azure VM auto-provisioning is not implemented, so a host is still required.
    azure = AzureCloudConfig(resources=AzureResources(resource_group="lp-rg"))
    with pytest.raises(RuntimeModeViolation):
        validate_runtime_mode(
            azure,
            WorkspaceRuntimeMode.RUNNING_INSTANCE,
            RunningInstanceConfig(kind=RunningInstanceKind.VM),
        )
    # With a host it passes.
    validate_runtime_mode(
        azure,
        WorkspaceRuntimeMode.RUNNING_INSTANCE,
        RunningInstanceConfig(kind=RunningInstanceKind.VM, host="10.0.0.9"),
    )


def test_running_instance_local_machine_ok() -> None:
    req = ProvisioningWizardRequest(
        name="attach-local",
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
        running_instance=RunningInstanceConfig(kind=RunningInstanceKind.LOCAL_MACHINE),
    )
    assert req.running_instance.kind == RunningInstanceKind.LOCAL_MACHINE
    assert req.kubernetes_packaging == KubernetesPackaging.NONE


def test_preview_plan_compose() -> None:
    config = WorkspaceWizardConfig(
        name="compose-demo",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
        dependencies=WorkloadDependenciesConfig(
            postgres=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
            redis=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
        ),
    )
    plan = resolve_preview_deploy_plan(config)
    assert plan.deploy_mode == DeployMode.COMPOSE
    assert plan.skip_local_cluster is True
    assert plan.enable_postgres is True
    assert plan.enable_redis is True


def test_preview_plan_kubernetes_manifest() -> None:
    config = WorkspaceWizardConfig(
        name="k8s-demo",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        runtime_mode=WorkspaceRuntimeMode.KUBERNETES,
        artifact_mode=WorkspaceArtifactsMode.MANIFEST_ONLY,
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
    )
    plan = resolve_preview_deploy_plan(config)
    assert plan.deploy_mode == DeployMode.MANIFEST
    assert plan.skip_local_cluster is False


def test_preview_plan_single_service_defaults_to_preview() -> None:
    config = WorkspaceWizardConfig(
        name="single-demo",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        runtime_mode=WorkspaceRuntimeMode.KUBERNETES,
        kubernetes_packaging=KubernetesPackaging.NONE,
    )
    plan = resolve_preview_deploy_plan(config, deployable_service_count=1)
    assert plan.deploy_mode == DeployMode.PREVIEW


def test_preview_plan_multi_service_forces_manifest() -> None:
    """Linked frontend + backend (2 services) must use per-service MANIFEST deploy,
    not the single-app PREVIEW path (which yields one generic ``app`` pod)."""
    config = WorkspaceWizardConfig(
        name="linked-demo",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        runtime_mode=WorkspaceRuntimeMode.KUBERNETES,
        kubernetes_packaging=KubernetesPackaging.NONE,
    )
    plan = resolve_preview_deploy_plan(config, deployable_service_count=2)
    assert plan.deploy_mode == DeployMode.MANIFEST
    assert plan.manifest_packaging == KubernetesPackaging.RAW_MANIFESTS.value


def test_preview_plan_multi_service_compose_still_compose() -> None:
    """docker_compose runtime is local Compose regardless of service count."""
    config = WorkspaceWizardConfig(
        name="compose-multi",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
    )
    plan = resolve_preview_deploy_plan(config, deployable_service_count=3)
    assert plan.deploy_mode == DeployMode.COMPOSE


def test_preview_plan_attach_serverless() -> None:
    config = WorkspaceWizardConfig(
        name="run-demo",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=GcpCloudConfig(resources=GcpResources(project_id="demo-proj", cloud_run=True)),
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
        running_instance=RunningInstanceConfig(
            kind=RunningInstanceKind.SERVERLESS,
            service_name="demo-svc",
        ),
    )
    plan = resolve_preview_deploy_plan(config)
    assert plan.deploy_mode == DeployMode.ATTACH
    assert plan.attach_kind == RunningInstanceKind.SERVERLESS.value
    assert plan.attach_service == "demo-svc"


def test_preview_plan_attach_aws_app_runner() -> None:
    config = WorkspaceWizardConfig(
        name="runner-demo",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=AwsCloudConfig(
            resources=AwsResources(app_runner=True, region="us-east-1"),
        ),
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
        running_instance=RunningInstanceConfig(
            kind=RunningInstanceKind.SERVERLESS,
            service_name="demo-runner",
            region="us-east-1",
        ),
    )
    plan = resolve_preview_deploy_plan(config)
    assert plan.deploy_mode == DeployMode.ATTACH
    assert plan.attach_kind == RunningInstanceKind.SERVERLESS.value
    assert plan.attach_service == "demo-runner"


def test_preview_plan_attach_vm() -> None:
    config = WorkspaceWizardConfig(
        name="vm-demo",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
        running_instance=RunningInstanceConfig(
            kind=RunningInstanceKind.VM,
            host="203.0.113.10",
        ),
    )
    plan = resolve_preview_deploy_plan(config)
    assert plan.deploy_mode == DeployMode.ATTACH
    assert plan.attach_kind == RunningInstanceKind.VM.value
    assert plan.attach_host == "203.0.113.10"

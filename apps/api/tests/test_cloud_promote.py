"""Tests for cloud promote retarget and primary service selection."""

from __future__ import annotations

from app.schemas.cloud import (
    CloudCredentials,
    CloudProvider,
    ContainerScaffoldConfig,
    ContainerServiceSpec,
    GcpCloudConfig,
    RunningInstanceConfig,
    RunningInstanceKind,
    WorkspaceRuntimeMode,
    WorkspaceWizardConfig,
)
from app.schemas.k8s import DeployMode
from app.services.cloud_promote import (
    apply_primary_service_selection,
    build_cloud_running_instance,
    cloud_config_for_promote,
    needs_cloud_retarget,
    promote_runtime_target,
    recommend_primary_service,
    resolve_attach_running_instance,
)


def test_needs_cloud_retarget_local_and_attach_compose() -> None:
    assert needs_cloud_retarget(source_provider="local", deploy_mode=DeployMode.ATTACH.value)
    assert needs_cloud_retarget(source_provider="local", deploy_mode=DeployMode.COMPOSE.value)
    assert not needs_cloud_retarget(source_provider="gcp", deploy_mode=DeployMode.PREVIEW.value)
    assert needs_cloud_retarget(source_provider="gcp", deploy_mode=DeployMode.ATTACH.value)


def test_promote_runtime_target_instance_vs_compose() -> None:
    instance_src = WorkspaceWizardConfig.model_validate(
        {
            "name": "src",
            "iac_engine": "terraform",
            "cloud": {"provider": "local", "resources": {}},
            "runtime_mode": WorkspaceRuntimeMode.RUNNING_INSTANCE.value,
        }
    )
    compose_src = instance_src.model_copy(
        update={"runtime_mode": WorkspaceRuntimeMode.DOCKER_COMPOSE}
    )
    assert promote_runtime_target(instance_src) == RunningInstanceKind.VM
    assert promote_runtime_target(compose_src) == RunningInstanceKind.SERVERLESS


def test_recommend_primary_service_prefers_frontend() -> None:
    services = [
        ContainerServiceSpec(name="api", app_kind="backend"),
        ContainerServiceSpec(name="web", app_kind="frontend"),
    ]
    assert recommend_primary_service(services) == "web"


def test_apply_primary_service_selection_exposes_only_primary() -> None:
    services = [
        ContainerServiceSpec(name="api", app_kind="backend"),
        ContainerServiceSpec(name="web", app_kind="frontend"),
    ]
    updated = apply_primary_service_selection(services, "api")
    by_name = {s.name: s for s in updated}
    assert by_name["api"].expose_preview is True
    assert by_name["web"].expose_preview is False


def test_cloud_config_for_promote_vm_vs_serverless() -> None:
    creds = CloudCredentials()
    vm = cloud_config_for_promote(CloudProvider.GCP, creds, target_kind=RunningInstanceKind.VM)
    assert isinstance(vm, GcpCloudConfig)
    assert vm.resources.artifact_registry is True
    assert vm.resources.cloud_run is False
    assert vm.resources.vpc is False
    assert vm.resources.subnets is False

    serverless = cloud_config_for_promote(
        CloudProvider.GCP,
        creds,
        target_kind=RunningInstanceKind.SERVERLESS,
    )
    assert isinstance(serverless, GcpCloudConfig)
    assert serverless.resources.cloud_run is True


def test_cloud_config_for_promote_vpc_subnets() -> None:
    creds = CloudCredentials()
    cfg = cloud_config_for_promote(
        CloudProvider.GCP,
        creds,
        target_kind=RunningInstanceKind.VM,
        create_vpc=True,
        create_subnets=True,
    )
    assert isinstance(cfg, GcpCloudConfig)
    assert cfg.resources.vpc is True
    assert cfg.resources.subnets is True

    aws = cloud_config_for_promote(
        CloudProvider.AWS,
        creds,
        target_kind=RunningInstanceKind.VM,
        create_vpc=True,
        create_subnets=False,
    )
    assert aws.resources.vpc is True
    assert aws.resources.subnets is False


def test_build_cloud_running_instance_vm() -> None:
    inst = build_cloud_running_instance(
        provider=CloudProvider.GCP,
        environment_name="demo-gcp",
        primary_service="web",
        source=RunningInstanceConfig(listen_port=3000),
        target_kind=RunningInstanceKind.VM,
    )
    assert inst.kind == RunningInstanceKind.VM
    assert inst.listen_port == 3000
    assert "web" in (inst.service_name or "")


def test_resolve_attach_running_instance_coerces_local_to_vm() -> None:
    local = RunningInstanceConfig(kind=RunningInstanceKind.LOCAL_MACHINE, listen_port=8080)
    resolved = resolve_attach_running_instance(
        local,
        cloud_provider="gcp",
        environment_name="demo-gcp",
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
    )
    assert resolved.kind == RunningInstanceKind.VM
    assert resolve_attach_running_instance(
        local,
        cloud_provider="local",
        environment_name="demo",
    ).kind == RunningInstanceKind.LOCAL_MACHINE


def test_build_cloud_running_instance_uses_requested_region() -> None:
    inst = build_cloud_running_instance(
        provider=CloudProvider.GCP,
        environment_name="demo-gcp",
        primary_service="web",
        source=RunningInstanceConfig(region="us-central1"),
        target_kind=RunningInstanceKind.VM,
        region="europe-west1",
    )
    assert inst.region == "europe-west1"


def test_build_cloud_running_instance_preserves_pm2_and_code_source() -> None:
    from app.schemas.cloud import InstanceCodeSource, InstanceProcessStrategy

    inst = build_cloud_running_instance(
        provider=CloudProvider.GCP,
        environment_name="demo-gcp",
        primary_service="web",
        source=RunningInstanceConfig(
            process_strategy=InstanceProcessStrategy.PM2,
            code_source=InstanceCodeSource.GITHUB,
            listen_port=3000,
        ),
        target_kind=RunningInstanceKind.VM,
        code_source=InstanceCodeSource.SSH,
    )
    assert inst.kind == RunningInstanceKind.VM
    assert inst.process_strategy == InstanceProcessStrategy.PM2
    assert inst.code_source == InstanceCodeSource.SSH


def test_build_cloud_promote_wizard_request_instance_vm() -> None:
    from app.services.cloud_promote import build_cloud_promote_wizard_request

    source = WorkspaceWizardConfig.model_validate(
        {
            "name": "src",
            "iac_engine": "terraform",
            "cloud": {"provider": "local", "resources": {}},
            "credentials": {},
            "has_credentials": False,
            "runtime_mode": WorkspaceRuntimeMode.RUNNING_INSTANCE.value,
            "container_scaffold": ContainerScaffoldConfig(
                enabled=True,
                generate_docker_compose=True,
                services=[
                    ContainerServiceSpec(name="web", app_kind="frontend"),
                    ContainerServiceSpec(name="api", app_kind="backend"),
                ],
            ).model_dump(mode="json"),
        }
    )
    req = build_cloud_promote_wizard_request(
        source,
        workspace_name="demo-gcp",
        provider=CloudProvider.GCP,
        credentials=CloudCredentials(gcp_sa_key_json='{"project_id":"demo-proj"}'),
        primary_service="web",
    )
    assert req.container_scaffold.generate_docker_compose is False
    assert req.running_instance.kind == RunningInstanceKind.VM
    exposed = [s for s in req.container_scaffold.services or [] if s.expose_preview]
    assert len(exposed) == 1
    assert exposed[0].name == "web"

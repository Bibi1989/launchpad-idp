"""Tests for cloud promote retarget and primary service selection."""

from __future__ import annotations

from app.schemas.cloud import (
    CloudCredentials,
    CloudProvider,
    ContainerScaffoldConfig,
    ContainerServiceSpec,
    GcpCloudConfig,
    KubernetesPackaging,
    RunningInstanceConfig,
    RunningInstanceKind,
    WorkspaceArtifactsMode,
    WorkspaceRuntimeMode,
    WorkspaceWizardConfig,
)
from app.schemas.k8s import DeployMode
from app.services.cloud_promote import (
    apply_primary_service_selection,
    build_cloud_running_instance,
    cloud_config_for_promote,
    needs_cloud_retarget,
    promote_cloud_deploy_mode,
    promote_runtime_target,
    recommend_primary_service,
    resolve_attach_running_instance,
)


def test_needs_cloud_retarget_local_and_attach_compose() -> None:
    assert needs_cloud_retarget(source_provider="local", deploy_mode=DeployMode.ATTACH.value)
    assert needs_cloud_retarget(source_provider="local", deploy_mode=DeployMode.COMPOSE.value)
    assert needs_cloud_retarget(source_provider="local", deploy_mode=DeployMode.MANIFEST.value)
    assert not needs_cloud_retarget(source_provider="gcp", deploy_mode=DeployMode.PREVIEW.value)
    assert not needs_cloud_retarget(source_provider="gcp", deploy_mode=DeployMode.MANIFEST.value)
    assert needs_cloud_retarget(source_provider="gcp", deploy_mode=DeployMode.ATTACH.value)


def test_promote_cloud_deploy_mode_keeps_kubernetes() -> None:
    assert promote_cloud_deploy_mode(
        DeployMode.MANIFEST.value, retarget=True
    ) == DeployMode.MANIFEST
    assert promote_cloud_deploy_mode(
        DeployMode.PREVIEW.value, retarget=True
    ) == DeployMode.PREVIEW
    assert promote_cloud_deploy_mode(
        DeployMode.ATTACH.value, retarget=True
    ) == DeployMode.ATTACH
    assert promote_cloud_deploy_mode(
        DeployMode.COMPOSE.value, retarget=True
    ) == DeployMode.ATTACH


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


def test_recommend_primary_service_prefers_website_over_dashboard() -> None:
    services = [
        ContainerServiceSpec(name="dashboard", app_kind="frontend"),
        ContainerServiceSpec(name="website", app_kind="frontend"),
        ContainerServiceSpec(name="api", app_kind="backend"),
    ]
    assert recommend_primary_service(services) == "website"


def test_apply_primary_service_selection_exposes_only_primary() -> None:
    services = [
        ContainerServiceSpec(name="api", app_kind="backend"),
        ContainerServiceSpec(name="web", app_kind="frontend"),
    ]
    updated = apply_primary_service_selection(services, "api")
    by_name = {s.name: s for s in updated}
    assert by_name["api"].expose_preview is True
    assert by_name["web"].expose_preview is False


def test_apply_primary_service_selection_clears_prior_true() -> None:
    services = [
        ContainerServiceSpec(name="dashboard", app_kind="frontend", expose_preview=True),
        ContainerServiceSpec(name="web", app_kind="frontend", expose_preview=True),
    ]
    updated = apply_primary_service_selection(services, "web")
    by_name = {s.name: s for s in updated}
    assert by_name["web"].expose_preview is True
    assert by_name["dashboard"].expose_preview is False


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


def test_build_cloud_promote_wizard_request_keeps_kubernetes() -> None:
    from app.schemas.cloud import KubernetesPackaging
    from app.services.cloud_promote import build_cloud_promote_wizard_request

    source = WorkspaceWizardConfig.model_validate(
        {
            "name": "src",
            "iac_engine": "terraform",
            "cloud": {"provider": "local", "resources": {}},
            "credentials": {},
            "has_credentials": False,
            "runtime_mode": WorkspaceRuntimeMode.KUBERNETES.value,
            "artifact_mode": "manifest_only",
            "kubernetes_packaging": KubernetesPackaging.RAW_MANIFESTS.value,
        }
    )
    req = build_cloud_promote_wizard_request(
        source,
        workspace_name="demo-gcp",
        provider=CloudProvider.GCP,
        credentials=CloudCredentials(gcp_sa_key_json='{"project_id":"demo-proj"}'),
    )
    assert req.runtime_mode == WorkspaceRuntimeMode.KUBERNETES
    assert req.kubernetes_packaging == KubernetesPackaging.RAW_MANIFESTS
    assert req.container_scaffold.enabled is False
    assert req.container_scaffold.generate_dockerfile is False
    assert isinstance(req.cloud, GcpCloudConfig)
    assert req.cloud.resources.gke is True
    assert req.cloud.resources.cloud_run is False


def test_workspace_has_imported_app_artifacts_detects_launch_web(tmp_path) -> None:
    from app.services.iac_generator import workspace_has_imported_app_artifacts

    root = tmp_path / "ws"
    (root / ".launchpad").mkdir(parents=True)
    (root / ".launchpad" / "image-builds.json").write_text(
        '[{"service":"launch-web","image":"launch-web:latest","context":".","dockerfile":"Dockerfile"}]\n',
        encoding="utf-8",
    )
    (root / "Dockerfile").write_text("FROM node:22\n", encoding="utf-8")
    (root / "package.json").write_text('{"name":"web"}\n', encoding="utf-8")
    assert workspace_has_imported_app_artifacts(root) is True


def test_regenerate_preserves_imported_app_package_json(tmp_path) -> None:
    from app.schemas.cloud import (
        CloudCredentials,
        GcpCloudConfig,
        GcpResources,
        IaCEngine,
        ProvisioningWizardRequest,
    )
    from app.services.iac_generator import IaCGenerator

    root = tmp_path / "imported"
    (root / ".launchpad").mkdir(parents=True)
    (root / "infra" / "k8s" / "manifests").mkdir(parents=True)
    (root / ".launchpad" / "image-builds.json").write_text(
        '[{"service":"launch-web","image":"launch-web:latest","context":".","dockerfile":"Dockerfile"}]\n',
        encoding="utf-8",
    )
    (root / "Dockerfile").write_text("FROM node:22-alpine\nCMD [\"node\"]\n", encoding="utf-8")
    (root / "package.json").write_text('{"name":"coderefine-website"}\n', encoding="utf-8")
    (root / "nuxt.config.ts").write_text("export default {}\n", encoding="utf-8")
    (root / "infra" / "k8s" / "manifests" / "launch-web-deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: launch-web\n",
        encoding="utf-8",
    )

    req = ProvisioningWizardRequest(
        name="coderefine-website-gcp",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=GcpCloudConfig(
            resources=GcpResources(
                gke=True,
                artifact_registry=True,
                region="europe-west3",
                project_id="demo",
            )
        ),
        credentials=CloudCredentials(gcp_sa_key_json='{"project_id":"demo"}'),
        runtime_mode=WorkspaceRuntimeMode.KUBERNETES,
        artifact_mode=WorkspaceArtifactsMode.BOTH,
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
        container_scaffold=ContainerScaffoldConfig(
            enabled=True,
            generate_dockerfile=True,
            app_name="app",
            stack="node",
            services=[],
        ),
    )
    IaCGenerator(workspace_root=tmp_path).regenerate(root, req)

    assert (root / "package.json").is_file()
    assert (root / "package.json").read_text(encoding="utf-8").find("coderefine-website") >= 0
    assert (root / "infra" / "k8s" / "manifests" / "launch-web-deployment.yaml").is_file()
    assert (root / ".launchpad" / "image-builds.json").read_text(encoding="utf-8").find(
        "launch-web"
    ) >= 0
    assert not (root / "apps" / "app" / "server.js").exists()

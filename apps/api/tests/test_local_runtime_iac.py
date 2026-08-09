"""Local Compose / running-instance IaC stubs."""

from __future__ import annotations

from pathlib import Path

from app.schemas.cloud import (
    CloudCredentials,
    ContainerScaffoldConfig,
    IaCEngine,
    LocalCloudConfig,
    LocalResources,
    ProvisioningWizardRequest,
    RunningInstanceConfig,
    RunningInstanceKind,
    WorkspaceArtifactsMode,
    WorkspaceRuntimeMode,
)
from app.services.iac_generator import IaCGenerator
from app.services.local_runtime_iac import write_local_runtime_iac


def test_write_local_runtime_terraform(tmp_path: Path) -> None:
    files = write_local_runtime_iac(
        tmp_path,
        name="compose-demo",
        engine=IaCEngine.TERRAFORM,
        runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
    )
    assert "infra/terraform/main.tf" in files
    assert (tmp_path / "infra" / "terraform" / "providers.tf").is_file()
    body = (tmp_path / "infra" / "terraform" / "main.tf").read_text(encoding="utf-8")
    assert "null_resource" in body
    assert "docker_compose" in (tmp_path / "infra" / "terraform" / "terraform.tfvars").read_text(
        encoding="utf-8"
    )


def test_local_compose_multi_service_writes_apps_and_compose_contexts(tmp_path: Path) -> None:
    """Frontend + backend services must each get CoreScaffold sources and compose
    build contexts under apps/<slug>/ (not repo-root context with bare Dockerfiles)."""
    from app.schemas.cloud import ContainerServiceSpec

    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="compose-multi",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
        artifact_mode=WorkspaceArtifactsMode.IAC_ONLY,
        container_scaffold=ContainerScaffoldConfig(
            enabled=True,
            generate_dockerfile=True,
            generate_docker_compose=True,
            services=[
                ContainerServiceSpec(
                    name="web-ui",
                    stack="nextjs",
                    app_kind="frontend",
                    listen_port=3000,
                ),
                ContainerServiceSpec(
                    name="api-server",
                    stack="node",
                    app_kind="backend",
                    listen_port=8080,
                ),
            ],
        ),
    )
    bundle = gen.generate(request)
    root = Path(bundle.root_dir)

    assert (root / "apps/web-ui/package.json").is_file()
    assert (root / "apps/web-ui/Dockerfile").is_file()
    assert (root / "apps/api-server/package.json").is_file()
    assert (root / "apps/api-server/Dockerfile").is_file()

    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "web-ui:" in compose
    assert "api-server:" in compose
    assert "context: apps/web-ui" in compose
    assert "context: apps/api-server" in compose
    assert "context: ." not in compose
    assert "launchpad.io/preview-target=true" in compose
    assert "API_URL=http://api-server:" in compose
    assert "preview_target: true" in compose
    # Frontend publishes a host port; backend stays on the compose network.
    assert "ports:" in compose
    assert "expose:" in compose
    assert not (root / "infra" / "k8s").exists()


def test_local_instance_multi_service_plan_stays_attach() -> None:
    from app.schemas.cloud import WorkspaceWizardConfig
    from app.schemas.k8s import DeployMode
    from app.services.preview_deploy_plan import resolve_preview_deploy_plan

    config = WorkspaceWizardConfig.model_validate(
        {
            "name": "inst-multi",
            "iac_engine": "terraform",
            "cloud": {"provider": "local", "resources": {}},
            "credentials": {},
            "has_credentials": False,
            "runtime_mode": "running_instance",
            "running_instance": {"kind": "local_machine"},
            "container_scaffold": {
                "enabled": True,
                "generate_dockerfile": True,
                "generate_docker_compose": False,
                "services": [
                    {
                        "name": "web-ui",
                        "stack": "nextjs",
                        "app_kind": "frontend",
                        "listen_port": 3000,
                    },
                    {
                        "name": "api-server",
                        "stack": "node",
                        "app_kind": "backend",
                        "listen_port": 8080,
                    },
                ],
            },
        }
    )
    plan = resolve_preview_deploy_plan(config)
    assert plan.deploy_mode == DeployMode.ATTACH


def test_local_instance_workspace_writes_pulumi_stub(tmp_path: Path) -> None:
    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="instance-iac",
        iac_engine=IaCEngine.PULUMI,
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
        running_instance=RunningInstanceConfig(kind=RunningInstanceKind.LOCAL_MACHINE),
        artifact_mode=WorkspaceArtifactsMode.IAC_ONLY,
        container_scaffold=ContainerScaffoldConfig(
            enabled=True,
            generate_dockerfile=True,
            generate_docker_compose=False,
        ),
    )
    bundle = gen.generate(request)
    root = Path(bundle.root_dir)
    assert (root / "Pulumi.yaml").is_file()
    assert (root / "index.ts").is_file()
    assert not (root / "infra" / "k8s").exists()

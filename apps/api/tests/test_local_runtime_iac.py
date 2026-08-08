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


def test_local_compose_workspace_writes_iac_when_enabled(tmp_path: Path) -> None:
    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="compose-iac",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
        artifact_mode=WorkspaceArtifactsMode.IAC_ONLY,
        container_scaffold=ContainerScaffoldConfig(
            enabled=True,
            generate_dockerfile=True,
            generate_docker_compose=True,
        ),
    )
    bundle = gen.generate(request)
    root = Path(bundle.root_dir)
    assert (root / "docker-compose.yml").is_file() or any(
        "docker-compose" in f for f in bundle.files
    )
    assert (root / "infra" / "terraform" / "main.tf").is_file()
    assert not (root / "infra" / "k8s").exists()


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

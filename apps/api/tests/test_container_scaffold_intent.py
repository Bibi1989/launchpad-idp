"""Linked workspaces must not scaffold a phantom apps/ dir (only real intent does)."""

from __future__ import annotations

from app.schemas.cloud import (
    CloudCredentials,
    ContainerScaffoldConfig,
    ContainerServiceSpec,
    IaCEngine,
    LocalCloudConfig,
    LocalResources,
    ProvisioningWizardRequest,
)
from app.services.iac_generator import _has_container_scaffold_intent


def _request(scaffold: ContainerScaffoldConfig) -> ProvisioningWizardRequest:
    return ProvisioningWizardRequest(
        name="scaffold-test",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources(cluster_name="launchpad")),
        credentials=CloudCredentials(),
        container_scaffold=scaffold,
    )


def test_disabled_scaffold_has_no_intent() -> None:
    assert not _has_container_scaffold_intent(_request(ContainerScaffoldConfig(enabled=False)))


def test_linked_workspace_has_no_intent() -> None:
    # Link mode: card enabled for the UI, but services cleared + generate flags off.
    linked = ContainerScaffoldConfig(
        enabled=True,
        generate_dockerfile=False,
        generate_docker_compose=False,
        frameworks=[],
        services=[],
    )
    assert not _has_container_scaffold_intent(_request(linked))


def test_services_workspace_has_intent() -> None:
    svc = ContainerScaffoldConfig(
        enabled=True,
        services=[ContainerServiceSpec(name="web-ui", listen_port=3000)],
    )
    assert _has_container_scaffold_intent(_request(svc))


def test_generate_flag_alone_has_intent() -> None:
    # Single-app "create services" flow: generate flag on, no explicit services.
    single = ContainerScaffoldConfig(enabled=True, generate_dockerfile=True, services=[])
    assert _has_container_scaffold_intent(_request(single))

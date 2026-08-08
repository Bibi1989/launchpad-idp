"""Attach to an existing runtime (kube context, endpoint, or serverless URL)."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.cloud import (
    KubernetesPackaging,
    RunningInstanceConfig,
    RunningInstanceKind,
)
from app.services.kubernetes import KubernetesProvisioner, ProvisionedResources

logger = get_logger(__name__)


class AttachDeployError(RuntimeError):
    """Running-instance attach failed."""


def deploy_attach(
    *,
    namespace: str,
    environment_id: str,
    name: str,
    git_branch: str,
    git_repo_url: str,
    ttl_expires_at: str,
    owner_label: str = "launchpad",
    image: str | None = None,
    enable_postgres: bool = False,
    enable_redis: bool = False,
    running_instance: RunningInstanceConfig,
    workspace_root: Path | None = None,
    packaging: KubernetesPackaging | None = None,
    settings: Settings | None = None,
) -> ProvisionedResources:
    """Attach a preview to an existing runtime without creating long-lived VMs."""
    cfg = settings or get_settings()
    kind = running_instance.kind

    if kind == RunningInstanceKind.ENDPOINT:
        url = (running_instance.endpoint_url or "").strip()
        if not url:
            raise AttachDeployError("endpoint_url is required for endpoint attach")
        return ProvisionedResources(
            namespace=namespace,
            preview_url=url,
            created_workload=True,
            image=image,
            labels={
                "launchpad.io/environment-id": environment_id,
                "launchpad.io/deploy-mode": "attach",
                "launchpad.io/attach-kind": kind.value,
            },
        )

    if kind == RunningInstanceKind.SERVERLESS:
        url = (running_instance.endpoint_url or "").strip()
        if not url:
            # No Cloud Run / Container Apps deployer in this phase: require an
            # existing service URL for a secure attach.
            raise AttachDeployError(
                "Serverless attach requires endpoint_url (existing Cloud Run / "
                "Container Apps URL). Provision the service first, then attach."
            )
        return ProvisionedResources(
            namespace=namespace,
            preview_url=url,
            created_workload=True,
            image=image,
            labels={
                "launchpad.io/environment-id": environment_id,
                "launchpad.io/deploy-mode": "attach",
                "launchpad.io/attach-kind": kind.value,
            },
        )

    # kube_context
    context = (running_instance.kube_context or "").strip()
    if not context:
        raise AttachDeployError("kube_context is required for kube attach")

    attach_settings = cfg.model_copy(
        update={
            "kubernetes_context": context,
            "kubernetes_enabled": True,
        }
    )
    provisioner = KubernetesProvisioner(attach_settings)

    if (
        workspace_root is not None
        and packaging
        in {KubernetesPackaging.RAW_MANIFESTS, KubernetesPackaging.HELM}
    ):
        from app.services.manifest_deploy import ManifestDeployer

        logger.info(
            "attach_manifest_deploy",
            environment_id=environment_id,
            context=context,
            packaging=packaging.value,
        )
        deployer = ManifestDeployer(attach_settings, provisioner)
        resources = deployer.deploy(
            workspace_root=workspace_root,
            namespace=namespace,
            environment_id=environment_id,
            name=name,
            git_branch=git_branch,
            git_repo_url=git_repo_url,
            ttl_expires_at=ttl_expires_at,
            owner_label=owner_label,
            image=image,
        )
    else:
        logger.info(
            "attach_preview_provision",
            environment_id=environment_id,
            context=context,
        )
        resources = provisioner.provision(
            namespace=namespace,
            environment_id=environment_id,
            name=name,
            git_branch=git_branch,
            git_repo_url=git_repo_url,
            ttl_expires_at=ttl_expires_at,
            owner_label=owner_label,
            image=image,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
        )

    resources.labels = {
        **(resources.labels or {}),
        "launchpad.io/deploy-mode": "attach",
        "launchpad.io/attach-kind": kind.value,
        "launchpad.io/attach-context": context,
    }
    return resources


def teardown_attach(
    *,
    running_instance: RunningInstanceConfig | None,
    namespace: str,
    settings: Settings | None = None,
) -> None:
    """Tear down attach resources. Endpoint/serverless are no-ops (external)."""
    cfg = settings or get_settings()
    if running_instance is None:
        return
    if running_instance.kind in {
        RunningInstanceKind.ENDPOINT,
        RunningInstanceKind.SERVERLESS,
    }:
        logger.info(
            "attach_teardown_noop",
            kind=running_instance.kind.value,
            namespace=namespace,
        )
        return

    context = (running_instance.kube_context or "").strip()
    attach_settings = cfg.model_copy(
        update={
            "kubernetes_context": context or cfg.kubernetes_context,
            "kubernetes_enabled": True,
        }
    )
    provisioner = KubernetesProvisioner(attach_settings)
    provisioner.teardown(namespace)

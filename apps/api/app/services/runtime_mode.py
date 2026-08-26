"""Workspace runtime-mode matrix: legal (provider, mode) pairs and artifact normalization."""

from __future__ import annotations

from app.schemas.cloud import (
    AnsibleConfig,
    AwsCloudConfig,
    AzureCloudConfig,
    CloudConfig,
    ContainerScaffoldConfig,
    GcpCloudConfig,
    InstanceProcessStrategy,
    KubernetesPackaging,
    LocalCloudConfig,
    RunningInstanceConfig,
    RunningInstanceKind,
    WorkspaceArtifactsMode,
    WorkspaceRuntimeMode,
)


class RuntimeModeViolation(ValueError):
    """Raised when a provider/runtime_mode combination is not allowed."""


def is_compose_allowed(cloud: CloudConfig) -> bool:
    """Compose previews are local-only (no remoted Docker socket)."""
    return isinstance(cloud, LocalCloudConfig)


def has_serverless_runtime(cloud: CloudConfig) -> bool:
    """Managed container platforms (Cloud Run, App Runner, Container Apps)."""
    if isinstance(cloud, GcpCloudConfig):
        return cloud.resources.cloud_run
    if isinstance(cloud, AwsCloudConfig):
        return cloud.resources.app_runner
    if isinstance(cloud, AzureCloudConfig):
        return cloud.resources.container_apps
    return False


def has_managed_kubernetes(cloud: CloudConfig) -> bool:
    if isinstance(cloud, LocalCloudConfig):
        return True
    if isinstance(cloud, GcpCloudConfig):
        return cloud.resources.gke
    if isinstance(cloud, AwsCloudConfig):
        return cloud.resources.eks
    if isinstance(cloud, AzureCloudConfig):
        return cloud.resources.aks
    return False


def has_vm_hint(cloud: CloudConfig) -> bool:
    """True when cloud resources suggest a VM target (GCE / EC2)."""
    if isinstance(cloud, AwsCloudConfig):
        return cloud.resources.ec2
    if isinstance(cloud, GcpCloudConfig):
        return cloud.resources.compute_instance
    return False


def wants_ansible_config(
    ansible: AnsibleConfig,
    *,
    iac_engine: str | None = None,
    config_tool: str | None = None,
) -> bool:
    """True when the user chose Ansible as IaC engine or VM configuration tool."""
    if ansible.enabled:
        return True
    if (iac_engine or "").strip().lower() == "ansible":
        return True
    return (config_tool or "cloud-init").strip().lower() == "ansible"


def ensure_ansible_for_vm_runtime(
    ansible: AnsibleConfig,
    *,
    runtime_mode: WorkspaceRuntimeMode,
    running_instance: RunningInstanceConfig | None,
    iac_engine: str | None = None,
    config_tool: str | None = None,
) -> AnsibleConfig:
    """Enable ``infra/ansible`` only when Ansible is the chosen VM config tool.

    Default instance configuration is LaunchConfig (cloud-init / startup script).
    Terraform/Pulumi still create the VM; Ansible is opt-in.
    """
    if not wants_ansible_config(
        ansible,
        iac_engine=iac_engine,
        config_tool=config_tool,
    ):
        return ansible
    if runtime_mode != WorkspaceRuntimeMode.RUNNING_INSTANCE:
        if (iac_engine or "").strip().lower() == "ansible" and not ansible.enabled:
            return ansible.model_copy(update={"enabled": True})
        return ansible
    instance = running_instance or RunningInstanceConfig()
    if instance.kind != RunningInstanceKind.VM:
        return ansible
    updates: dict[str, object] = {"enabled": True}
    if instance.listen_port:
        updates["app_listen_port"] = int(instance.listen_port)
    if instance.ssh_user:
        updates["ssh_user"] = instance.ssh_user
    if instance.ssh_port:
        updates["ssh_port"] = int(instance.ssh_port)
    if instance.ssh_key_path:
        updates["ssh_private_key_path"] = instance.ssh_key_path
    if instance.host:
        updates["hosts"] = instance.host
    if instance.process_strategy == InstanceProcessStrategy.DOCKER:
        updates["install_docker"] = True
    return ansible.model_copy(update=updates)


def ensure_autocreate_vm_resources(
    cloud: CloudConfig,
    *,
    runtime_mode: WorkspaceRuntimeMode,
    running_instance: RunningInstanceConfig | None,
) -> CloudConfig:
    """Enable GCE/EC2 IaC when running_instance VM has no BYO host.

    Provision UI historically left ``compute_instance`` false for GCP VMs
    (``gcp_vm_ssh`` had no resourceKey). Without this, Pulumi/Terraform apply
    succeeds with only VPC/secrets and never yields a preview URL.
    """
    if runtime_mode != WorkspaceRuntimeMode.RUNNING_INSTANCE:
        return cloud
    instance = running_instance or RunningInstanceConfig()
    if instance.kind != RunningInstanceKind.VM:
        return cloud
    if (instance.host or "").strip() or (instance.preview_url_override or "").strip():
        return cloud

    if isinstance(cloud, GcpCloudConfig):
        updates: dict[str, object] = {}
        if not cloud.resources.compute_instance:
            updates["compute_instance"] = True
        # Prefer an explicit VPC/subnet when already selected; otherwise default
        # network is fine for a single ephemeral VM.
        if updates:
            return cloud.model_copy(
                update={"resources": cloud.resources.model_copy(update=updates)}
            )
        return cloud

    if isinstance(cloud, AwsCloudConfig):
        if not cloud.resources.ec2:
            return cloud.model_copy(
                update={
                    "resources": cloud.resources.model_copy(update={"ec2": True})
                }
            )
        return cloud

    return cloud


def validate_runtime_mode(
    cloud: CloudConfig,
    runtime_mode: WorkspaceRuntimeMode,
    running_instance: RunningInstanceConfig | None = None,
) -> None:
    """Enforce the secure / cost-efficient mode matrix."""
    if runtime_mode == WorkspaceRuntimeMode.DOCKER_COMPOSE:
        if not is_compose_allowed(cloud):
            raise RuntimeModeViolation(
                "Docker Compose runtime is local-only (remote Docker sockets are not supported)"
            )
        return

    if runtime_mode == WorkspaceRuntimeMode.KUBERNETES:
        return

    if runtime_mode == WorkspaceRuntimeMode.RUNNING_INSTANCE:
        cfg = running_instance or RunningInstanceConfig()
        kind = cfg.kind

        if kind == RunningInstanceKind.SERVERLESS:
            if isinstance(cloud, LocalCloudConfig):
                raise RuntimeModeViolation(
                    "Serverless compute requires Cloud Run, App Runner, or Container Apps"
                )
            if not has_serverless_runtime(cloud):
                raise RuntimeModeViolation(
                    "Enable Cloud Run (GCP), App Runner (AWS), or Container Apps (Azure) "
                    "for serverless container compute"
                )
            return

        if kind == RunningInstanceKind.VM:
            has_target = (
                (cfg.host or "").strip()
                or (cfg.preview_url_override or "").strip()
                or (cfg.service_name or "").strip()
            )
            # A host is only mandatory when nothing can supply one automatically:
            #  - local provider -> one-click preview falls back to local Docker
            #  - GCP / AWS      -> the VM (and its public IP) are auto-created at deploy
            # Azure auto-provisioning is not implemented yet, so it still needs a host.
            can_autocreate = isinstance(
                cloud, (LocalCloudConfig, GcpCloudConfig, AwsCloudConfig)
            )
            if not has_target and not can_autocreate:
                raise RuntimeModeViolation(
                    "VM compute needs a host (IP or hostname), a preview URL override, "
                    "or a cloud instance name. GCP/AWS create the VM for you; Azure "
                    "auto-provisioning is not available yet, so set a host for Azure."
                )
            return

        if kind == RunningInstanceKind.LOCAL_MACHINE:
            if not isinstance(cloud, LocalCloudConfig) and not (cfg.host or "").strip():
                # Allow local_machine only for local provider (operator Docker).
                raise RuntimeModeViolation(
                    "local_machine compute is only available for the local provider "
                    "(use vm for EC2/VPS, or serverless for Cloud Run)"
                )
            return

        raise RuntimeModeViolation(f"Unknown running instance kind: {kind}")

    raise RuntimeModeViolation(f"Unknown runtime_mode: {runtime_mode}")


def normalize_artifacts_for_runtime_mode(
    *,
    cloud: CloudConfig,
    runtime_mode: WorkspaceRuntimeMode,
    artifact_mode: WorkspaceArtifactsMode,
    kubernetes_packaging: KubernetesPackaging,
    container_scaffold: ContainerScaffoldConfig,
    running_instance: RunningInstanceConfig | None = None,
) -> tuple[
    WorkspaceArtifactsMode,
    KubernetesPackaging,
    ContainerScaffoldConfig,
    RunningInstanceConfig,
]:
    """One runtime winner: Compose and instance modes do not scaffold unused K8s packaging."""
    instance = running_instance or RunningInstanceConfig()

    if runtime_mode == WorkspaceRuntimeMode.DOCKER_COMPOSE:
        scaffold = container_scaffold.model_copy(
            update={
                "enabled": True,
                "generate_dockerfile": True,
                "generate_docker_compose": True,
            }
        )
        return (
            WorkspaceArtifactsMode.IAC_ONLY,
            KubernetesPackaging.NONE,
            scaffold,
            instance,
        )

    if runtime_mode == WorkspaceRuntimeMode.RUNNING_INSTANCE:
        # Prefer serverless when Cloud Run / Container Apps is enabled.
        if (
            has_serverless_runtime(cloud)
            and instance.kind == RunningInstanceKind.LOCAL_MACHINE
        ):
            instance = instance.model_copy(update={"kind": RunningInstanceKind.SERVERLESS})
        elif (
            isinstance(cloud, LocalCloudConfig)
            and instance.kind == RunningInstanceKind.SERVERLESS
        ):
            instance = instance.model_copy(update={"kind": RunningInstanceKind.LOCAL_MACHINE})
        elif (
            has_vm_hint(cloud)
            and instance.kind == RunningInstanceKind.LOCAL_MACHINE
            and not isinstance(cloud, LocalCloudConfig)
        ):
            instance = instance.model_copy(update={"kind": RunningInstanceKind.VM})

        scaffold = container_scaffold
        if not scaffold.enabled:
            scaffold = scaffold.model_copy(
                update={
                    "enabled": True,
                    "generate_dockerfile": True,
                    "generate_docker_compose": False,
                }
            )
        # Keep running_instance.listen_port as the user-chosen host publish port.
        # Container port comes from Dockerfile EXPOSE / service scaffold at attach time.
        return (
            WorkspaceArtifactsMode.IAC_ONLY
            if artifact_mode == WorkspaceArtifactsMode.MANIFEST_ONLY
            else artifact_mode,
            KubernetesPackaging.NONE,
            scaffold,
            instance,
        )

    # kubernetes (default)
    if isinstance(cloud, LocalCloudConfig):
        packaging = (
            kubernetes_packaging
            if kubernetes_packaging != KubernetesPackaging.NONE
            else KubernetesPackaging.RAW_MANIFESTS
        )
        return WorkspaceArtifactsMode.MANIFEST_ONLY, packaging, container_scaffold, instance

    return artifact_mode, kubernetes_packaging, container_scaffold, instance


def coerce_wizard_snapshot(raw: dict[str, object]) -> dict[str, object]:
    """Backfill runtime_mode for workspaces created before Phase 0."""
    out = dict(raw)
    if "runtime_mode" not in out or not out.get("runtime_mode"):
        out["runtime_mode"] = WorkspaceRuntimeMode.KUBERNETES.value
    ri = out.get("running_instance")
    if not isinstance(ri, dict):
        out["running_instance"] = RunningInstanceConfig().model_dump(mode="json")
    else:
        kind = ri.get("kind")
        if kind == "kube_context":
            ri = {**ri, "kind": RunningInstanceKind.LOCAL_MACHINE.value}
        elif kind == "endpoint":
            ri = {
                **ri,
                "kind": RunningInstanceKind.VM.value,
                "preview_url_override": ri.get("preview_url_override") or ri.get("endpoint_url"),
            }
        out["running_instance"] = ri
    return out


def default_runtime_mode_for_provider(cloud: CloudConfig) -> WorkspaceRuntimeMode:
    """Historical workspaces and new providers default to Kubernetes."""
    _ = cloud
    return WorkspaceRuntimeMode.KUBERNETES

"""Workspace runtime-mode matrix: legal (provider, mode) pairs and artifact normalization."""

from __future__ import annotations

from app.schemas.cloud import (
    AwsCloudConfig,
    AzureCloudConfig,
    CloudConfig,
    ContainerScaffoldConfig,
    GcpCloudConfig,
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
    if isinstance(cloud, GcpCloudConfig):
        return cloud.resources.cloud_run
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
        if isinstance(cloud, LocalCloudConfig):
            return
        if not has_managed_kubernetes(cloud) and not has_serverless_runtime(cloud):
            # Cloud kubernetes mode still allows IaC-only workspaces without a cluster;
            # packaging validators enforce cluster when manifests are requested.
            return
        return

    if runtime_mode == WorkspaceRuntimeMode.RUNNING_INSTANCE:
        if isinstance(cloud, LocalCloudConfig):
            cfg = running_instance or RunningInstanceConfig()
            if cfg.kind == RunningInstanceKind.SERVERLESS:
                raise RuntimeModeViolation(
                    "Serverless attach is only available for GCP Cloud Run or Azure Container Apps"
                )
            if cfg.kind == RunningInstanceKind.ENDPOINT and not (cfg.endpoint_url or "").strip():
                raise RuntimeModeViolation("Running instance endpoint_url is required")
            if cfg.kind == RunningInstanceKind.KUBE_CONTEXT and not (cfg.kube_context or "").strip():
                raise RuntimeModeViolation("Running instance kube_context is required")
            return

        if has_serverless_runtime(cloud):
            return

        cfg = running_instance or RunningInstanceConfig()
        if cfg.kind == RunningInstanceKind.ENDPOINT and not (cfg.endpoint_url or "").strip():
            raise RuntimeModeViolation("Running instance endpoint_url is required")
        if cfg.kind == RunningInstanceKind.KUBE_CONTEXT and not (cfg.kube_context or "").strip():
            raise RuntimeModeViolation(
                "Provide a kube_context to attach, or enable Cloud Run / Container Apps"
            )
        return

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
    """One runtime winner: Compose and attach do not scaffold unused K8s packaging."""
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
            WorkspaceArtifactsMode.IAC_ONLY
            if not isinstance(cloud, LocalCloudConfig)
            else WorkspaceArtifactsMode.IAC_ONLY,
            KubernetesPackaging.NONE,
            scaffold,
            instance,
        )

    if runtime_mode == WorkspaceRuntimeMode.RUNNING_INSTANCE:
        if has_serverless_runtime(cloud) and instance.kind == RunningInstanceKind.KUBE_CONTEXT:
            instance = instance.model_copy(update={"kind": RunningInstanceKind.SERVERLESS})
        return (
            WorkspaceArtifactsMode.IAC_ONLY
            if artifact_mode == WorkspaceArtifactsMode.MANIFEST_ONLY
            else artifact_mode,
            KubernetesPackaging.NONE,
            container_scaffold,
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
    if "running_instance" not in out or not isinstance(out.get("running_instance"), dict):
        out["running_instance"] = RunningInstanceConfig().model_dump(mode="json")
    return out


def default_runtime_mode_for_provider(cloud: CloudConfig) -> WorkspaceRuntimeMode:
    """Historical workspaces and new providers default to Kubernetes."""
    _ = cloud
    return WorkspaceRuntimeMode.KUBERNETES

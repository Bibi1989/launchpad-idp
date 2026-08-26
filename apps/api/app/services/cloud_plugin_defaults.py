"""Apply cloud plugin selection to wizard requests (mirrors web applyPluginServiceDefaults)."""

from __future__ import annotations

from app.providers.service_plugins import split_plugin_id
from app.schemas.cloud import (
    AwsCloudConfig,
    AzureCloudConfig,
    CloudConfig,
    CloudPluginTarget,
    CloudProvider,
    GcpCloudConfig,
    KubernetesImageSource,
    ProvisioningWizardRequest,
    RunningInstanceConfig,
    WorkspaceRuntimeMode,
)
from app.services.cloud_region import _coerce_region
from app.services.runtime_mode import ensure_autocreate_vm_resources


def _service_id(plugin: CloudPluginTarget, parent: str) -> str:
    explicit = (plugin.service or "").strip().lower()
    if explicit:
        return explicit
    pid = (plugin.provider or "").strip().lower()
    prefix = f"{parent}-"
    if pid.startswith(prefix):
        return pid[len(prefix) :]
    return pid


def apply_cloud_plugin_defaults(
    request: ProvisioningWizardRequest,
    plugin: CloudPluginTarget | None,
) -> ProvisioningWizardRequest:
    """Merge plugin region/service flags into a wizard request (non-destructive copy)."""
    if plugin is None or not (plugin.provider or "").strip():
        return request

    parent, _service = split_plugin_id(plugin.provider or "")
    service = _service or _service_id(plugin, parent)
    external_image = (
        request.kubernetes_options.image_source == KubernetesImageSource.EXTERNAL
    )

    cloud = request.cloud
    runtime_mode = request.runtime_mode
    running_instance = request.running_instance

    region_raw = (plugin.region or "").strip()
    region = _coerce_region(parent, region_raw) if region_raw else None

    if parent == CloudProvider.GCP.value and isinstance(cloud, GcpCloudConfig):
        resources = cloud.resources.model_copy(
            update={
                "vpc": True,
                "subnets": True,
                "gke": service == "gke",
                "cloud_run": service == "cloud-run",
                "compute_instance": service in {"gce", "gce-docker"},
                "artifact_registry": (service in {"gke", "cloud-run"}) and not external_image,
                **({"region": region} if region else {}),
            }
        )
        cloud = cloud.model_copy(update={"resources": resources})
        if resources.compute_instance or resources.cloud_run:
            runtime_mode = WorkspaceRuntimeMode.RUNNING_INSTANCE
        elif resources.gke:
            runtime_mode = WorkspaceRuntimeMode.KUBERNETES
    elif parent == CloudProvider.AWS.value and isinstance(cloud, AwsCloudConfig):
        resources = cloud.resources.model_copy(
            update={
                "vpc": True,
                "subnets": True,
                "eks": service == "eks",
                "ec2": service in {"ec2", "ec2-docker"},
                "app_runner": service == "ecs-fargate",
                "ecr": (service in {"eks", "ecs-fargate"}) and not external_image,
                **({"region": region} if region else {}),
            }
        )
        cloud = cloud.model_copy(update={"resources": resources})
        if resources.eks:
            runtime_mode = WorkspaceRuntimeMode.KUBERNETES
        elif resources.ec2 or resources.app_runner:
            runtime_mode = WorkspaceRuntimeMode.RUNNING_INSTANCE
    elif parent == CloudProvider.AZURE.value and isinstance(cloud, AzureCloudConfig):
        resources = cloud.resources.model_copy(
            update={
                "vnet": True,
                "subnets": True,
                "aks": service == "aks",
                "container_apps": service in {"container-apps", "aci"},
                "acr": (service in {"aks", "container-apps", "aci"}) and not external_image,
                **({"location": region} if region else {}),
            }
        )
        cloud = cloud.model_copy(update={"resources": resources})
        if resources.aks:
            runtime_mode = WorkspaceRuntimeMode.KUBERNETES
        elif resources.container_apps:
            runtime_mode = WorkspaceRuntimeMode.RUNNING_INSTANCE

    if region and runtime_mode == WorkspaceRuntimeMode.RUNNING_INSTANCE:
        running_instance = running_instance.model_copy(update={"region": region})

    cloud = ensure_autocreate_vm_resources(
        cloud,
        runtime_mode=runtime_mode,
        running_instance=running_instance,
    )

    return request.model_copy(
        update={
            "cloud": cloud,
            "runtime_mode": runtime_mode,
            "running_instance": running_instance,
            "cloud_plugin": plugin,
        }
    )


def plugin_implies_vm(plugin: CloudPluginTarget | None) -> bool:
    if plugin is None or not (plugin.provider or "").strip():
        return False
    parent, service = split_plugin_id(plugin.provider or "")
    svc = service or _service_id(plugin, parent)
    return svc in {
        "gce",
        "gce-docker",
        "ec2",
        "ec2-docker",
        "droplet",
        "droplet-docker",
        "linode-instance",
        "cloud-server",
        "azure-vm",
        "vm-docker",
    }

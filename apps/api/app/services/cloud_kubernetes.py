"""Connect Kubernetes previews to GKE/EKS/AKS instead of the local kind/k3d cluster."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger
from app.schemas.cloud import CloudCredentials, CloudProvider
from app.schemas.k8s import DeployMode
from app.services.cloud_instance_compute import (
    CloudInstanceComputeError,
    _credential_env,
    _run_cmd,
    resolve_gcp_project_id,
)

logger = get_logger(__name__)

SHARED_PREVIEW_CLUSTER = "launchpad-previews"
_K8S_DEPLOY_MODES = frozenset({DeployMode.MANIFEST.value, DeployMode.PREVIEW.value})


@dataclass(frozen=True)
class CloudKubernetesTarget:
    provider: str
    cluster_name: str
    region: str
    kubeconfig_path: str
    context: str
    created: bool = False


def is_cloud_kubernetes_provider(provider: str | None) -> bool:
    raw = (provider or "").strip().lower()
    return raw in {
        CloudProvider.GCP.value,
        CloudProvider.AWS.value,
        CloudProvider.AZURE.value,
    }


def is_cloud_kubernetes_deploy(*, provider: str | None, deploy_mode: str | None) -> bool:
    mode = (deploy_mode or "").strip().lower()
    return is_cloud_kubernetes_provider(provider) and mode in _K8S_DEPLOY_MODES


def region_from_wizard(provider: str, snapshot: dict | None) -> str:
    from app.services.cloud_promote import default_region

    try:
        region = default_region(CloudProvider(provider))
    except ValueError:
        region = "us-central1"
    if not isinstance(snapshot, dict):
        return region
    cloud = snapshot.get("cloud")
    if not isinstance(cloud, dict):
        return region
    resources = cloud.get("resources")
    if not isinstance(resources, dict):
        return region
    return str(resources.get("region") or resources.get("location") or region).strip() or region


def select_gke_cluster(
    clusters: list[object],
    *,
    preferred_name: str,
    region: str,
) -> dict[str, object] | None:
    """Pick a RUNNING GKE cluster: preferred name, then same region, then any."""
    running: list[dict[str, object]] = []
    for item in clusters:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").upper()
        if status and status not in {"RUNNING", "PROVISIONING", "RECONCILING"}:
            continue
        if status == "RUNNING" or not status:
            running.append(item)
    preferred = (preferred_name or "").strip()
    region_prefix = (region or "").strip()
    for item in running:
        if str(item.get("name") or "").strip() == preferred:
            return item
    if region_prefix:
        for item in running:
            location = str(item.get("location") or item.get("zone") or "")
            if location == region_prefix or location.startswith(f"{region_prefix}-"):
                return item
    return running[0] if running else None


def kubeconfig_path_for(*, provider: str, project: str, region: str, cluster: str) -> Path:
    raw = f"{provider}-{project}-{region}-{cluster}".lower()
    safe = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-") or "cluster"
    root = Path.home() / ".launchpad" / "kubeconfigs"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.yaml"


def ensure_cloud_kubernetes_target(
    *,
    provider: str,
    credentials: CloudCredentials | None,
    region: str,
    environment_id: str,
    existing_vpc_id: str | None = None,
    create: bool = True,
) -> CloudKubernetesTarget:
    """Return kubeconfig + context for the cloud Kubernetes cluster.

    GCP: reuse a RUNNING GKE cluster (prefer ``launchpad-previews``), or create
    an Autopilot cluster when ``create`` is True. AWS/Azure: reuse an existing
    cluster only (do not silently fall back to local kind/k3d).
    """
    raw = (provider or "").strip().lower()
    if raw == CloudProvider.GCP.value:
        return _ensure_gke_target(
            credentials=credentials,
            region=region,
            environment_id=environment_id,
            existing_vpc_id=existing_vpc_id,
            create=create,
        )
    if raw == CloudProvider.AWS.value:
        raise CloudInstanceComputeError(
            "AWS Kubernetes promote needs an EKS cluster in this region. "
            "Create an EKS cluster, then retry. Local kind/k3d is not used for cloud deploys."
        )
    if raw == CloudProvider.AZURE.value:
        raise CloudInstanceComputeError(
            "Azure Kubernetes promote needs an AKS cluster in this location. "
            "Create an AKS cluster, then retry. Local kind/k3d is not used for cloud deploys."
        )
    raise CloudInstanceComputeError(f"Cloud Kubernetes is not supported for provider {provider}")


def _ensure_gke_target(
    *,
    credentials: CloudCredentials | None,
    region: str,
    environment_id: str,
    existing_vpc_id: str | None,
    create: bool,
) -> CloudKubernetesTarget:
    import shutil

    if shutil.which("gcloud") is None:
        raise CloudInstanceComputeError("gcloud CLI is required to deploy Kubernetes to GKE")

    env = _credential_env(credentials, environment_id=environment_id, provider=CloudProvider.GCP.value)
    project_id = resolve_gcp_project_id(credentials=credentials, env=env)
    if not project_id:
        raise CloudInstanceComputeError(
            "GCP project id is required to deploy to GKE. Save a service account JSON "
            "or GCP project id in Settings, then retry."
        )
    env["CLOUDSDK_CORE_PROJECT"] = project_id
    env["GOOGLE_CLOUD_PROJECT"] = project_id
    resolved_region = (region or "").strip() or "us-central1"

    _enable_container_api(credentials=credentials, project_id=project_id)

    listed = _run_cmd(
        [
            "gcloud",
            "container",
            "clusters",
            "list",
            f"--project={project_id}",
            "--format=json",
        ],
        timeout=60,
        check=False,
        env=env,
    )
    clusters: list[object] = []
    if listed.returncode == 0 and (listed.stdout or "").strip():
        try:
            parsed = json.loads(listed.stdout)
            if isinstance(parsed, list):
                clusters = parsed
        except json.JSONDecodeError:
            clusters = []

    selected = select_gke_cluster(
        clusters,
        preferred_name=SHARED_PREVIEW_CLUSTER,
        region=resolved_region,
    )
    created = False
    if selected is None:
        if not create:
            raise CloudInstanceComputeError(
                f"No GKE cluster found in project {project_id} to tear down this preview."
            )
        selected = _create_gke_autopilot(
            project_id=project_id,
            region=resolved_region,
            env=env,
            existing_vpc_id=existing_vpc_id,
        )
        created = True

    cluster_name = str(selected.get("name") or SHARED_PREVIEW_CLUSTER).strip()
    location = str(selected.get("location") or resolved_region).strip() or resolved_region
    kubeconfig = kubeconfig_path_for(
        provider="gcp",
        project=project_id,
        region=location,
        cluster=cluster_name,
    )
    loc_flag = "--zone" if _is_gke_zone(location) else "--region"
    creds = _run_cmd(
        [
            "gcloud",
            "container",
            "clusters",
            "get-credentials",
            cluster_name,
            loc_flag,
            location,
            f"--project={project_id}",
        ],
        timeout=90,
        check=False,
        env={**env, "KUBECONFIG": str(kubeconfig)},
    )
    if creds.returncode != 0:
        detail = (creds.stderr or creds.stdout or "get-credentials failed")[:400]
        raise CloudInstanceComputeError(f"Failed to fetch GKE credentials: {detail}")

    context = _kube_context_from_config(kubeconfig) or f"gke_{project_id}_{location}_{cluster_name}"
    logger.info(
        "cloud_kubernetes_target_ready",
        provider="gcp",
        cluster=cluster_name,
        location=location,
        context=context,
        created=created,
        kubeconfig=str(kubeconfig),
    )
    return CloudKubernetesTarget(
        provider=CloudProvider.GCP.value,
        cluster_name=cluster_name,
        region=location,
        kubeconfig_path=str(kubeconfig),
        context=context,
        created=created,
    )


def _enable_container_api(*, credentials: CloudCredentials | None, project_id: str) -> None:
    sa = (credentials.gcp_sa_key_json if credentials is not None else None) or ""
    if not sa.strip():
        return
    try:
        from app.services.gcp_api_enablement import enable_gcp_apis

        enable_gcp_apis(
            sa_json=sa,
            project_id=project_id,
            apis=["container.googleapis.com"],
            timeout_seconds=180.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("gke_api_enable_skipped", project_id=project_id, error=str(exc)[:200])


def _create_gke_autopilot(
    *,
    project_id: str,
    region: str,
    env: dict[str, str],
    existing_vpc_id: str | None,
) -> dict[str, object]:
    cmd = [
        "gcloud",
        "container",
        "clusters",
        "create-auto",
        SHARED_PREVIEW_CLUSTER,
        f"--region={region}",
        f"--project={project_id}",
        "--quiet",
    ]
    network = (existing_vpc_id or "").strip()
    if network and network not in {"default", "default-vpc"}:
        cmd.append(f"--network={network}")
    logger.info("gke_autopilot_create_start", cluster=SHARED_PREVIEW_CLUSTER, region=region)
    created = _run_cmd(cmd, timeout=720, check=False, env=env)
    if created.returncode != 0:
        detail = (created.stderr or created.stdout or "create-auto failed")[:600]
        if "already exists" in detail.lower():
            return {"name": SHARED_PREVIEW_CLUSTER, "location": region, "status": "RUNNING"}
        raise CloudInstanceComputeError(f"Failed to create GKE Autopilot cluster: {detail}")
    return {"name": SHARED_PREVIEW_CLUSTER, "location": region, "status": "RUNNING"}


def _is_gke_zone(location: str) -> bool:
    parts = (location or "").split("-")
    return len(parts) >= 3 and len(parts[-1]) <= 2


def _kube_context_from_config(path: Path) -> str | None:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    current = str(data.get("current-context") or "").strip()
    return current or None

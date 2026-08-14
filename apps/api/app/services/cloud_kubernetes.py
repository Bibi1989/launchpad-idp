"""Connect Kubernetes previews to GKE/EKS/AKS instead of the local kind/k3d cluster."""

from __future__ import annotations

import json
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.core.logging import get_logger, sanitize_log_message
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


def gke_cluster_has_public_endpoint(cluster: dict[str, object]) -> bool:
    """True when the control plane still exposes a public API endpoint."""
    private = cluster.get("privateClusterConfig")
    if isinstance(private, dict):
        # Private-endpoint-only clusters are unreachable from the Launchpad worker.
        if bool(private.get("enablePrivateEndpoint")) and not str(
            private.get("publicEndpoint") or ""
        ).strip():
            return False
        if private.get("publicEndpoint") is False:
            return False
    endpoint = str(cluster.get("endpoint") or "").strip()
    return bool(endpoint)


def select_gke_cluster(
    clusters: list[object],
    *,
    preferred_name: str,
    region: str,
) -> dict[str, object] | None:
    """Pick a RUNNING GKE cluster: preferred name, then same region, then any.

    Skips private-endpoint-only clusters that the control-plane worker cannot reach.
    """
    running: list[dict[str, object]] = []
    for item in clusters:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").upper()
        if status and status not in {"RUNNING", "PROVISIONING", "RECONCILING"}:
            continue
        if status == "RUNNING" or not status:
            if gke_cluster_has_public_endpoint(item):
                running.append(item)
            else:
                logger.info(
                    "gke_cluster_skipped_private_endpoint",
                    name=str(item.get("name") or ""),
                    location=str(item.get("location") or ""),
                )
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
    an Autopilot cluster when ``create`` is True.
    AWS: reuse an ACTIVE EKS cluster (prefer ``launchpad-previews``), or create
    an EKS Auto Mode cluster via boto3 when ``create`` is True (no aws CLI /
    eksctl required on the worker host).
    Azure: reuse an existing AKS cluster only for now.
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
        return _ensure_eks_target(
            credentials=credentials,
            region=region,
            environment_id=environment_id,
            create=create,
        )
    if raw == CloudProvider.AZURE.value:
        raise CloudInstanceComputeError(
            "Azure Kubernetes promote needs an AKS cluster in this location. "
            "Create an AKS cluster, then retry. Local kind/k3d is not used for cloud deploys."
        )
    raise CloudInstanceComputeError(f"Cloud Kubernetes is not supported for provider {provider}")


def select_eks_cluster(names: list[str], *, preferred_name: str) -> str | None:
    """Pick an EKS cluster name: preferred shared preview, else first listed."""
    cleaned = [str(n).strip() for n in names if str(n).strip()]
    preferred = (preferred_name or "").strip()
    if preferred and preferred in cleaned:
        return preferred
    return cleaned[0] if cleaned else None


def _ensure_eks_target(
    *,
    credentials: CloudCredentials | None,
    region: str,
    environment_id: str,
    create: bool,
) -> CloudKubernetesTarget:
    from app.services.aws_client import (
        AwsClientError,
        create_eks_auto_cluster,
        ensure_eks_auto_roles,
        ensure_eks_preview_subnets,
        eks_cluster_status,
        eks_cluster_subnet_ids,
        list_eks_cluster_names,
        sts_account_id,
        tag_eks_subnets_for_load_balancer,
        wait_eks_cluster_active,
        write_eks_kubeconfig,
    )
    from app.services.cloud_networks import _normalize_aws_region

    env = _credential_env(
        credentials, environment_id=environment_id, provider=CloudProvider.AWS.value
    )
    resolved_region = _normalize_aws_region((region or "").strip() or "us-east-1")
    env = {**env, "AWS_DEFAULT_REGION": resolved_region, "AWS_REGION": resolved_region}

    try:
        account_id = sts_account_id(env=env, region=resolved_region)
        names = list_eks_cluster_names(env=env, region=resolved_region)
    except AwsClientError as exc:
        raise CloudInstanceComputeError(str(exc)) from exc

    selected = select_eks_cluster(names, preferred_name=SHARED_PREVIEW_CLUSTER)
    created = False
    # Always refresh Auto Mode IAM trust/policies (existing roles may lack sts:TagSession).
    try:
        ensure_eks_auto_roles(env=env, region=resolved_region)
    except AwsClientError as exc:
        if selected is None and create:
            raise CloudInstanceComputeError(str(exc)) from exc
        logger.warning(
            "eks_auto_roles_refresh_failed",
            error=sanitize_log_message(str(exc)[:300]),
        )
    if selected is None:
        if not create:
            raise CloudInstanceComputeError(
                f"No EKS cluster found in {resolved_region} to tear down this preview."
            )
        try:
            subnet_ids = ensure_eks_preview_subnets(env=env, region=resolved_region)
            cluster_role_arn, node_role_arn = ensure_eks_auto_roles(
                env=env, region=resolved_region
            )
            create_eks_auto_cluster(
                env=env,
                region=resolved_region,
                name=SHARED_PREVIEW_CLUSTER,
                subnet_ids=subnet_ids,
                cluster_role_arn=cluster_role_arn,
                node_role_arn=node_role_arn,
            )
        except AwsClientError as exc:
            raise CloudInstanceComputeError(str(exc)) from exc
        selected = SHARED_PREVIEW_CLUSTER
        created = True

    try:
        status = eks_cluster_status(env=env, region=resolved_region, name=selected)
    except AwsClientError as exc:
        raise CloudInstanceComputeError(str(exc)) from exc
    if status and status not in {"ACTIVE", "CREATING", "UPDATING"}:
        raise CloudInstanceComputeError(
            f"EKS cluster '{selected}' is {status}. Wait until it is ACTIVE, then retry."
        )
    if status == "CREATING" or (created and status != "ACTIVE"):
        try:
            wait_eks_cluster_active(
                env=env, region=resolved_region, name=selected, timeout_seconds=1500.0
            )
        except AwsClientError as exc:
            raise CloudInstanceComputeError(str(exc)) from exc
    elif status == "UPDATING":
        # A prior control-plane upgrade may still be running. Wait briefly, then
        # continue if the API is reachable - never block Celery soft/hard limits.
        try:
            wait_eks_cluster_active(
                env=env, region=resolved_region, name=selected, timeout_seconds=90.0
            )
        except AwsClientError as exc:
            logger.warning(
                "eks_cluster_still_updating",
                cluster=selected,
                error=sanitize_log_message(str(exc)[:300]),
            )

    # Do not call ensure_eks_cluster_version here. Multi-minor upgrades take far
    # longer than Celery provision/teardown time limits. New clusters are created
    # at _EKS_KUBERNETES_VERSION; existing clusters stay on their current minor.

    try:
        subnet_ids = eks_cluster_subnet_ids(env=env, region=resolved_region, name=selected)
        if subnet_ids:
            tag_eks_subnets_for_load_balancer(
                env=env,
                region=resolved_region,
                subnet_ids=subnet_ids,
                cluster_name=selected,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "eks_subnet_elb_tag_failed",
            cluster=selected,
            error=sanitize_log_message(str(exc)[:300]),
        )

    kubeconfig = kubeconfig_path_for(
        provider="aws",
        project=account_id or "aws",
        region=resolved_region,
        cluster=selected,
    )
    try:
        context = write_eks_kubeconfig(
            env=env,
            region=resolved_region,
            cluster_name=selected,
            kubeconfig_path=str(kubeconfig),
        )
    except AwsClientError as exc:
        raise CloudInstanceComputeError(str(exc)) from exc

    if not _kubeconfig_api_tcp_ok(kubeconfig, timeout_seconds=8.0):
        server = _kubeconfig_server(kubeconfig) or "unknown"
        raise CloudInstanceComputeError(
            f"Cannot reach EKS API for cluster '{selected}' ({server}). "
            "Confirm the cluster endpoint is public and this host can reach it, "
            "or create/use an EKS cluster with a public endpoint. "
            "Local kind/k3d is not used for AWS Kubernetes deploys."
        )

    context = context or (
        _kube_context_from_config(kubeconfig)
        or f"arn:aws:eks:{resolved_region}:{account_id or 'account'}:cluster/{selected}"
    )
    logger.info(
        "cloud_kubernetes_target_ready",
        provider="aws",
        cluster=selected,
        location=resolved_region,
        context=context,
        created=created,
        kubeconfig=str(kubeconfig),
    )
    return CloudKubernetesTarget(
        provider=CloudProvider.AWS.value,
        cluster_name=selected,
        region=resolved_region,
        kubeconfig_path=str(kubeconfig),
        context=context,
        created=created,
    )


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
                f"No publicly reachable GKE cluster found in project {project_id} "
                "to tear down this preview."
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
    _fetch_gke_credentials(
        cluster_name=cluster_name,
        location=location,
        loc_flag=loc_flag,
        project_id=project_id,
        kubeconfig=kubeconfig,
        env=env,
    )

    # Master authorized networks (or a stale private endpoint) drop packets from
    # the Launchpad worker. Open public API access for the shared preview cluster
    # and re-check TCP reachability before handing the kubeconfig to the client.
    _ensure_gke_api_reachable(
        cluster_name=cluster_name,
        location=location,
        loc_flag=loc_flag,
        project_id=project_id,
        kubeconfig=kubeconfig,
        env=env,
        selected=selected if isinstance(selected, dict) else {},
    )

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


def _fetch_gke_credentials(
    *,
    cluster_name: str,
    location: str,
    loc_flag: str,
    project_id: str,
    kubeconfig: Path,
    env: dict[str, str],
    dns_endpoint: bool = False,
) -> None:
    cmd = [
        "gcloud",
        "container",
        "clusters",
        "get-credentials",
        cluster_name,
        loc_flag,
        location,
        f"--project={project_id}",
    ]
    if dns_endpoint:
        cmd.append("--dns-endpoint")
    creds = _run_cmd(
        cmd,
        timeout=90,
        check=False,
        env={**env, "KUBECONFIG": str(kubeconfig)},
    )
    if creds.returncode != 0:
        detail = (creds.stderr or creds.stdout or "get-credentials failed")[:400]
        raise CloudInstanceComputeError(f"Failed to fetch GKE credentials: {detail}")


def _ensure_gke_api_reachable(
    *,
    cluster_name: str,
    location: str,
    loc_flag: str,
    project_id: str,
    kubeconfig: Path,
    env: dict[str, str],
    selected: dict[str, object],
) -> None:
    """Make the control plane reachable from this host, or raise a clear error."""
    if _kubeconfig_api_tcp_ok(kubeconfig):
        return

    logger.warning(
        "gke_api_unreachable_before_repair",
        cluster=cluster_name,
        location=location,
        server=_kubeconfig_server(kubeconfig),
    )

    # Prefer opening the shared preview cluster to the public internet so local
    # and OCI workers can apply manifests. Org policies may still block this.
    if cluster_name == SHARED_PREVIEW_CLUSTER or not _master_authorized_allows_any(selected):
        _open_gke_master_authorized_networks(
            cluster_name=cluster_name,
            location=location,
            loc_flag=loc_flag,
            project_id=project_id,
            env=env,
        )
        # Credentials can still point at a private IP; refresh after the update.
        _fetch_gke_credentials(
            cluster_name=cluster_name,
            location=location,
            loc_flag=loc_flag,
            project_id=project_id,
            kubeconfig=kubeconfig,
            env=env,
        )
        if _kubeconfig_api_tcp_ok(kubeconfig):
            return

    # DNS-based control plane endpoint (when enabled on the cluster).
    try:
        _fetch_gke_credentials(
            cluster_name=cluster_name,
            location=location,
            loc_flag=loc_flag,
            project_id=project_id,
            kubeconfig=kubeconfig,
            env=env,
            dns_endpoint=True,
        )
        if _kubeconfig_api_tcp_ok(kubeconfig, timeout_seconds=8.0):
            return
    except CloudInstanceComputeError:
        logger.info("gke_dns_endpoint_credentials_unavailable", cluster=cluster_name)

    server = _kubeconfig_server(kubeconfig) or "unknown"
    raise CloudInstanceComputeError(
        f"Cannot reach GKE API for cluster '{cluster_name}' ({server}). "
        "The control plane is likely private-only or Master Authorized Networks "
        "blocks this host. For Launchpad previews, either: "
        f"(1) run `gcloud container clusters update {cluster_name} {loc_flag} {location} "
        "--no-enable-master-authorized-networks --project="
        f"{project_id}`, or (2) add this machine's public IP/32 to master authorized "
        "networks, or (3) create/use a GKE cluster with a public endpoint. "
        "Local kind/k3d is not used for GCP Kubernetes deploys."
    )


def _master_authorized_allows_any(cluster: dict[str, object]) -> bool:
    cfg = cluster.get("masterAuthorizedNetworksConfig")
    if not isinstance(cfg, dict):
        return True
    if not bool(cfg.get("enabled")):
        return True
    return False


def _open_gke_master_authorized_networks(
    *,
    cluster_name: str,
    location: str,
    loc_flag: str,
    project_id: str,
    env: dict[str, str],
) -> None:
    """Disable master authorized networks so the worker can reach the public API."""
    updated = _run_cmd(
        [
            "gcloud",
            "container",
            "clusters",
            "update",
            cluster_name,
            loc_flag,
            location,
            f"--project={project_id}",
            "--no-enable-master-authorized-networks",
            "--quiet",
        ],
        timeout=300,
        check=False,
        env=env,
    )
    if updated.returncode != 0:
        detail = (updated.stderr or updated.stdout or "")[:300]
        logger.warning(
            "gke_open_master_networks_failed",
            cluster=cluster_name,
            detail=detail,
        )
        # Fallback: authorize this host's egress IP only.
        egress = _public_egress_cidr()
        if not egress:
            return
        authorize = _run_cmd(
            [
                "gcloud",
                "container",
                "clusters",
                "update",
                cluster_name,
                loc_flag,
                location,
                f"--project={project_id}",
                "--enable-master-authorized-networks",
                f"--master-authorized-networks={egress}",
                "--quiet",
            ],
            timeout=300,
            check=False,
            env=env,
        )
        if authorize.returncode != 0:
            logger.warning(
                "gke_authorize_egress_ip_failed",
                cluster=cluster_name,
                cidr=egress,
                detail=(authorize.stderr or authorize.stdout or "")[:300],
            )
        else:
            logger.info("gke_master_authorized_networks_set", cluster=cluster_name, cidr=egress)
        return
    logger.info("gke_master_authorized_networks_disabled", cluster=cluster_name)
    # Propagate quickly; TCP probe retries if the LB is still catching up.
    time.sleep(1.0)


def _public_egress_cidr() -> str | None:
    """Best-effort public IPv4 of this host as a /32 for master authorized networks."""
    import urllib.request

    for url in (
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://checkip.amazonaws.com",
    ):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                raw = response.read().decode("utf-8", errors="ignore").strip()
            if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", raw):
                return f"{raw}/32"
        except Exception:
            continue
    return None


def _kubeconfig_server(path: Path) -> str | None:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    clusters = data.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        return None
    first = clusters[0]
    if not isinstance(first, dict):
        return None
    cluster = first.get("cluster")
    if not isinstance(cluster, dict):
        return None
    server = str(cluster.get("server") or "").strip()
    return server or None


def _kubeconfig_api_tcp_ok(path: Path, *, timeout_seconds: float = 8.0) -> bool:
    server = _kubeconfig_server(path)
    if not server:
        return False
    parsed = urlparse(server)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError as exc:
        logger.info(
            "gke_api_tcp_probe_failed",
            host=host,
            port=port,
            error=str(exc)[:200],
        )
        return False


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
        # Launchpad workers (laptop / OCI) must reach the public control plane.
        "--no-enable-master-authorized-networks",
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
    return {
        "name": SHARED_PREVIEW_CLUSTER,
        "location": region,
        "status": "RUNNING",
        "endpoint": "pending",
        "masterAuthorizedNetworksConfig": {"enabled": False},
    }


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

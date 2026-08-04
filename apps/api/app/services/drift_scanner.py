"""Compare live Kubernetes preview workloads against control-plane expectations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.models.domain import AuditAction, AuditStatus, Environment
from app.schemas.k8s import DeployMode

if TYPE_CHECKING:
    from app.services.audit import AuditService
    from app.services.kubernetes import KubernetesProvisioner

logger = get_logger(__name__)

GIT_COMMIT_LABEL = "launchpad.io/git-commit"
GIT_COMMIT_ENV = "GIT_COMMIT_SHA"
DRIFT_SCANNER_ACTOR = "system:drift-scanner"
ENV_ID_LABEL = "launchpad.io/environment-id"


@dataclass(frozen=True, slots=True)
class DriftFinding:
    environment_id: str
    namespace: str
    mismatches: tuple[str, ...]

    @property
    def detail(self) -> str:
        return "; ".join(self.mismatches)


@dataclass(frozen=True, slots=True)
class ExpectedDeployment:
    name: str
    image: str


def inspect_live_deployment(
    deployment: object,
    *,
    expected_image: str,
    expected_commit: str | None,
) -> list[str]:
    """Return human-readable drift reasons (empty when in sync)."""
    mismatches: list[str] = []
    live_image = _container_image(deployment)
    if live_image is None:
        return ["deployment has no containers"]
    if live_image != expected_image:
        mismatches.append(f"image expected={expected_image} live={live_image}")

    live_commit = _extract_commit(deployment)
    if expected_commit and live_commit and live_commit != expected_commit:
        mismatches.append(f"commit expected={expected_commit} live={live_commit}")
    elif expected_commit and not live_commit:
        mismatches.append(f"commit expected={expected_commit} live=<missing>")

    return mismatches


def expected_manifest_deployments(
    workspace_root: Path,
    *,
    environment: Environment,
    default_image: str,
) -> list[ExpectedDeployment]:
    """Build expected Deployment inventory from patched workspace manifests."""
    from app.services.manifest_deploy import load_manifest_documents, patch_manifest_documents

    image = environment.workload_image or default_image
    documents = load_manifest_documents(workspace_root)
    patched = patch_manifest_documents(
        documents,
        target_namespace=environment.namespace_name,
        environment_id=str(environment.id),
        name=environment.name,
        git_branch=environment.git_branch,
        git_repo_url=environment.git_repo_url,
        ttl_expires_at=environment.ttl_expires_at.isoformat(),
        owner_label="launchpad",
        image=image,
    )
    expected: list[ExpectedDeployment] = []
    for doc in patched:
        if str(doc.get("kind") or "") != "Deployment":
            continue
        name = str((doc.get("metadata") or {}).get("name") or "").strip()
        if not name:
            continue
        containers = (
            ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
        ).get("containers") or []
        live_image = ""
        if containers and isinstance(containers[0], dict):
            live_image = str(containers[0].get("image") or image)
        expected.append(ExpectedDeployment(name=name, image=live_image or image))
    return expected


def inspect_manifest_inventory(
    *,
    expected: list[ExpectedDeployment],
    live_by_name: dict[str, object],
) -> list[str]:
    """Compare expected Deployments to live namespaced Deployments."""
    mismatches: list[str] = []
    if not expected:
        return ["no Deployment resources found in workspace manifests"]

    for item in expected:
        live = live_by_name.get(item.name)
        if live is None:
            mismatches.append(f"deployment {item.name} missing")
            continue
        live_image = _container_image(live) or ""
        if live_image != item.image:
            mismatches.append(
                f"deployment {item.name} image expected={item.image} live={live_image}"
            )
    return mismatches


def _container_image(deployment: object) -> str | None:
    spec = getattr(deployment, "spec", None)
    template = getattr(spec, "template", None) if spec else None
    pod_spec = getattr(template, "spec", None) if template else None
    containers = getattr(pod_spec, "containers", None) if pod_spec else None
    if not containers:
        return None
    return getattr(containers[0], "image", None) or ""


def _extract_commit(deployment: object) -> str | None:
    metadata = getattr(deployment, "metadata", None)
    labels = getattr(metadata, "labels", None) or {}
    if GIT_COMMIT_LABEL in labels:
        return str(labels[GIT_COMMIT_LABEL])

    annotations = getattr(metadata, "annotations", None) or {}
    if GIT_COMMIT_LABEL in annotations:
        return str(annotations[GIT_COMMIT_LABEL])

    container = None
    spec = getattr(deployment, "spec", None)
    template = getattr(spec, "template", None) if spec else None
    pod_spec = getattr(template, "spec", None) if template else None
    containers = getattr(pod_spec, "containers", None) if pod_spec else None
    if containers:
        container = containers[0]
    if container is None:
        return None
    env_vars = getattr(container, "env", None) or []
    for item in env_vars:
        name = getattr(item, "name", None)
        if name == GIT_COMMIT_ENV:
            value = getattr(item, "value", None)
            return str(value) if value else None
    return None


def scan_environment(
    provisioner: KubernetesProvisioner,
    environment: Environment,
    *,
    default_image: str,
    workspace_root: Path | None = None,
) -> DriftFinding | None:
    """Compare live cluster state to control-plane expectations. Returns None if in sync."""
    if not provisioner._settings.kubernetes_enabled:  # noqa: SLF001
        return None
    if not provisioner.clients_ready:
        # Cluster clients unavailable (kubeconfig/context unreachable): cannot
        # compare against live state, so skip rather than crash the scan.
        logger.warning("drift_scan_skipped_no_cluster_client", environment_id=str(environment.id))
        return None

    deploy_mode = (environment.deploy_mode or DeployMode.PREVIEW.value).lower()
    if deploy_mode == DeployMode.MANIFEST.value:
        return _scan_manifest(
            provisioner,
            environment,
            default_image=default_image,
            workspace_root=workspace_root,
        )
    return _scan_preview(provisioner, environment, default_image=default_image)


def _scan_preview(
    provisioner: KubernetesProvisioner,
    environment: Environment,
    *,
    default_image: str,
) -> DriftFinding | None:
    if not provisioner.clients_ready:
        return None

    from kubernetes.client.rest import ApiException

    namespace = environment.namespace_name
    expected_image = environment.workload_image or default_image
    expected_commit = (environment.latest_commit_sha or "").strip() or None

    try:
        deployment = provisioner._apps.read_namespaced_deployment("app", namespace)  # noqa: SLF001
    except ApiException as exc:
        if exc.status == 404:
            return DriftFinding(
                environment_id=str(environment.id),
                namespace=namespace,
                mismatches=("deployment app not found in namespace",),
            )
        logger.warning(
            "drift_scan_read_failed",
            environment_id=str(environment.id),
            namespace=namespace,
            status=exc.status,
        )
        return None
    except OSError as exc:
        logger.warning(
            "drift_scan_cluster_unreachable",
            environment_id=str(environment.id),
            error=str(exc),
        )
        return None

    mismatches = inspect_live_deployment(
        deployment,
        expected_image=expected_image,
        expected_commit=expected_commit,
    )
    if not mismatches:
        return None
    return DriftFinding(
        environment_id=str(environment.id),
        namespace=namespace,
        mismatches=tuple(mismatches),
    )


def _scan_manifest(
    provisioner: KubernetesProvisioner,
    environment: Environment,
    *,
    default_image: str,
    workspace_root: Path | None,
) -> DriftFinding | None:
    if not provisioner.clients_ready:
        return None

    from kubernetes.client.rest import ApiException

    namespace = environment.namespace_name
    if workspace_root is None or not workspace_root.is_dir():
        return DriftFinding(
            environment_id=str(environment.id),
            namespace=namespace,
            mismatches=("manifest workspace root unavailable for drift scan",),
        )

    try:
        expected = expected_manifest_deployments(
            workspace_root,
            environment=environment,
            default_image=default_image,
        )
    except OSError as exc:
        logger.warning(
            "drift_scan_manifest_load_failed",
            environment_id=str(environment.id),
            error=str(exc),
        )
        return DriftFinding(
            environment_id=str(environment.id),
            namespace=namespace,
            mismatches=(f"manifest load failed: {exc}",),
        )

    label_selector = f"{ENV_ID_LABEL}={environment.id}"
    try:
        listed = provisioner._apps.list_namespaced_deployment(  # noqa: SLF001
            namespace,
            label_selector=label_selector,
        )
    except ApiException as exc:
        logger.warning(
            "drift_scan_list_failed",
            environment_id=str(environment.id),
            namespace=namespace,
            status=exc.status,
        )
        return None
    except OSError as exc:
        logger.warning(
            "drift_scan_cluster_unreachable",
            environment_id=str(environment.id),
            error=str(exc),
        )
        return None

    live_by_name: dict[str, Any] = {}
    for item in getattr(listed, "items", None) or []:
        name = getattr(getattr(item, "metadata", None), "name", None)
        if name:
            live_by_name[str(name)] = item

    # Fallback: some older manifests may lack environment-id labels on Deployment.
    if not live_by_name:
        try:
            listed_all = provisioner._apps.list_namespaced_deployment(namespace)  # noqa: SLF001
        except (ApiException, OSError):
            listed_all = None
        if listed_all is not None:
            for item in getattr(listed_all, "items", None) or []:
                name = getattr(getattr(item, "metadata", None), "name", None)
                if name:
                    live_by_name[str(name)] = item

    mismatches = inspect_manifest_inventory(expected=expected, live_by_name=live_by_name)
    if not mismatches:
        return None
    return DriftFinding(
        environment_id=str(environment.id),
        namespace=namespace,
        mismatches=tuple(mismatches),
    )


async def record_drift_if_changed(
    audit: AuditService,
    *,
    environment: Environment,
    finding: DriftFinding,
    actor_id: str,
) -> bool:
    """Append DRIFT_DETECTED when detail differs from the latest drift audit."""
    latest = await audit.latest_for_environment(
        environment.id,
        AuditAction.DRIFT_DETECTED,
    )
    if latest is not None and latest.detail == finding.detail:
        return False
    await audit.record(
        action=AuditAction.DRIFT_DETECTED,
        actor_id=actor_id,
        status=AuditStatus.SUCCESS,
        environment_id=environment.id,
        workspace_id=environment.workspace_id,
        commit_sha=environment.latest_commit_sha,
        detail=finding.detail,
    )
    return True

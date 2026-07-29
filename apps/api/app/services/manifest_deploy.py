"""Apply workspace Kubernetes manifests into an ephemeral preview namespace."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.k8s_spec import APP_NAME, build_preview_labels
from app.services.kubernetes import (
    KubernetesProvisioner,
    ProvisionedResources,
    resolve_preview_node_port,
)

logger = get_logger(__name__)

K8S_MANIFESTS_DIR = Path("infra") / "k8s" / "manifests"
HELM_CHART_DIR = Path("infra") / "helm" / "app-chart"

# Preferred HTTP ports when an image EXPOSEs several (skip brokers/DBs).
_HTTP_PORT_PREFERENCE: tuple[int, ...] = (
    80,
    8080,
    8000,
    3000,
    5000,
    5173,
    4200,
    4000,
    8501,
    15672,
)
_NON_HTTP_PORTS = frozenset({5672, 6379, 5432, 3306, 27017, 9092, 9200})

# kubernetes.utils.create_from_dict incorrectly maps autoscaling.k8s.io → AutoscalingV1Api
# (HPA only). These kinds must go through DynamicClient / CustomObjectsApi.
_DYNAMIC_APPLY_KINDS = frozenset(
    {
        "VerticalPodAutoscaler",
        "NodePool",
        "EC2NodeClass",
    }
)

# Optional CRDs: skip (warn) when the cluster does not have the API installed.
_OPTIONAL_CRD_KINDS = frozenset(
    {
        "VerticalPodAutoscaler",
        "NodePool",
        "EC2NodeClass",
    }
)


def _requires_dynamic_apply(doc: dict[str, Any]) -> bool:
    kind = str(doc.get("kind") or "")
    api_version = str(doc.get("apiVersion") or "")
    if kind in _DYNAMIC_APPLY_KINDS:
        return True
    if api_version.startswith("autoscaling.k8s.io/"):
        return True
    if api_version.startswith("karpenter.sh/") or api_version.startswith("karpenter.k8s.aws/"):
        return True
    return False


def _is_optional_crd(doc: dict[str, Any]) -> bool:
    return str(doc.get("kind") or "") in _OPTIONAL_CRD_KINDS


def workspace_has_raw_manifests(workspace_root: Path) -> bool:
    manifest_dir = workspace_root / K8S_MANIFESTS_DIR
    if not manifest_dir.is_dir():
        return False
    return any(manifest_dir.glob("*.y*ml"))


def workspace_has_helm_chart(workspace_root: Path) -> bool:
    return (workspace_root / HELM_CHART_DIR / "Chart.yaml").is_file()


def workspace_has_deployable_k8s(workspace_root: Path) -> bool:
    return workspace_has_raw_manifests(workspace_root) or workspace_has_helm_chart(workspace_root)


def load_manifest_documents(workspace_root: Path) -> list[dict[str, Any]]:
    manifest_dir = workspace_root / K8S_MANIFESTS_DIR
    documents: list[dict[str, Any]] = []
    for path in sorted(manifest_dir.glob("*.y*ml")):
        raw = path.read_text(encoding="utf-8")
        for doc in yaml.safe_load_all(raw):
            if isinstance(doc, dict) and doc.get("kind"):
                documents.append(doc)
    return documents


def load_helm_template_documents(
    workspace_root: Path,
    *,
    namespace: str | None = None,
    release_name: str = APP_NAME,
) -> list[dict[str, Any]]:
    """Render ``infra/helm/app-chart`` to Kubernetes objects via ``helm template``.

    Forces ``fullnameOverride``/``nameOverride`` to ``app`` so preview waits and
    NodePort assignment match the Launchpad Service/Deployment naming contract.
    """
    chart_dir = workspace_root / HELM_CHART_DIR
    if not (chart_dir / "Chart.yaml").is_file():
        raise FileNotFoundError(f"Helm chart not found at {chart_dir}")

    cmd = [
        "helm",
        "template",
        release_name,
        str(chart_dir),
        "--set",
        f"fullnameOverride={APP_NAME}",
        "--set",
        f"nameOverride={APP_NAME}",
    ]
    if namespace:
        cmd.extend(["--namespace", namespace])

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Helm chart workspace requires the `helm` CLI on the API/worker host "
            f"(chart={chart_dir.as_posix()})"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"helm template failed for {chart_dir}: {exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:500]
        raise RuntimeError(
            f"helm template failed for {chart_dir}"
            + (f": {detail}" if detail else "")
        )

    documents: list[dict[str, Any]] = []
    for doc in yaml.safe_load_all(completed.stdout or ""):
        if isinstance(doc, dict) and doc.get("kind"):
            documents.append(doc)
    if not documents:
        raise RuntimeError(f"helm template produced no Kubernetes documents for {chart_dir}")
    return documents


def load_workspace_manifest_documents(
    workspace_root: Path,
    *,
    namespace: str | None = None,
) -> list[dict[str, Any]]:
    """Load deployable docs from raw manifests, else rendered Helm chart."""
    if workspace_has_raw_manifests(workspace_root):
        return load_manifest_documents(workspace_root)
    if workspace_has_helm_chart(workspace_root):
        return load_helm_template_documents(workspace_root, namespace=namespace)
    raise FileNotFoundError(
        "No deployable Kubernetes workload found. Expected "
        f"{K8S_MANIFESTS_DIR.as_posix()}/*.yaml or "
        f"{HELM_CHART_DIR.as_posix()}/Chart.yaml "
        f"in workspace {workspace_root}"
    )


def _first_deployment_image(documents: list[dict[str, Any]]) -> str | None:
    """Return the first container image declared on a Deployment document or Helm values."""
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        kind = str(doc.get("kind") or "").lower()
        if kind == "deployment":
            containers = (
                ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
            ).get("containers") or []
            for container in containers:
                if isinstance(container, dict):
                    image = container.get("image")
                    if isinstance(image, str) and image.strip():
                        return image.strip()
        img = doc.get("image")
        if isinstance(img, dict) and img.get("repository"):
            repo = str(img.get("repository")).strip()
            tag = str(img.get("tag") or "").strip()
            if repo:
                if tag and not repo.endswith(f":{tag}") and ":" not in repo:
                    return f"{repo}:{tag}"
                return repo
        elif isinstance(img, str) and img.strip():
            return img.strip()
    return None


def resolve_manifest_workload_image(
    documents: list[dict[str, Any]],
    *,
    provided_image: str | None,
    default_image: str,
) -> str:
    """Prefer a built/override image, else the workspace Deployment image, else default.

    Workspace manifests are the source of truth for MANIFEST deploys. Passing the
    environment's stale default (e.g. nginx) must not overwrite a user-edited image.
    """
    if provided_image and provided_image.strip() and provided_image.strip() != default_image:
        return provided_image.strip()
    manifest_image = _first_deployment_image(documents)
    if manifest_image and manifest_image.strip():
        return manifest_image.strip()
    if provided_image and provided_image.strip():
        return provided_image.strip()
    return default_image


def inspect_image_exposed_ports(image: str) -> list[int]:
    """Return EXPOSE ports from a local Docker image (empty if unavailable)."""
    try:
        completed = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                image,
                "--format",
                "{{json .Config.ExposedPorts}}",
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("image_expose_inspect_unavailable", image=image, error=str(exc))
        return []

    if completed.returncode != 0:
        # Image may not be local yet — best-effort pull then re-inspect.
        pull = subprocess.run(
            ["docker", "pull", image],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if pull.returncode != 0:
            logger.info(
                "image_expose_pull_failed",
                image=image,
                error=(pull.stderr or pull.stdout or "").strip()[:300],
            )
            return []
        completed = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                image,
                "--format",
                "{{json .Config.ExposedPorts}}",
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if completed.returncode != 0:
            return []

    raw = (completed.stdout or "").strip()
    if not raw or raw == "null":
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    ports: list[int] = []
    for key in payload:
        try:
            ports.append(int(str(key).split("/", 1)[0]))
        except ValueError:
            continue
    return sorted(set(ports))


def _manifest_container_port(container: dict[str, Any]) -> int | None:
    ports = container.get("ports") or []
    for item in ports:
        if not isinstance(item, dict):
            continue
        value = item.get("containerPort", item.get("container_port"))
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _manifest_service_target_port(documents: list[dict[str, Any]]) -> int | None:
    for doc in documents:
        if doc.get("kind") != "Service":
            continue
        ports = ((doc.get("spec") or {}).get("ports")) or []
        for item in ports:
            if not isinstance(item, dict):
                continue
            value = item.get("targetPort", item.get("target_port"))
            if isinstance(value, str) and value.strip().isdigit():
                value = int(value.strip())
            if isinstance(value, int):
                return value
    return None


def resolve_workload_listen_port(
    *,
    image: str,
    manifest_port: int | None,
    exposed_ports: list[int] | None = None,
    service_target_port: int | None = None,
) -> int:
    """Pick the container listen port for Service + probes.

    Scaffold manifests default to 80. When the image EXPOSEs a different HTTP port
    (e.g. Vite on 5000), prefer the image so readiness does not probe the wrong port.
    Helm charts often keep Deployment containerPort at the chart default while
    ``service.targetPort`` already carries the real listen port — honor that too.
    """
    if manifest_port is not None and manifest_port != 80:
        return manifest_port
    if service_target_port is not None and service_target_port != 80:
        return service_target_port

    exposed = exposed_ports if exposed_ports is not None else inspect_image_exposed_ports(image)
    for preferred in _HTTP_PORT_PREFERENCE:
        if preferred in exposed:
            return preferred

    httpish = [port for port in exposed if port not in _NON_HTTP_PORTS]
    if httpish:
        return httpish[0]
    if exposed:
        return exposed[0]
    return service_target_port or manifest_port or 80


def _is_nginx_image(image: str) -> bool:
    return "nginx" in image.lower()


def _align_container_port_and_probes(
    container: dict[str, Any],
    *,
    listen_port: int,
    image: str,
) -> None:
    """Align containerPort + probes to the workload listen port.

    Scaffold charts ship HTTP GET probes tuned for nginx. Vite/Node (and similar)
    often accept TCP long before HTTP returns headers — kubelet then fails with
    ``context deadline exceeded (awaiting headers)`` and the Deployment never
    becomes Ready within the provision timeout. Non-nginx images therefore use
    ``tcpSocket`` (+ a long ``startupProbe``) so Ready tracks "listening", not
    "first HTTP compile finished".
    """
    ports = container.setdefault("ports", [])
    if not ports:
        ports.append({"name": "http", "containerPort": listen_port, "protocol": "TCP"})
    else:
        first = ports[0]
        if isinstance(first, dict):
            first["name"] = first.get("name") or "http"
            first["containerPort"] = listen_port
            first.setdefault("protocol", "TCP")

    use_tcp_probes = not _is_nginx_image(image)
    if use_tcp_probes:
        _apply_tcp_workload_probes(container, listen_port=listen_port)
        return

    for probe_key in ("readinessProbe", "livenessProbe", "startupProbe"):
        probe = container.get(probe_key)
        if not isinstance(probe, dict):
            continue
        http_get = probe.get("httpGet")
        if isinstance(http_get, dict):
            http_get["port"] = listen_port
            http_get.setdefault("path", "/")
            continue
        tcp = probe.get("tcpSocket")
        if isinstance(tcp, dict):
            tcp["port"] = listen_port


def _apply_tcp_workload_probes(container: dict[str, Any], *, listen_port: int) -> None:
    """Replace HTTP probes with TCP for non-nginx preview workloads."""
    startup = container.get("startupProbe")
    if not isinstance(startup, dict):
        startup = {}
        container["startupProbe"] = startup
    for key in ("httpGet", "exec", "grpc"):
        startup.pop(key, None)
    startup["tcpSocket"] = {"port": listen_port}
    startup["periodSeconds"] = max(int(startup.get("periodSeconds") or 0), 5)
    startup["timeoutSeconds"] = max(int(startup.get("timeoutSeconds") or 0), 3)
    # Non-nginx workloads can take longer to bind the TCP port (Node/Vite cold start + compilation).
    # Match the default worker timeout (240s) using periodSeconds=5.
    startup["failureThreshold"] = max(int(startup.get("failureThreshold") or 0), 48)
    startup.setdefault("successThreshold", 1)

    for probe_key, timings in (
        (
            "readinessProbe",
            {
                "initialDelaySeconds": 5,
                "periodSeconds": 5,
                "timeoutSeconds": 3,
                "failureThreshold": 12,
            },
        ),
        (
            "livenessProbe",
            {
                # Keep liveness disabled long enough for the startupProbe window to complete.
                "initialDelaySeconds": 120,
                "periodSeconds": 20,
                "timeoutSeconds": 3,
                "failureThreshold": 6,
            },
        ),
    ):
        probe = container.get(probe_key)
        if not isinstance(probe, dict):
            probe = {}
            container[probe_key] = probe
        for key in ("httpGet", "exec", "grpc"):
            probe.pop(key, None)
        probe["tcpSocket"] = {"port": listen_port}
        # Overwrite scaffold/Helm HTTP timings — keeping chart initialDelay (15+)
        # delays Ready after the startup probe already proved the port is open.
        probe.update(timings)
        probe.setdefault("successThreshold", 1)


def _parse_memory_to_mi(value: object) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Ki"):
            return max(1, int(raw[:-2]) // 1024)
        if raw.endswith("Mi"):
            return int(raw[:-2])
        if raw.endswith("Gi"):
            return int(raw[:-2]) * 1024
        if raw.endswith("M"):
            return int(raw[:-1])
        if raw.endswith("G"):
            return int(raw[:-1]) * 1024
        # Plain bytes
        return max(1, int(raw) // (1024 * 1024))
    except ValueError:
        return None


def _parse_cpu_to_millis(value: object) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("m"):
            return int(raw[:-1])
        return int(float(raw) * 1000)
    except ValueError:
        return None


def _ensure_non_nginx_runtime_resources(container: dict[str, Any], *, image: str) -> None:
    """Raise scaffold memory/CPU floors for Node/Vite images so probes stay healthy.

    The nginx scaffold defaults (128Mi/256Mi) OOM-kill Vite/dev servers (exit 137),
    which makes Open app hang or appear to serve a stale nginx preview on another port.
    """
    if _is_nginx_image(image):
        return
    resources = container.setdefault("resources", {})
    requests = resources.setdefault("requests", {})
    limits = resources.setdefault("limits", {})

    req_mem = _parse_memory_to_mi(requests.get("memory"))
    lim_mem = _parse_memory_to_mi(limits.get("memory"))
    if req_mem is None or req_mem < 256:
        requests["memory"] = "256Mi"
    if lim_mem is None or lim_mem < 768:
        limits["memory"] = "768Mi"

    req_cpu = _parse_cpu_to_millis(requests.get("cpu"))
    lim_cpu = _parse_cpu_to_millis(limits.get("cpu"))
    if req_cpu is None or req_cpu < 100:
        requests["cpu"] = "100m"
    if lim_cpu is None or lim_cpu < 500:
        limits["cpu"] = "500m"

def patch_manifest_documents(
    documents: list[dict[str, Any]],
    *,
    target_namespace: str,
    environment_id: str,
    name: str,
    git_branch: str,
    git_repo_url: str,
    ttl_expires_at: str,
    owner_label: str,
    image: str,
) -> list[dict[str, Any]]:
    labels = build_preview_labels(
        environment_id=environment_id,
        name=name,
        git_branch=git_branch,
        git_repo_url=git_repo_url,
        ttl_expires_at=ttl_expires_at,
        owner_label=owner_label,
    )

    manifest_port: int | None = None
    for doc in documents:
        if doc.get("kind") != "Deployment":
            continue
        containers = (
            ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
        ).get("containers") or []
        if containers and isinstance(containers[0], dict):
            manifest_port = _manifest_container_port(containers[0])
            break
    service_target_port = _manifest_service_target_port(documents)
    listen_port = resolve_workload_listen_port(
        image=image,
        manifest_port=manifest_port,
        service_target_port=service_target_port,
    )
    if listen_port != (manifest_port or 80):
        logger.info(
            "manifest_listen_port_resolved",
            image=image,
            manifest_port=manifest_port,
            service_target_port=service_target_port,
            listen_port=listen_port,
        )

    patched: list[dict[str, Any]] = []
    for doc in documents:
        kind = doc.get("kind")
        # Namespace + preview governance are applied by KubernetesProvisioner.apply_governance.
        # Re-creating the same LimitRange/Quota names causes 409 Conflict rollbacks.
        if (
            kind == "Namespace"
            or _is_preview_governance_document(doc)
            or _is_preview_skipped_workload_document(doc)
        ):
            continue
        metadata = doc.setdefault("metadata", {})
        metadata["namespace"] = target_namespace
        meta_labels = metadata.setdefault("labels", {})
        meta_labels.update(labels)
        if kind == "Deployment":
            _patch_deployment(
                doc,
                image=image,
                git_branch=git_branch,
                git_repo_url=git_repo_url,
                listen_port=listen_port,
            )
        if kind == "Service":
            _patch_service_for_preview(doc, target_port=listen_port)
        patched.append(doc)
    return patched


def _is_preview_governance_document(doc: dict[str, Any]) -> bool:
    """Return True for scaffold objects already managed by apply_governance."""
    from app.services.k8s_spec import LIMIT_RANGE_NAME, QUOTA_NAME

    kind = str(doc.get("kind") or "")
    name = str((doc.get("metadata") or {}).get("name") or "").strip()
    if kind.lower() == "limitrange" and name == LIMIT_RANGE_NAME:
        return True
    if kind.lower() == "resourcequota" and name == QUOTA_NAME:
        return True
    return False


def _is_preview_skipped_workload_document(doc: dict[str, Any]) -> bool:
    """Skip chart objects that harm local kind previews (no metrics-server).

    HPAs without metrics.k8s.io spam kube-controller-manager and have contributed
    to CrashLoopBackOff / stalled Deployment rollouts on the Launchpad kind cluster.
    """
    kind = str(doc.get("kind") or "")
    return kind in {"HorizontalPodAutoscaler", "VerticalPodAutoscaler"}


# nginx:*-alpine non-root UID/GID (matches KubernetesProvisioner preview profile).
_NGINX_NON_ROOT_UID = 101


def _patch_deployment(
    doc: dict[str, Any],
    *,
    image: str,
    git_branch: str,
    git_repo_url: str,
    listen_port: int = 80,
) -> None:
    from app.services.k8s_spec import preview_workload_selector

    spec = doc.setdefault("spec", {})
    spec["replicas"] = 1
    # RollingUpdate keeps the previous Ready pod until the new revision passes
    # probes. Recreate left ready=0 during kind controller-manager blips and
    # caused false Ready timeouts; wait_for_workload_ready still requires the
    # *updated* revision + expected image before succeeding.
    spec["strategy"] = {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
    }
    # Keep selector stable across raw↔Helm switches. Extra Helm labels may stay on
    # the pod template, but matchLabels must remain Launchpad's immutable contract.
    selector_labels = preview_workload_selector()
    selector = spec.setdefault("selector", {})
    selector["matchLabels"] = dict(selector_labels)
    template = spec.setdefault("template", {})
    template_meta = template.setdefault("metadata", {})
    template_labels = template_meta.setdefault("labels", {})
    template_labels.update(selector_labels)
    template_labels.setdefault("app", APP_NAME)
    template_labels["launchpad.io/managed-by"] = "launchpad-idp"
    pod_spec = template.setdefault("spec", {})
    containers = pod_spec.setdefault("containers", [])
    if not containers:
        containers.append({"name": APP_NAME})
    container = containers[0]
    container["name"] = container.get("name") or APP_NAME
    container["image"] = image
    tag = image.rsplit(":", 1)[-1] if ":" in image.split("@", 1)[0] else "latest"
    if "@" in image or tag == "latest":
        container["imagePullPolicy"] = "Always"
    else:
        container["imagePullPolicy"] = "IfNotPresent"
    env = {item["name"]: item for item in container.get("env", []) if "name" in item}
    env["GIT_REPO_URL"] = {"name": "GIT_REPO_URL", "value": git_repo_url}
    env["GIT_BRANCH"] = {"name": "GIT_BRANCH", "value": git_branch}
    env["PORT"] = {"name": "PORT", "value": str(listen_port)}
    # Vite / many Node servers bind 127.0.0.1 unless HOST is set — probes use the pod IP.
    if not _is_nginx_image(image):
        env.setdefault("HOST", {"name": "HOST", "value": "0.0.0.0"})
        env.setdefault("APP_PORT", {"name": "APP_PORT", "value": str(listen_port)})
    container["env"] = list(env.values())
    _align_container_port_and_probes(container, listen_port=listen_port, image=image)
    _ensure_non_nginx_runtime_resources(container, image=image)
    _ensure_non_root_user(pod_spec, container, image=image)
    _strip_nginx_only_mounts(pod_spec, container, image=image)


def _strip_nginx_only_mounts(
    pod_spec: dict[str, Any],
    container: dict[str, Any],
    *,
    image: str,
) -> None:
    """Ensure custom images are not restricted by nginx readOnlyRootFilesystem scaffold."""
    if _is_nginx_image(image):
        return
    container_sec = container.setdefault("securityContext", {})
    container_sec["readOnlyRootFilesystem"] = False


def _ensure_non_root_user(
    pod_spec: dict[str, Any],
    container: dict[str, Any],
    *,
    image: str,
) -> None:
    """Avoid CreateContainerConfigError when runAsNonRoot is set without runAsUser.

    Workspace scaffolds historically set ``runAsNonRoot: true`` without a UID.
    ``nginx`` images still declare USER root, so kubelet rejects the pod until an
    explicit UID is present. Non-nginx images keep their own USER — do not invent
    nginx's UID 101 for them.
    """
    pod_security = pod_spec.setdefault("securityContext", {})
    container_security = container.setdefault("securityContext", {})
    wants_non_root = bool(
        pod_security.get("runAsNonRoot") or container_security.get("runAsNonRoot")
    )
    if not wants_non_root:
        return

    image_l = image.lower()
    if "http-echo" in image_l:
        pod_security.pop("runAsNonRoot", None)
        pod_security.pop("runAsUser", None)
        pod_security.pop("runAsGroup", None)
        container_security.pop("runAsNonRoot", None)
        container_security.pop("runAsUser", None)
        return

    if "nginx" in image_l:
        if pod_security.get("runAsUser") is None:
            pod_security["runAsUser"] = _NGINX_NON_ROOT_UID
        if pod_security.get("runAsGroup") is None:
            pod_security["runAsGroup"] = _NGINX_NON_ROOT_UID
        pod_security["runAsNonRoot"] = True
        if container_security.get("runAsUser") is None:
            container_security["runAsUser"] = _NGINX_NON_ROOT_UID
        container_security["runAsNonRoot"] = True
        return

    # Foreign images: drop nginx-scaffold hardening (UID 101 / read-only root).
    if pod_security.get("runAsUser") == _NGINX_NON_ROOT_UID:
        pod_security.pop("runAsUser", None)
        pod_security.pop("runAsGroup", None)
    if container_security.get("runAsUser") == _NGINX_NON_ROOT_UID:
        container_security.pop("runAsUser", None)
    if pod_security.get("runAsUser") is None:
        pod_security.pop("runAsNonRoot", None)
    if container_security.get("runAsUser") is None:
        container_security.pop("runAsNonRoot", None)
    if container_security.get("readOnlyRootFilesystem") is True:
        container_security.pop("readOnlyRootFilesystem", None)


def _patch_service_for_preview(doc: dict[str, Any], *, target_port: int = 80) -> None:
    """Normalize Service ports; leave ClusterIP until a mapped NodePort is assigned.

    Creating the Service as NodePort without an explicit ``nodePort`` lets the API
    auto-assign a high port (e.g. 31196) outside PREVIEW_NODE_PORT_MIN/MAX. That
    port is not forwarded by kind, so Open app fails while catalog previews work.
    """
    spec = doc.setdefault("spec", {})
    spec["type"] = "ClusterIP"
    ports = spec.setdefault("ports", [])
    if not ports:
        ports.append(
            {
                "name": "http",
                "port": 80,
                "targetPort": target_port,
                "protocol": "TCP",
            }
        )
    port = ports[0]
    if isinstance(port, dict):
        port["name"] = port.get("name") or "http"
        port.setdefault("port", 80)
        port["targetPort"] = target_port
        port.setdefault("protocol", "TCP")
        port.pop("nodePort", None)


class ManifestDeployer:
    """Deploy workspace manifests into a preview namespace."""

    def __init__(
        self,
        settings: Settings | None = None,
        provisioner: KubernetesProvisioner | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provisioner = provisioner or KubernetesProvisioner(self._settings)

    def deploy(
        self,
        *,
        workspace_root: Path,
        namespace: str,
        environment_id: str,
        name: str,
        git_branch: str,
        git_repo_url: str,
        ttl_expires_at: str,
        owner_label: str = "launchpad",
        image: str | None = None,
    ) -> ProvisionedResources:
        workload_image = image or self._settings.default_workload_image
        labels = build_preview_labels(
            environment_id=environment_id,
            name=name,
            git_branch=git_branch,
            git_repo_url=git_repo_url,
            ttl_expires_at=ttl_expires_at,
            owner_label=owner_label,
        )
        resources = ProvisionedResources(
            namespace=namespace,
            labels=labels,
            image=workload_image,
        )

        if not workspace_has_deployable_k8s(workspace_root):
            raise FileNotFoundError(
                "No deployable Kubernetes workload found. Expected "
                f"{K8S_MANIFESTS_DIR.as_posix()}/*.yaml or "
                f"{HELM_CHART_DIR.as_posix()}/Chart.yaml "
                f"in workspace {workspace_root}"
            )

        if not self._settings.kubernetes_enabled:
            logger.info(
                "manifest_deploy_simulated",
                namespace=namespace,
                environment_id=environment_id,
                workspace_root=str(workspace_root),
                source="helm" if workspace_has_helm_chart(workspace_root) else "raw",
            )
            resources.created_namespace = True
            resources.created_quota = True
            resources.created_limit_range = True
            resources.created_network_policy = True
            resources.created_workload = True
            resources.simulated = True
            documents = load_workspace_manifest_documents(
                workspace_root,
                namespace=namespace,
            )
            resources.image = resolve_manifest_workload_image(
                documents,
                provided_image=image,
                default_image=self._settings.default_workload_image,
            )
            resources.preview_url = self._provisioner._portal_preview_url(
                environment_id=environment_id
            )
            return resources

        documents = load_workspace_manifest_documents(
            workspace_root,
            namespace=namespace,
        )
        workload_image = resolve_manifest_workload_image(
            documents,
            provided_image=image,
            default_image=self._settings.default_workload_image,
        )
        resources.image = workload_image
        patched = patch_manifest_documents(
            documents,
            target_namespace=namespace,
            environment_id=environment_id,
            name=name,
            git_branch=git_branch,
            git_repo_url=git_repo_url,
            ttl_expires_at=ttl_expires_at,
            owner_label=owner_label,
            image=workload_image,
        )
        listen_port = 80
        for doc in patched:
            if doc.get("kind") != "Deployment":
                continue
            containers = (
                ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
            ).get("containers") or []
            if containers and isinstance(containers[0], dict):
                listen_port = _manifest_container_port(containers[0]) or listen_port
                break

        self._provisioner.apply_governance(
            namespace=namespace,
            labels=labels,
            resources=resources,
            listen_ports=[listen_port],
        )
        self._strip_preview_incompatible_controllers(namespace=namespace)
        self._apply_documents(namespace=namespace, documents=patched)
        resources.created_workload = True

        used_ports = self._provisioner._list_allocated_node_ports(exclude_namespace=namespace)
        existing_port = self._provisioner._read_namespaced_app_node_port(namespace)
        node_port = resolve_preview_node_port(
            environment_id,
            existing_port=existing_port,
            port_min=self._settings.preview_node_port_min,
            port_max=self._settings.preview_node_port_max,
            used_ports=used_ports,
        )
        # Always pin the Service into the kind-mapped range (fixes auto-assigned ports).
        node_port = self._assign_node_port(
            namespace=namespace,
            node_port=node_port,
            used_ports=used_ports,
            labels=labels,
            target_port=listen_port,
        )
        resources.node_port = node_port

        if self._settings.preview_ingress_host_template:
            host = self._settings.preview_ingress_host_template.format(
                name=name,
                environment_id=environment_id,
                namespace=namespace,
            )
            self._provisioner._apply_ingress(namespace=namespace, labels=labels, host=host)
            resources.preview_url = f"http://{host}"
        else:
            resources.preview_url = self._provisioner._node_port_preview_url(node_port=node_port)

        ready_timeout = self._settings.kubernetes_ready_timeout_seconds
        # Non-nginx images (pull + cold Node/Vite bind) need more headroom than nginx.
        # Keep probe windows in sync with the worker's ready timeout.
        if not _is_nginx_image(workload_image):
            ready_timeout = max(ready_timeout, 240.0)
        try:
            self._provisioner.wait_for_workload_ready(
                namespace=namespace,
                timeout_seconds=ready_timeout,
                expected_image=workload_image,
            )
        except TimeoutError as exc:
            # Attach resolved resources for the worker so it can persist preview_url/node_port
            # even when readiness times out.
            setattr(exc, "provisioned_resources", resources)
            raise
        return resources

    def _strip_preview_incompatible_controllers(self, *, namespace: str) -> None:
        """Remove leftover HPA/VPA that stall kind control-plane reconciliation."""
        self._provisioner.delete_namespaced_hpa(namespace=namespace)

    def _apply_documents(self, *, namespace: str, documents: list[dict[str, Any]]) -> None:
        from kubernetes import client
        from kubernetes.client.rest import ApiException
        from kubernetes.utils import FailToCreateError, create_from_dict

        api_client = client.ApiClient()
        for doc in documents:
            kind = doc.get("kind")
            name = (doc.get("metadata") or {}).get("name")
            # Defense in depth: apply_governance owns these names; never re-create.
            if kind == "Namespace" or _is_preview_governance_document(doc):
                logger.info(
                    "manifest_governance_skipped",
                    namespace=namespace,
                    kind=kind,
                    name=name,
                )
                continue
            if _is_preview_skipped_workload_document(doc):
                logger.info(
                    "manifest_preview_incompatible_skipped",
                    namespace=namespace,
                    kind=kind,
                    name=name,
                )
                continue
            try:
                if _requires_dynamic_apply(doc):
                    applied = self._create_via_dynamic(
                        api_client=api_client,
                        doc=doc,
                        namespace=namespace,
                    )
                    if not applied:
                        continue
                else:
                    create_from_dict(
                        k8s_client=api_client,
                        data=doc,
                        namespace=namespace,
                        verbose=False,
                    )
            except FailToCreateError as exc:
                if not _all_already_exist(exc):
                    raise
                self._handle_already_exists(
                    api_client=api_client,
                    doc=doc,
                    namespace=namespace,
                )
            except ApiException as exc:
                if not _is_already_exists_status(getattr(exc, "status", None), exc):
                    raise
                self._handle_already_exists(
                    api_client=api_client,
                    doc=doc,
                    namespace=namespace,
                )
            logger.info(
                "manifest_resource_applied",
                namespace=namespace,
                kind=kind,
                name=name,
            )

    def _create_via_dynamic(
        self,
        *,
        api_client: object,
        doc: dict[str, Any],
        namespace: str,
    ) -> bool:
        """Create a CRD-backed object via DynamicClient.

        Returns False when an optional CRD is missing from the cluster (skip).
        """
        from kubernetes.client.rest import ApiException
        from kubernetes.dynamic import DynamicClient
        from kubernetes.dynamic.exceptions import ResourceNotFoundError

        kind = str(doc.get("kind") or "")
        name = (doc.get("metadata") or {}).get("name")
        api_version = str(doc.get("apiVersion") or "")
        dyn = DynamicClient(api_client)
        try:
            resource = dyn.resources.get(api_version=api_version, kind=kind)
        except (ResourceNotFoundError, KeyError, ValueError) as exc:
            if _is_optional_crd(doc):
                logger.warning(
                    "manifest_optional_crd_unavailable",
                    namespace=namespace,
                    kind=kind,
                    name=name,
                    api_version=api_version,
                    error=str(exc),
                )
                return False
            raise

        try:
            resource.create(body=doc, namespace=namespace)
        except ApiException as exc:
            if getattr(exc, "status", None) == 404 and _is_optional_crd(doc):
                logger.warning(
                    "manifest_optional_crd_unavailable",
                    namespace=namespace,
                    kind=kind,
                    name=name,
                    api_version=api_version,
                    error=str(exc),
                )
                return False
            raise
        return True

    def _handle_already_exists(
        self,
        *,
        api_client: object,
        doc: dict[str, Any],
        namespace: str,
    ) -> None:
        """Idempotent create follow-up: skip platform governance, else patch/replace."""
        kind = doc.get("kind")
        name = (doc.get("metadata") or {}).get("name")
        if _is_preview_governance_document(doc):
            logger.info(
                "manifest_governance_already_exists",
                namespace=namespace,
                kind=kind,
                name=name,
            )
            return
        self._replace_document(api_client=api_client, doc=doc, namespace=namespace)

    def _replace_document(
        self,
        *,
        api_client: object,
        doc: dict[str, Any],
        namespace: str,
    ) -> None:
        from kubernetes.client.rest import ApiException
        from kubernetes.dynamic import DynamicClient

        dyn = DynamicClient(api_client)
        api_version = doc.get("apiVersion", "")
        kind = str(doc.get("kind") or "")
        name = doc.get("metadata", {}).get("name")
        resource = dyn.resources.get(api_version=api_version, kind=kind)
        # Prefer patch/server-side apply semantics so we do not need resourceVersion.
        patch_error: Exception | None = None
        try:
            resource.patch(
                body=doc,
                name=name,
                namespace=namespace,
                content_type="application/merge-patch+json",
            )
            return
        except Exception as exc:
            patch_error = exc

        try:
            resource.replace(body=doc, name=name, namespace=namespace)
            return
        except Exception as replace_error:
            if _should_recreate_immutable_resource(kind, replace_error) or (
                patch_error is not None
                and _should_recreate_immutable_resource(kind, patch_error)
            ):
                logger.info(
                    "manifest_resource_recreate_immutable",
                    namespace=namespace,
                    kind=kind,
                    name=name,
                    error=str(replace_error)[:300],
                )
                self._delete_and_recreate_document(
                    resource=resource,
                    doc=doc,
                    name=str(name),
                    namespace=namespace,
                )
                return
            if patch_error is not None:
                raise replace_error from patch_error
            raise

    def _delete_and_recreate_document(
        self,
        *,
        resource: object,
        doc: dict[str, Any],
        name: str,
        namespace: str,
    ) -> None:
        import time

        from kubernetes.client.rest import ApiException

        try:
            resource.delete(name=name, namespace=namespace)  # type: ignore[attr-defined]
        except ApiException as exc:
            if exc.status != 404:
                raise

        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                resource.get(name=name, namespace=namespace)  # type: ignore[attr-defined]
            except ApiException as exc:
                if exc.status == 404:
                    break
                raise
            time.sleep(0.4)
        else:
            raise TimeoutError(
                f"Timed out waiting for {doc.get('kind')}/{name} deletion in {namespace}"
            )

        resource.create(body=doc, namespace=namespace)  # type: ignore[attr-defined]

    def _assign_node_port(
        self,
        *,
        namespace: str,
        node_port: int,
        used_ports: set[int],
        labels: dict[str, str] | None = None,
        target_port: int = 80,
    ) -> int:
        """Pin Service ``app`` to an explicit NodePort in the kind-mapped range.

        Always creates a clean Service body (never reuse a read object's
        resourceVersion/uid/clusterIP). Changing ``nodePort`` requires
        delete+recreate. Returns the applied port.
        """
        assert self._provisioner._core is not None
        from kubernetes import client
        from kubernetes.client.rest import ApiException

        from app.services.k8s_spec import preview_workload_selector
        from app.services.kubernetes import _is_node_port_allocated_error

        candidates = [node_port]
        port_min = self._settings.preview_node_port_min
        port_max = self._settings.preview_node_port_max
        for port in range(port_min, port_max + 1):
            if port not in candidates and port not in used_ports:
                candidates.append(port)

        selector = preview_workload_selector()
        svc_labels = dict(labels or {})
        svc_labels.setdefault("app", APP_NAME)
        svc_labels.setdefault("launchpad.io/managed-by", "launchpad-idp")

        last_error: ApiException | None = None
        for candidate in candidates:
            try:
                existing = self._provisioner._core.read_namespaced_service(APP_NAME, namespace)
            except ApiException as exc:
                if exc.status != 404:
                    raise
                existing = None

            existing_target: int | None = None
            if (
                existing is not None
                and existing.spec is not None
                and existing.spec.ports
                and existing.spec.ports[0].target_port is not None
            ):
                raw_target = existing.spec.ports[0].target_port
                try:
                    existing_target = int(raw_target)
                except (TypeError, ValueError):
                    existing_target = None

            if (
                existing is not None
                and existing.spec is not None
                and existing.spec.type == "NodePort"
                and existing.spec.ports
                and existing.spec.ports[0].node_port is not None
                and int(existing.spec.ports[0].node_port) == candidate
                and existing_target == target_port
            ):
                return candidate

            service = client.V1Service(
                metadata=client.V1ObjectMeta(
                    name=APP_NAME,
                    namespace=namespace,
                    labels=svc_labels,
                ),
                spec=client.V1ServiceSpec(
                    selector=selector,
                    ports=[
                        client.V1ServicePort(
                            name="http",
                            port=80,
                            target_port=target_port,
                            protocol="TCP",
                            node_port=candidate,
                        )
                    ],
                    type="NodePort",
                ),
            )

            try:
                if existing is not None:
                    try:
                        self._provisioner._core.delete_namespaced_service(APP_NAME, namespace)
                    except ApiException as delete_exc:
                        if delete_exc.status != 404:
                            raise
                self._provisioner._core.create_namespaced_service(namespace, service)
                return candidate
            except ApiException as exc:
                last_error = exc
                if _is_node_port_allocated_error(exc):
                    used_ports.add(candidate)
                    continue
                # Non-collision create failures after delete must not soft-return —
                # otherwise Open app points at a dead mapped port with no Service.
                used_ports.add(candidate)
                continue
        if last_error is not None:
            raise last_error
        raise RuntimeError(
            f"Failed to assign NodePort for Service/{APP_NAME} in {namespace}"
        )


def _should_recreate_immutable_resource(kind: str, exc: BaseException) -> bool:
    """Detect Kubernetes immutable-field conflicts (e.g. Deployment spec.selector)."""
    kind_l = kind.lower()
    if kind_l not in {"deployment", "statefulset", "daemonset", "job"}:
        return False
    status = getattr(exc, "status", None)
    body = str(getattr(exc, "body", "") or exc).lower()
    if "immutable" in body or "field is immutable" in body:
        return True
    return status == 422 and "selector" in body


def _all_already_exist(exc: Exception) -> bool:
    """True when kubernetes.utils.FailToCreateError only contains AlreadyExists (409)."""
    api_exceptions = getattr(exc, "api_exceptions", None)
    if not api_exceptions:
        return _is_already_exists_status(None, exc)
    for item in api_exceptions:
        if not _is_already_exists_status(getattr(item, "status", None), item):
            return False
    return True


def _is_already_exists_status(status: object, exc: object | None = None) -> bool:
    """Accept int/str 409 or an AlreadyExists create conflict from ApiException-like objects."""
    if status == 409 or status == "409":
        return True
    try:
        if status is not None and int(str(status)) == 409:
            return True
    except (TypeError, ValueError):
        pass

    reason = getattr(exc, "reason", None) if exc is not None else None
    body = getattr(exc, "body", None) if exc is not None else None
    text = " ".join(
        part
        for part in (str(reason or ""), body if isinstance(body, str) else str(body or ""), str(exc or ""))
        if part
    )
    return "AlreadyExists" in text or "already exists" in text.lower()

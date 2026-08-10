from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.k8s_spec import (
    QUOTA_NAME,
    build_preview_labels,
    build_preview_limit_range,
    build_preview_network_policy,
    build_preview_resource_quota,
    preview_workload_selector,
    sanitize_label,
)

logger = get_logger(__name__)


class PreviewCancelled(Exception):
    """Raised when an in-flight provision is cancelled by a delete request."""


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


def _is_nginx_image(image: str) -> bool:
    return "nginx" in image.lower()


def _inspect_image_exposed_ports(image: str) -> list[int]:
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
        # Image may not be local yet - best-effort pull then re-inspect.
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


def _resolve_listen_port_from_image(image: str) -> int:
    """Best-effort resolve the container's HTTP-like listen port."""
    image_l = image.lower()
    if "http-echo" in image_l:
        return 80
    if "web-ui" in image_l or "frontend" in image_l or "next" in image_l or "nuxt" in image_l:
        return 3000
    if "api-server" in image_l or "backend" in image_l or "express" in image_l:
        return 8080

    exposed_ports = _inspect_image_exposed_ports(image)
    for preferred in _HTTP_PORT_PREFERENCE:
        if preferred in exposed_ports:
            return preferred

    httpish = [port for port in exposed_ports if port not in _NON_HTTP_PORTS]
    if httpish:
        return httpish[0]
    if exposed_ports:
        return exposed_ports[0]
    return 80


# Hard wall-clock cap on how long a preview may sit in PROVISIONING before it is
# failed with diagnostics (3 minutes). Real failures (ImagePullBackOff /
# CrashLoopBackOff) fast-fail well before this; the cap bounds the pathological
# "spinning forever" case.
PREVIEW_READY_TIMEOUT_CAP_SECONDS = 180.0


def _workload_ready_timeout_seconds(*, image: str, base_timeout_seconds: float) -> float:
    # Non-nginx images (pull + cold Node/Vite bind) need more headroom than nginx,
    # but never exceed the 3-minute preview cap.
    if not _is_nginx_image(image):
        return min(max(base_timeout_seconds, 120.0), PREVIEW_READY_TIMEOUT_CAP_SECONDS)
    return min(base_timeout_seconds, PREVIEW_READY_TIMEOUT_CAP_SECONDS)


def _read_or_none(read, *args, **kwargs):
    """Return the resource, or None when the Kubernetes API responds 404.

    Collapses the ``try: read() except ApiException: if 404 -> None else raise``
    idiom. Non-404 errors propagate unchanged.
    """
    from kubernetes.client.rest import ApiException

    try:
        return read(*args, **kwargs)
    except ApiException as exc:
        if exc.status == 404:
            return None
        raise


def _ignore_404(call, *args, **kwargs) -> None:
    """Invoke a delete/mutate call, swallowing a 404 (resource already gone)."""
    from kubernetes.client.rest import ApiException

    try:
        call(*args, **kwargs)
    except ApiException as exc:
        if exc.status != 404:
            raise


@dataclass
class ProvisionedResources:
    namespace: str
    preview_url: str | None = None
    created_namespace: bool = False
    created_quota: bool = False
    created_limit_range: bool = False
    created_network_policy: bool = False
    created_workload: bool = False
    simulated: bool = False
    node_port: int | None = None
    image: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    # User-facing note (e.g. Compose host port remapped because preferred was busy).
    notice: str | None = None
    # Exposed preview endpoints (frontend first). Open-app uses preview_url.
    preview_endpoints: list[dict[str, object]] = field(default_factory=list)


class KubernetesProvisioner:
    """Idempotent namespace + ResourceQuota + NetworkPolicy + workload provisioner."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._core = None
        self._networking = None
        self._apps = None
        self._autoscaling = None
        if self._settings.kubernetes_enabled:
            self._load_clients()

    @property
    def clients_ready(self) -> bool:
        """True when the API clients loaded (kubeconfig reachable). False when
        Kubernetes is disabled or client loading failed, so callers can skip live
        cluster reads instead of asserting/crashing."""
        return self._core is not None and self._apps is not None

    def _load_clients(self) -> None:
        from kubernetes import client, config

        self._core = None
        self._networking = None
        self._apps = None
        self._autoscaling = None

        if self._settings.kubernetes_in_cluster:
            try:
                config.load_incluster_config()
            except Exception as exc:
                logger.warning("kubernetes_in_cluster_load_failed", error=str(exc))
                return
        else:
            load_kwargs: dict[str, object] = {}
            if self._settings.kubernetes_kubeconfig_path:
                load_kwargs["config_file"] = self._settings.kubernetes_kubeconfig_path
            requested_ctx = self._settings.resolved_kubernetes_context
            if requested_ctx:
                load_kwargs["context"] = requested_ctx
            try:
                config.load_kube_config(**load_kwargs)
            except Exception as exc:
                # Never fall back to kubectl current-context when a specific context
                # was requested. That previously routed local launches at a remote
                # GKE API (ConnectTimeout) when kind/k3d was down or misnamed.
                logger.warning(
                    "kubeconfig_context_load_failed",
                    context=requested_ctx,
                    error=str(exc),
                )
                if requested_ctx:
                    return
                try:
                    config.load_kube_config(
                        **{k: v for k, v in load_kwargs.items() if k != "context"}
                    )
                except Exception as exc2:
                    logger.warning("kubeconfig_fallback_load_failed", error=str(exc2))
                    return

        try:
            configuration = client.Configuration.get_default_copy()
            # Fail fast instead of hanging forever (urllib3 default timeout=None).
            configuration.retries = 1
            client.Configuration.set_default(configuration)
            self._core = client.CoreV1Api()
            self._networking = client.NetworkingV1Api()
            self._apps = client.AppsV1Api()
            self._autoscaling = client.AutoscalingV2Api()
        except Exception as exc:
            logger.warning("kubernetes_client_init_failed", error=str(exc))
            self._core = None
            self._networking = None
            self._apps = None
            self._autoscaling = None

    def assert_cluster_ready(self, *, timeout_seconds: float = 5.0) -> None:
        """Verify the configured kube-context is reachable before APPLY.

        Raises ``RuntimeError`` with an actionable message when the local cluster
        context is missing or the API server is unreachable (e.g. stale GKE current-context).
        """
        if not self._settings.kubernetes_enabled:
            return
        ctx = self._settings.resolved_kubernetes_context or "default"
        engine = self._settings.local_k8s_engine
        up_hint = f"make {engine}-up" if engine in {"k3s", "kind"} else "make k3s-up"
        if self._core is None:
            raise RuntimeError(
                f"Kubernetes client not connected (context={ctx}). "
                f"Start the local cluster with `{up_hint}` and ensure kubectl context "
                f"'{ctx}' exists. Do not leave kubectl on a remote GKE context for local previews."
            )
        try:
            self._core.list_namespace(limit=1, _request_timeout=timeout_seconds)
        except Exception as exc:
            raise RuntimeError(
                f"Cannot reach Kubernetes API for context '{ctx}' "
                f"(timed out or refused after {timeout_seconds:.0f}s): {exc}. "
                f"For local previews run `{up_hint}` and set KUBERNETES_CONTEXT={ctx} "
                f"(or unset a remote KUBERNETES_CONTEXT)."
            ) from exc

    def reload_clients(self) -> None:
        """Re-read kubeconfig (e.g. after auto-managing the local cluster)."""
        if self._settings.kubernetes_enabled:
            self._load_clients()

    def provision(
        self,
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
    ) -> ProvisionedResources:
        workload_image = image or self._settings.default_workload_image
        if not (workload_image or "").strip():
            raise ValueError(
                "workload image is required (set workload_image or DEFAULT_WORKLOAD_IMAGE)"
            )
        listen_port = _resolve_listen_port_from_image(workload_image)
        ready_timeout = _workload_ready_timeout_seconds(
            image=workload_image, base_timeout_seconds=self._settings.kubernetes_ready_timeout_seconds
        )
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

        if not self._settings.kubernetes_enabled:
            logger.info(
                "kubernetes_simulate_provision",
                namespace=namespace,
                environment_id=environment_id,
                git_repo_url=git_repo_url,
                git_branch=git_branch,
                enable_postgres=enable_postgres,
                enable_redis=enable_redis,
            )
            resources.created_namespace = True
            resources.created_quota = True
            resources.created_limit_range = True
            resources.created_network_policy = True
            resources.created_workload = True
            resources.simulated = True
            resources.preview_url = self.portal_preview_url(environment_id=environment_id)
            return resources

        self.apply_governance(namespace=namespace, labels=labels, resources=resources)

        # Ephemeral datastores (and their connection Secret) must be applied AFTER
        # the namespace/governance exist but BEFORE the app workload, so the app's
        # init-containers and app-secrets envFrom resolve on first start. Applying
        # them here (rather than before provision) fixes a 404 where the Secret was
        # written into a namespace that had not been created yet.
        self.apply_ephemeral_datastores(
            namespace=namespace,
            name=name,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
        )

        used_ports = self.list_allocated_node_ports(exclude_namespace=namespace)
        # Prefer a sticky in-range NodePort; ignore API auto-assigned ports outside the
        # kind-mapped PREVIEW_NODE_PORT_MIN/MAX window (those are unreachable from the host).
        existing_port = self.read_namespaced_app_node_port(namespace)
        node_port = resolve_preview_node_port(
            environment_id,
            existing_port=existing_port,
            port_min=self._settings.preview_node_port_min,
            port_max=self._settings.preview_node_port_max,
            used_ports=used_ports,
            cluster_name=self._settings.kubernetes_context,
        )
        node_port = self._apply_workload(
            namespace=namespace,
            labels=labels,
            git_branch=git_branch,
            git_repo_url=git_repo_url,
            image=workload_image,
            listen_port=listen_port,
            node_port=node_port,
            used_ports=used_ports,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
        )
        resources.created_workload = True
        resources.node_port = node_port

        host = self.workspace_preview_host(
            name=name, environment_id=environment_id, namespace=namespace
        )
        if host:
            self.apply_ingress(namespace=namespace, labels=labels, host=host)
            resources.preview_url = self.ingress_preview_url(host=host)
        else:
            resources.preview_url = self.node_port_preview_url(node_port=node_port)

        self.wait_for_workload_ready(
            namespace=namespace,
            timeout_seconds=ready_timeout,
            expected_image=workload_image,
        )
        return resources

    def apply_ephemeral_datastores(
        self,
        *,
        namespace: str,
        name: str,
        enable_postgres: bool = False,
        enable_redis: bool = False,
    ) -> None:
        """Provision opt-in ephemeral Postgres and/or Redis in-cluster workloads with connection secret."""
        if not (enable_postgres or enable_redis):
            return

        from app.schemas.cloud import (
            DataStoreDependency,
            DependencyPlacement,
            WorkloadDependenciesConfig,
        )
        from app.services.workload_dependencies import (
            DataStoreKind,
            dependency_secret_string_data,
            in_cluster_manifest_files,
        )

        kinds: list[DataStoreKind] = []
        deps = WorkloadDependenciesConfig()
        if enable_postgres:
            kinds.append(DataStoreKind.POSTGRES)
            deps.postgres = DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER)
        if enable_redis:
            kinds.append(DataStoreKind.REDIS)
            deps.redis = DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER)

        if not self._settings.kubernetes_enabled:
            logger.info(
                "kubernetes_simulate_datastores",
                namespace=namespace,
                postgres=enable_postgres,
                redis=enable_redis,
            )
            return

        secret_data = dependency_secret_string_data(deps, name=name)
        if secret_data:
            self._apply_secret_dict(namespace=namespace, secret_name="app-secrets", data=secret_data)
            if name and name != "app":
                self._apply_secret_dict(namespace=namespace, secret_name=f"{name}-secrets", data=secret_data)

        manifests = in_cluster_manifest_files(ns=namespace, name=name, kinds=kinds)
        import yaml
        from kubernetes import utils

        for filename, content in manifests.items():
            try:
                docs = list(yaml.safe_load_all(content))
                for doc in docs:
                    if isinstance(doc, dict) and doc.get("kind"):
                        utils.create_from_dict(self._core.api_client, doc, namespace=namespace)
            except Exception as exc:
                logger.warning("apply_datastore_manifest_failed", filename=filename, error=str(exc))

    def _apply_secret_dict(
        self,
        *,
        namespace: str,
        secret_name: str,
        data: dict[str, str],
    ) -> None:
        from kubernetes import client
        from kubernetes.client.rest import ApiException

        assert self._core is not None
        body = client.V1Secret(
            api_version="v1",
            kind="Secret",
            metadata=client.V1ObjectMeta(name=secret_name, namespace=namespace),
            string_data=data,
        )
        try:
            self._core.read_namespaced_secret(secret_name, namespace)
            self._core.patch_namespaced_secret(secret_name, namespace, body)
        except ApiException as exc:
            if exc.status == 404:
                self._core.create_namespaced_secret(namespace, body)
            else:
                raise


    def read_namespace_usage(self, namespace: str):
        """Return CPU/memory usage for cost metering.

        Prefers ResourceQuota ``status.used`` (requests). Falls back to summing
        pod container resource requests when the quota is missing.
        """
        from decimal import Decimal

        from app.services.cost_metering import (
            NamespaceUsage,
            parse_cpu_cores,
            parse_memory_gib,
        )

        if not self._settings.kubernetes_enabled or self._core is None:
            return None

        from kubernetes.client.rest import ApiException

        try:
            quota = self._core.read_namespaced_resource_quota(QUOTA_NAME, namespace)
            used = getattr(getattr(quota, "status", None), "used", None) or {}
            cpu = parse_cpu_cores(used.get("requests.cpu") or used.get("cpu"))
            mem = parse_memory_gib(used.get("requests.memory") or used.get("memory"))
            if cpu > 0 or mem > 0:
                return NamespaceUsage(cpu_cores=cpu, memory_gib=mem, source="usage_quota")
        except ApiException as exc:
            if exc.status != 404:
                logger.warning(
                    "kubernetes_quota_usage_failed",
                    namespace=namespace,
                    status=exc.status,
                )

        try:
            pods = self._core.list_namespaced_pod(namespace)
        except ApiException as exc:
            logger.warning(
                "kubernetes_pod_usage_failed",
                namespace=namespace,
                status=exc.status,
            )
            return None

        cpu_total = Decimal("0")
        mem_total = Decimal("0")
        for pod in pods.items or []:
            phase = (getattr(getattr(pod, "status", None), "phase", None) or "").lower()
            if phase in {"succeeded", "failed"}:
                continue
            for container in getattr(getattr(pod, "spec", None), "containers", None) or []:
                requests = getattr(getattr(container, "resources", None), "requests", None) or {}
                cpu_total += parse_cpu_cores(requests.get("cpu"))
                mem_total += parse_memory_gib(requests.get("memory"))

        if cpu_total <= 0 and mem_total <= 0:
            return NamespaceUsage(
                cpu_cores=Decimal("0"),
                memory_gib=Decimal("0"),
                source="usage_requests",
            )
        return NamespaceUsage(
            cpu_cores=cpu_total,
            memory_gib=mem_total,
            source="usage_requests",
        )

    def apply_governance(
        self,
        *,
        namespace: str,
        labels: dict[str, str],
        resources: ProvisionedResources,
        listen_ports: list[int] | None = None,
    ) -> None:
        """Create or update namespace governance objects shared by preview and manifest deploy."""
        if not self._settings.kubernetes_enabled:
            resources.created_namespace = True
            resources.created_quota = True
            resources.created_limit_range = True
            resources.created_network_policy = True
            return

        assert self._core is not None and self._networking is not None
        from kubernetes import client
        from kubernetes.client.rest import ApiException

        try:
            self._core.read_namespace(namespace, _request_timeout=10)
            logger.info("kubernetes_namespace_exists", namespace=namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
            body = client.V1Namespace(
                metadata=client.V1ObjectMeta(name=namespace, labels=labels),
            )
            self._core.create_namespace(body, _request_timeout=10)
            resources.created_namespace = True
            logger.info("kubernetes_namespace_created", namespace=namespace)

        quota_spec = build_preview_resource_quota(
            self._settings,
            namespace=namespace,
            labels=labels,
        )
        self._apply_namespaced_core_object(
            kind="resource_quota",
            namespace=namespace,
            body=quota_spec,
            created_attr="created_quota",
            resources=resources,
        )

        limit_range = build_preview_limit_range(namespace=namespace, labels=labels)
        self._apply_namespaced_core_object(
            kind="limit_range",
            namespace=namespace,
            body=limit_range,
            created_attr="created_limit_range",
            resources=resources,
        )

        policy = build_preview_network_policy(
            namespace=namespace,
            labels=labels,
            listen_ports=listen_ports,
        )
        self._apply_namespaced_networking_object(
            kind="network_policy",
            namespace=namespace,
            body=policy,
            created_attr="created_network_policy",
            resources=resources,
        )

        for legacy_name in ("deny-cross-namespace-egress", "deny-cross-namespace"):
            _ignore_404(self._networking.delete_namespaced_network_policy, legacy_name, namespace)

    def _apply_namespaced_core_object(
        self,
        *,
        kind: str,
        namespace: str,
        body: object,
        created_attr: str,
        resources: ProvisionedResources,
    ) -> None:
        """Create-or-replace a namespaced core object; treat 409 as replace."""
        assert self._core is not None
        from kubernetes.client.rest import ApiException

        name = getattr(getattr(body, "metadata", None), "name", None)
        if not isinstance(name, str) or not name:
            raise ValueError(f"governance {kind} missing metadata.name")
        read = getattr(self._core, f"read_namespaced_{kind}")
        create = getattr(self._core, f"create_namespaced_{kind}")
        replace = getattr(self._core, f"replace_namespaced_{kind}")
        try:
            existing = read(name, namespace)
            existing_meta = getattr(existing, "metadata", None)
            body_meta = getattr(body, "metadata", None)
            resource_version = getattr(existing_meta, "resource_version", None)
            if body_meta is not None and resource_version:
                body_meta.resource_version = resource_version
            replace(name, namespace, body)
        except ApiException as exc:
            if exc.status != 404:
                raise
            try:
                create(namespace, body)
                setattr(resources, created_attr, True)
            except ApiException as create_exc:
                if create_exc.status != 409:
                    raise
                existing = read(name, namespace)
                existing_meta = getattr(existing, "metadata", None)
                body_meta = getattr(body, "metadata", None)
                resource_version = getattr(existing_meta, "resource_version", None)
                if body_meta is not None and resource_version:
                    body_meta.resource_version = resource_version
                replace(name, namespace, body)

    def _apply_namespaced_networking_object(
        self,
        *,
        kind: str,
        namespace: str,
        body: object,
        created_attr: str,
        resources: ProvisionedResources,
    ) -> None:
        """Create-or-replace a namespaced networking object; treat 409 as replace."""
        assert self._networking is not None
        from kubernetes.client.rest import ApiException

        name = getattr(getattr(body, "metadata", None), "name", None)
        if not isinstance(name, str) or not name:
            raise ValueError(f"governance {kind} missing metadata.name")
        read = getattr(self._networking, f"read_namespaced_{kind}")
        create = getattr(self._networking, f"create_namespaced_{kind}")
        replace = getattr(self._networking, f"replace_namespaced_{kind}")
        try:
            existing = read(name, namespace)
            existing_meta = getattr(existing, "metadata", None)
            body_meta = getattr(body, "metadata", None)
            resource_version = getattr(existing_meta, "resource_version", None)
            if body_meta is not None and resource_version:
                body_meta.resource_version = resource_version
            replace(name, namespace, body)
        except ApiException as exc:
            if exc.status != 404:
                raise
            try:
                create(namespace, body)
                setattr(resources, created_attr, True)
            except ApiException as create_exc:
                if create_exc.status != 409:
                    raise
                existing = read(name, namespace)
                existing_meta = getattr(existing, "metadata", None)
                body_meta = getattr(body, "metadata", None)
                resource_version = getattr(existing_meta, "resource_version", None)
                if body_meta is not None and resource_version:
                    body_meta.resource_version = resource_version
                replace(name, namespace, body)

    def portal_preview_url(self, *, environment_id: str) -> str:
        base = self._settings.preview_public_base_url.rstrip("/")
        return f"{base}/p/{environment_id}"

    def workspace_preview_host(
        self, *, name: str, environment_id: str, namespace: str
    ) -> str | None:
        """Public ingress host for a preview, or None to fall back to NodePort.

        Prefers an explicit ``preview_ingress_host_template``. Otherwise, when a
        Cloudflare Tunnel / production is active and a base domain is configured,
        derives ``ws-{workspace_id}.{base_domain}`` to match the wildcard CNAME in
        Cloudflare DNS. Returns None in local offline development so the caller
        falls back to a NodePort URL.
        """
        template = self._settings.preview_ingress_host_template
        if (
            not template
            and self._settings.preview_tunnel_active
            and self._settings.preview_base_domain
        ):
            template = "ws-{workspace_id}.{base_domain}"
        if not template:
            return None
        return template.format(
            name=name,
            environment_id=environment_id,
            workspace_id=environment_id,
            namespace=namespace,
            base_domain=(self._settings.preview_base_domain or "").strip("."),
        )

    def ingress_preview_url(self, *, host: str) -> str:
        # Cloudflare Tunnel serves the public host over https (443 -> ingress :80),
        # so the URL is host-only with no NodePort suffix.
        scheme = "https" if self._settings.preview_tunnel_active else "http"
        return f"{scheme}://{host}"

    def node_port_preview_url(self, *, node_port: int) -> str:
        from urllib.parse import urlparse

        host = (self._settings.preview_node_host or "").strip().rstrip("/")
        # Follow the scheme Launchpad itself is served on (https in prod) so the
        # preview link isn't blocked as mixed content; default to http locally.
        scheme = "http"
        try:
            base_scheme = urlparse(self._settings.preview_public_base_url).scheme
            if base_scheme:
                scheme = base_scheme
        except Exception:
            pass
        if not host:
            # Never emit a hostless "http://:30087". Fall back to the
            # preview base URL host, then to loopback.
            try:
                host = urlparse(self._settings.preview_public_base_url).hostname or ""
            except Exception:
                host = ""
            host = host or "127.0.0.1"
        if "://" in host:
            return f"{host}:{node_port}"
        return f"{scheme}://{host}:{node_port}"
    def wait_for_workload_ready(
        self,
        *,
        namespace: str,
        timeout_seconds: float,
        expected_image: str | None = None,
        cancel_check: "Callable[[], bool] | None" = None,
    ) -> None:
        """Block until the *current* Deployment revision is Ready.

        Ready replicas from a previous ReplicaSet (e.g. nginx still serving while a
        new image is ImagePullBackOff) must not count as success.

        Fails fast (``RuntimeError``) on terminal pod states - ImagePullBackOff,
        CrashLoopBackOff, CreateContainerError - instead of spinning to the
        deadline. If ``cancel_check`` is provided and returns True (e.g. the user
        requested force-delete mid-provision), raises ``PreviewCancelled`` so the
        provision task aborts immediately.
        """
        if not self._settings.kubernetes_enabled:
            return
        assert self._apps is not None
        deadline = time.monotonic() + timeout_seconds
        last_ready = 0
        last_updated = 0
        last_desired = 1
        last_unavailable = 0
        stable_ready_polls = 0
        required_stable_polls = 2
        start_time = time.monotonic()
        while time.monotonic() < deadline:
            if cancel_check is not None and cancel_check():
                raise PreviewCancelled(
                    f"Provisioning of {namespace} cancelled by delete request"
                )

            pull_error = self._first_pod_image_error(namespace=namespace)
            if pull_error:
                raise RuntimeError(pull_error)

            crash_error = self._first_pod_crash_error(namespace=namespace)
            if crash_error:
                raise RuntimeError(crash_error)

            snapshot = self._deployment_ready_snapshot(namespace=namespace)
            last_desired = snapshot["desired"]
            last_ready = snapshot["ready"]
            last_updated = snapshot["updated"]
            last_unavailable = snapshot["unavailable"]

            if last_updated == 0 and (time.monotonic() - start_time) > 15.0:
                cp_stall = self._control_plane_stall_hint()
                if cp_stall:
                    raise RuntimeError(cp_stall)

            if snapshot["revision_ready"]:
                stable_ready_polls += 1
                if stable_ready_polls < required_stable_polls:
                    time.sleep(self._settings.kubernetes_ready_poll_seconds)
                    continue
                logger.info(
                    "kubernetes_workload_ready",
                    namespace=namespace,
                    deployment_count=snapshot.get("deployment_count"),
                    ready_replicas=last_ready,
                    updated_replicas=last_updated,
                    image=expected_image,
                )
                return
            stable_ready_polls = 0
            time.sleep(self._settings.kubernetes_ready_poll_seconds)

        # Final check: kind control-plane blips often flip Ready just after the
        # deadline. Accept success if every Deployment is Ready now.
        snapshot = self._deployment_ready_snapshot(namespace=namespace)
        if snapshot["revision_ready"]:
            logger.info(
                "kubernetes_workload_ready_after_deadline",
                namespace=namespace,
                deployment_count=snapshot.get("deployment_count"),
                ready_replicas=snapshot["ready"],
                updated_replicas=snapshot["updated"],
                image=expected_image,
            )
            return

        hint = self._first_pod_probe_hint(namespace=namespace)
        control_plane = self._control_plane_stall_hint()
        extras = " ".join(part for part in (hint, control_plane) if part)
        pending = snapshot.get("pending") or []
        pending_note = f" Pending: {', '.join(pending)}." if pending else ""
        raise TimeoutError(
            f"Deployments in {namespace} not Ready on current revision "
            f"(deployments={snapshot.get('deployment_count')}, updated={snapshot['updated']}, "
            f"ready={snapshot['ready']}, unavailable={snapshot['unavailable']}, "
            f"desired={snapshot['desired']}) within {timeout_seconds:.0f}s"
            + pending_note
            + (f". {extras}" if extras else "")
        )

    def _deployment_ready_snapshot(self, *, namespace: str) -> dict[str, object]:
        """Aggregate rollout readiness across *all* Deployments in the namespace.

        Workspaces no longer contain a single hardcoded ``app`` Deployment - they
        may ship ``launch-web``, ``launch-server``, ``postgres``, etc. The preview
        is Ready only when every Deployment has completed its current-revision
        rollout (``readyReplicas >= specReplicas`` with no lingering old pods).
        """
        assert self._apps is not None
        deployments = list(self._apps.list_namespaced_deployment(namespace).items or [])

        total_desired = total_ready = total_updated = total_unavailable = 0
        pending: list[str] = []
        all_ready = bool(deployments)  # an empty namespace is not "ready" yet

        for dep in deployments:
            meta = dep.metadata
            spec = dep.spec
            status = dep.status
            name = (meta.name if meta else None) or "?"
            desired = spec.replicas if (spec and spec.replicas is not None) else 1
            ready = (status.ready_replicas if status else None) or 0
            updated = (status.updated_replicas if status else None) or 0
            unavailable = (status.unavailable_replicas if status else None) or 0
            generation = (meta.generation if meta else None) or 0
            observed = (status.observed_generation if status else None) or 0
            total = getattr(status, "replicas", None) if status else None

            total_desired += desired
            total_ready += ready
            total_updated += updated
            total_unavailable += unavailable

            # Intentionally scaled to 0 (paused) counts as complete.
            if desired == 0:
                continue
            dep_ready = (
                observed >= generation
                and updated >= desired
                and ready >= desired
                and unavailable == 0
                # No old-revision pods lingering (guards nginx-old-RS false-Ready).
                and (total is None or total == updated)
            )
            if not dep_ready:
                all_ready = False
                pending.append(f"{name}({ready}/{desired})")

        return {
            "desired": total_desired,
            "ready": total_ready,
            "updated": total_updated,
            "unavailable": total_unavailable,
            "revision_ready": all_ready,
            "deployment_count": len(deployments),
            "pending": pending,
        }

    def _control_plane_stall_hint(self) -> str | None:
        """Detect kind/control-plane issues that stall Deployment rollouts."""
        if self._core is None:
            return None
        try:
            pods = self._core.list_namespaced_pod(
                "kube-system",
                label_selector="component=kube-controller-manager",
            )
        except Exception:
            return None
        for pod in pods.items or []:
            phase = pod.status.phase if pod.status else None
            for status in pod.status.container_statuses or []:
                waiting = status.state.waiting if status.state else None
                reason = waiting.reason if waiting else None
                if reason in {"CrashLoopBackOff", "Error", "ImagePullBackOff"} or (
                    phase and phase != "Running"
                ):
                    return (
                        f"Cluster control plane unhealthy: kube-controller-manager "
                        f"is {reason or phase}. Deployment rollouts will stall until "
                        "it recovers (common on overloaded kind clusters)."
                    )
                if status.ready is False:
                    restarts = status.restart_count or 0
                    if restarts >= 3:
                        return (
                            "Cluster control plane unhealthy: kube-controller-manager "
                            f"not Ready (restarts={restarts}). Deployment rollouts may stall."
                        )
        return None

    def delete_namespaced_hpa(self, *, namespace: str, name: str = "app") -> None:
        """Best-effort remove of preview HPA (kind has no metrics-server)."""
        if self._autoscaling is None:
            return
        from kubernetes.client.rest import ApiException

        try:
            self._autoscaling.delete_namespaced_horizontal_pod_autoscaler(name, namespace)
            logger.info("preview_hpa_removed", namespace=namespace, name=name)
        except ApiException as exc:
            if getattr(exc, "status", None) != 404:
                logger.warning(
                    "preview_hpa_remove_failed",
                    namespace=namespace,
                    name=name,
                    error=str(exc),
                )
        except Exception as exc:
            logger.warning(
                "preview_hpa_remove_failed",
                namespace=namespace,
                name=name,
                error=str(exc),
            )

    def scale_deployment(self, *, namespace: str, replicas: int = 1) -> bool:
        """Scale deployment replicas in namespace (0 to pause, 1 to resume).

        Returns True when scaling succeeded or Kubernetes is disabled (simulate mode).
        Returns False when a non-404 API error prevented scaling.
        """
        if not self._settings.kubernetes_enabled or self._apps is None:
            return True
        from kubernetes.client.rest import ApiException

        try:
            deployments = self._apps.list_namespaced_deployment(namespace)
            scaled = 0
            for dep in deployments.items or []:
                dep_name = dep.metadata.name if dep.metadata else "app"
                body = {"spec": {"replicas": replicas}}
                self._apps.patch_namespaced_deployment(dep_name, namespace, body)
                scaled += 1
                logger.info(
                    "kubernetes_deployment_scaled",
                    namespace=namespace,
                    name=dep_name,
                    replicas=replicas,
                )
            return True
        except ApiException as exc:
            if getattr(exc, "status", None) == 404:
                return True
            logger.warning(
                "kubernetes_scale_deployment_failed",
                namespace=namespace,
                replicas=replicas,
                error=str(exc),
            )
            return False

    def _first_pod_image_error(self, *, namespace: str) -> str | None:
        if self._core is None:
            return None
        # Namespace-wide: catch pull errors on any generated workload
        # (launch-web, launch-server, postgres, …), not just the legacy "app".
        try:
            pods = self._core.list_namespaced_pod(namespace)
        except Exception as exc:
            logger.warning("kubernetes_list_pods_for_image_error_failed", error=str(exc))
            return None
        terminal_reasons = {"ErrImagePull", "ImagePullBackOff", "InvalidImageName"}
        for pod in pods.items or []:
            pod_name = pod.metadata.name if pod.metadata else "app"
            for status in pod.status.container_statuses or []:
                waiting = status.state.waiting if status.state else None
                if waiting is None or waiting.reason not in terminal_reasons:
                    continue
                detail = (waiting.message or "").strip()
                image = status.image or "unknown"
                if detail:
                    return (
                        f"Failed to pull image {image} for pod {pod_name}: "
                        f"{waiting.reason} - {detail}"
                    )
                return f"Failed to pull image {image} for pod {pod_name}: {waiting.reason}"
        return None

    def _first_pod_crash_error(self, *, namespace: str) -> str | None:
        """Detect terminal crash states so provisioning fails fast (not on deadline).

        - CreateContainer*/RunContainerError: config errors that never self-heal
          (e.g. runAsNonRoot without a UID) - fail immediately.
        - CrashLoopBackOff after >= 2 restarts: the container keeps exiting
          (e.g. "postgres: Error", app crash) - fail with the last exit detail.
        """
        if self._core is None:
            return None
        try:
            pods = self._core.list_namespaced_pod(namespace)
        except Exception as exc:
            logger.warning("kubernetes_list_pods_for_crash_error_failed", error=str(exc))
            return None
        config_reasons = {
            "CreateContainerConfigError",
            "CreateContainerError",
            "RunContainerError",
        }
        for pod in pods.items or []:
            pod_name = pod.metadata.name if pod.metadata else "app"
            for status in pod.status.container_statuses or []:
                restarts = status.restart_count or 0
                waiting = status.state.waiting if status.state else None
                if waiting and waiting.reason in config_reasons:
                    detail = (waiting.message or "").strip()
                    base = (
                        f"Container {status.name} in pod {pod_name} failed to start: "
                        f"{waiting.reason}"
                    )
                    return f"{base} - {detail}" if detail else base
                if waiting and waiting.reason == "CrashLoopBackOff" and restarts >= 2:
                    last = status.last_state.terminated if status.last_state else None
                    detail = ""
                    if last:
                        detail = (last.message or "").strip() or f"exit code {last.exit_code}"
                    base = (
                        f"Container {status.name} in pod {pod_name} is crash-looping "
                        f"(CrashLoopBackOff after {restarts} restarts)"
                    )
                    return f"{base} - {detail}" if detail else base
        return None

    def _first_pod_probe_hint(self, *, namespace: str) -> str | None:
        """Best-effort hint from pod conditions / container state for timeouts."""
        if self._core is None:
            return None
        try:
            pods = self._core.list_namespaced_pod(namespace)
        except Exception:
            return None
        for pod in pods.items or []:
            pod_name = pod.metadata.name if pod.metadata else "app"
            for status in pod.status.container_statuses or []:
                waiting = status.state.waiting if status.state else None
                if waiting and waiting.reason:
                    detail = (waiting.message or waiting.reason).strip()
                    return f"Pod {pod_name} waiting: {detail}"
                last = status.last_state.terminated if status.last_state else None
                if last and last.reason:
                    return (
                        f"Pod {pod_name} last exit={last.exit_code} reason={last.reason}. "
                        "Check that readiness probes target the port your app listens on "
                        "(image EXPOSE / containerPort)."
                    )
            for condition in pod.status.conditions or []:
                if condition.type == "Ready" and condition.status != "True" and condition.message:
                    return f"Pod {pod_name}: {condition.message}"
        return (
            "Check readiness/liveness probes and containerPort - "
            "apps that listen on a non-80 port need matching probes/Service targetPort. "
            "Dev servers (Vite/Node) often need TCP probes: HTTP GET can hang awaiting headers "
            "while the process is compiling."
        )

    def _ready_pods_match_image(self, *, namespace: str, expected_image: str) -> bool:
        if self._core is None:
            return True
        from app.services.k8s_spec import preview_workload_selector

        selector = ",".join(f"{k}={v}" for k, v in preview_workload_selector().items())
        try:
            pods = self._core.list_namespaced_pod(namespace, label_selector=selector)
        except Exception:
            return False
        matched = False
        exp_base = expected_image.split(":")[0].split("/")[-1].lower()
        for pod in pods.items or []:
            phase = pod.status.phase if pod.status else None
            if phase != "Running":
                continue
            for status in pod.status.container_statuses or []:
                if not status.ready:
                    continue
                img_name = (status.image or "").lower()
                img_id = (status.image_id or "").lower()
                if (
                    img_name == expected_image
                    or exp_base in img_name
                    or exp_base in img_id
                    or "nginx" in img_name
                ):
                    matched = True
        return matched

    def apply_ingress(
        self,
        *,
        namespace: str,
        labels: dict[str, str],
        host: str,
        backend_service: str = "app",
        backend_port: int = 80,
    ) -> None:
        assert self._networking is not None
        from kubernetes import client
        from kubernetes.client.rest import ApiException

        ingress_class = (self._settings.preview_ingress_class or "nginx").strip() or "nginx"
        service_name = (backend_service or "app").strip() or "app"
        port_number = backend_port if backend_port and backend_port > 0 else 80
        ingress = client.V1Ingress(
            metadata=client.V1ObjectMeta(
                name="app",
                namespace=namespace,
                labels=labels,
                annotations={
                    "kubernetes.io/ingress.class": ingress_class,
                },
            ),
            spec=client.V1IngressSpec(
                ingress_class_name=ingress_class,
                rules=[
                    client.V1IngressRule(
                        host=host,
                        http=client.V1HTTPIngressRuleValue(
                            paths=[
                                client.V1HTTPIngressPath(
                                    path="/",
                                    path_type="Prefix",
                                    backend=client.V1IngressBackend(
                                        service=client.V1IngressServiceBackend(
                                            name=service_name,
                                            port=client.V1ServiceBackendPort(number=port_number),
                                        )
                                    ),
                                )
                            ]
                        ),
                    )
                ]
            ),
        )
        try:
            self._networking.read_namespaced_ingress("app", namespace, _request_timeout=10)
            self._networking.replace_namespaced_ingress(
                "app", namespace, ingress, _request_timeout=(5, 30)
            )
        except ApiException as exc:
            if exc.status in {408, 504}:
                raise RuntimeError(
                    "Kubernetes API timed out applying Ingress "
                    f"(HTTP {exc.status}). Check KUBERNETES_CONTEXT points at your "
                    "local cluster; gateway timeouts usually mean a remote/unreachable API."
                ) from exc
            if exc.status != 404:
                raise
            try:
                self._networking.create_namespaced_ingress(
                    namespace, ingress, _request_timeout=(5, 30)
                )
            except ApiException as create_exc:
                if create_exc.status in {408, 504}:
                    raise RuntimeError(
                        "Kubernetes API timed out creating Ingress "
                        f"(HTTP {create_exc.status}). Check KUBERNETES_CONTEXT points at "
                        "your local cluster; gateway timeouts usually mean a remote/"
                        "unreachable API."
                    ) from create_exc
                raise
        logger.info(
            "kubernetes_ingress_applied",
            namespace=namespace,
            host=host,
            ingress_class=ingress_class,
            backend_service=service_name,
            backend_port=port_number,
        )

    def resolve_docker_host_gateway_ip(self) -> str | None:
        """IP of the Docker host as seen from inside the local cluster (k3d/kind)."""
        import re
        import socket

        def _resolve_name(name: str) -> str | None:
            raw = (name or "").strip()
            if not raw:
                return None
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", raw):
                return raw
            try:
                infos = socket.getaddrinfo(raw, None, socket.AF_INET)
                if infos:
                    return infos[0][4][0]
            except OSError:
                return None
            return None

        explicit = (self._settings.preview_docker_host_ip or "").strip()
        if explicit:
            resolved = _resolve_name(explicit)
            if resolved:
                return resolved

        alias = (self._settings.preview_docker_host_alias or "host.k3d.internal").strip()
        if self._core is not None:
            try:
                cm = self._core.read_namespaced_config_map(
                    "coredns", "kube-system", _request_timeout=10
                )
                raw = ""
                if cm.data:
                    raw = cm.data.get("NodeHosts") or cm.data.get("Corefile") or ""
                for line in raw.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and alias in parts[1:]:
                        candidate = parts[0].strip()
                        if re.match(r"^\d+\.\d+\.\d+\.\d+$", candidate):
                            return candidate
            except Exception as exc:
                logger.info("docker_host_coredns_lookup_failed", alias=alias, error=str(exc))

        for name in (alias, "host.docker.internal"):
            resolved = _resolve_name(name)
            if resolved:
                return resolved
        return None

    def apply_docker_host_preview_ingress(
        self,
        *,
        namespace: str,
        environment_id: str,
        name: str,
        host_port: int,
        labels: dict[str, str] | None = None,
    ) -> str | None:
        """Expose a Docker-published host port at ``https://ws-{id}.{base_domain}``.

        Creates a selector-less Service + Endpoints pointing at the Docker host
        gateway IP (``host.k3d.internal``) and an Ingress host matching the named
        Cloudflare Tunnel wildcard. Returns the public preview URL, or None when
        the bridge cannot be created (caller keeps host-port / trycloudflare).
        """
        if not self._settings.kubernetes_enabled or self._core is None or self._networking is None:
            logger.info(
                "docker_host_preview_ingress_skipped",
                reason="kubernetes_unavailable",
                environment_id=environment_id,
            )
            return None
        if not self._settings.preview_tunnel_active or not self._settings.preview_base_domain:
            logger.info(
                "docker_host_preview_ingress_skipped",
                reason="preview_domain_inactive",
                environment_id=environment_id,
                tunnel_active=self._settings.preview_tunnel_active,
                base_domain=bool(self._settings.preview_base_domain),
            )
            return None
        if host_port <= 0:
            logger.info(
                "docker_host_preview_ingress_skipped",
                reason="invalid_host_port",
                environment_id=environment_id,
                host_port=host_port,
            )
            return None

        host = self.workspace_preview_host(
            name=name, environment_id=environment_id, namespace=namespace
        )
        if not host:
            logger.info(
                "docker_host_preview_ingress_skipped",
                reason="no_preview_host",
                environment_id=environment_id,
            )
            return None

        gateway_ip = self.resolve_docker_host_gateway_ip()
        if not gateway_ip:
            logger.warning(
                "docker_host_preview_ingress_no_gateway",
                environment_id=environment_id,
                host_port=host_port,
            )
            return None

        from kubernetes import client
        from kubernetes.client.rest import ApiException

        meta_labels = {
            "app.kubernetes.io/managed-by": "launchpad",
            "launchpad.io/managed-by": "launchpad-idp",
            "launchpad.io/environment-id": environment_id,
            "launchpad.io/preview-bridge": "docker-host",
            **(labels or {}),
        }

        try:
            self._core.read_namespace(namespace, _request_timeout=10)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self._core.create_namespace(
                client.V1Namespace(
                    metadata=client.V1ObjectMeta(name=namespace, labels=meta_labels)
                ),
                _request_timeout=10,
            )
            logger.info("kubernetes_namespace_created", namespace=namespace, purpose="docker-host-preview")

        service = client.V1Service(
            metadata=client.V1ObjectMeta(name="app", namespace=namespace, labels=meta_labels),
            spec=client.V1ServiceSpec(
                ports=[
                    client.V1ServicePort(
                        name="http",
                        port=80,
                        target_port=host_port,
                        protocol="TCP",
                    )
                ],
            ),
        )
        try:
            self._core.read_namespaced_service("app", namespace, _request_timeout=10)
            self._core.replace_namespaced_service("app", namespace, service, _request_timeout=10)
        except ApiException as exc:
            if getattr(exc, "status", None) != 404:
                raise
            self._core.create_namespaced_service(namespace, service, _request_timeout=10)

        endpoints = client.V1Endpoints(
            metadata=client.V1ObjectMeta(name="app", namespace=namespace, labels=meta_labels),
            subsets=[
                client.V1EndpointSubset(
                    addresses=[client.V1EndpointAddress(ip=gateway_ip)],
                    ports=[
                        client.CoreV1EndpointPort(name="http", port=host_port, protocol="TCP")
                    ],
                )
            ],
        )
        try:
            self._core.read_namespaced_endpoints("app", namespace, _request_timeout=10)
            self._core.replace_namespaced_endpoints("app", namespace, endpoints, _request_timeout=10)
        except ApiException as exc:
            if getattr(exc, "status", None) != 404:
                raise
            self._core.create_namespaced_endpoints(namespace, endpoints, _request_timeout=10)

        self.apply_ingress(
            namespace=namespace,
            labels=meta_labels,
            host=host,
            backend_service="app",
            backend_port=80,
        )
        url = self.ingress_preview_url(host=host)
        logger.info(
            "docker_host_preview_ingress_applied",
            namespace=namespace,
            environment_id=environment_id,
            host=host,
            host_port=host_port,
            gateway_ip=gateway_ip,
            preview_url=url,
        )
        return url

    def list_allocated_node_ports(self, *, exclude_namespace: str | None = None) -> set[int]:
        """Return NodePorts already claimed by Services cluster-wide."""
        if not self._settings.kubernetes_enabled or self._core is None:
            return set()
        used: set[int] = set()
        try:
            services = self._core.list_service_for_all_namespaces()
        except Exception as exc:
            logger.warning("kubernetes_list_services_for_nodeports_failed", error=str(exc))
            return used
        for svc in services.items or []:
            meta = svc.metadata
            if (
                exclude_namespace
                and meta is not None
                and meta.namespace == exclude_namespace
            ):
                continue
            ports = (svc.spec.ports if svc.spec else None) or []
            for port in ports:
                if port.node_port:
                    used.add(int(port.node_port))
        return used

    def resolve_external_preview_url(
        self, namespace: str, *, timeout_seconds: float = 120.0
    ) -> str | None:
        """Resolve a cloud/production preview's public URL from the cluster.

        Reads the external address of a LoadBalancer Service (``status.loadBalancer``)
        or an Ingress (its host rule / load-balancer address) in the namespace - e.g.
        ``http://34.120.10.5`` or ``https://preview.example.com``. Cloud load balancers
        take time to allocate an address, so this polls while a candidate exists but has
        no address yet; it returns ``None`` immediately when nothing is externally
        exposed (caller keeps its default URL) and after ``timeout_seconds`` otherwise.
        """
        if not self._settings.kubernetes_enabled or self._core is None:
            return None
        import time as _time
        from kubernetes.client.rest import ApiException

        deadline = _time.time() + max(timeout_seconds, 0.0)
        while True:
            saw_pending = False

            try:
                services = self._core.list_namespaced_service(namespace)
            except ApiException:
                services = None
            for svc in (getattr(services, "items", None) or []):
                if svc.spec is None or svc.spec.type != "LoadBalancer":
                    continue
                addr = _lb_ingress_address(svc.status)
                if addr:
                    port = svc.spec.ports[0].port if svc.spec.ports else None
                    return _external_url(addr, port)
                saw_pending = True  # LB exists but address not assigned yet

            if self._networking is not None:
                try:
                    ingresses = self._networking.list_namespaced_ingress(namespace)
                except ApiException:
                    ingresses = None
                for ing in (getattr(ingresses, "items", None) or []):
                    tls = bool(ing.spec and ing.spec.tls)
                    host = None
                    if ing.spec and ing.spec.rules:
                        host = next((r.host for r in ing.spec.rules if r.host), None)
                    chosen = host or _lb_ingress_address(ing.status)
                    if chosen:
                        return f"{'https' if tls else 'http'}://{chosen}"
                    saw_pending = True  # Ingress exists but no host/address yet

            if not saw_pending or _time.time() >= deadline:
                return None
            _time.sleep(3.0)

    def read_namespaced_app_node_port(self, namespace: str) -> int | None:
        if not self._settings.kubernetes_enabled or self._core is None:
            return None

        existing = _read_or_none(self._core.read_namespaced_service, "app", namespace)
        if (
            existing is None
            or existing.spec is None
            or existing.spec.type != "NodePort"
            or not existing.spec.ports
            or existing.spec.ports[0].node_port is None
        ):
            return None
        return int(existing.spec.ports[0].node_port)

    # Public Service accessors so collaborators (e.g. ManifestDeployer) operate on
    # a supported surface instead of reaching into the raw ``_core`` client.
    def read_service(self, name: str, namespace: str):
        """Return the named Service, or None on 404 / when Kubernetes is disabled."""
        if self._core is None:
            return None
        return _read_or_none(self._core.read_namespaced_service, name, namespace)

    def delete_service(self, name: str, namespace: str) -> None:
        """Delete the named Service, ignoring a 404 (already gone)."""
        if self._core is None:
            return
        _ignore_404(self._core.delete_namespaced_service, name, namespace)

    def create_service(self, namespace: str, body: object) -> None:
        """Create a Service in the namespace."""
        assert self._core is not None
        self._core.create_namespaced_service(namespace, body)

    @staticmethod
    def _datastore_init_containers(
        *,
        enable_postgres: bool,
        enable_redis: bool,
    ) -> list[object]:
        """Wait for in-cluster Postgres/Redis before the app container starts."""
        from kubernetes import client

        waits: list[tuple[str, str, int]] = []
        if enable_postgres:
            waits.append(("wait-for-postgres", "postgres", 5432))
        if enable_redis:
            waits.append(("wait-for-redis", "redis", 6379))
        containers: list[object] = []
        for name, host, port in waits:
            containers.append(
                client.V1Container(
                    name=name,
                    image="busybox:1.36",
                    image_pull_policy="IfNotPresent",
                    command=[
                        "sh",
                        "-c",
                        (
                            "count=0\n"
                            f"until nc -z {host} {port}; do\n"
                            "  count=$((count+1))\n"
                            "  if [ $count -ge 30 ]; then\n"
                            f'    echo "Timed out waiting for {host}:{port} after 60s"\n'
                            "    exit 1\n"
                            "  fi\n"
                            f'  echo "waiting for {host}:{port} ($count/30)..."\n'
                            "  sleep 2\n"
                            "done\n"
                        ),
                    ],
                    resources=client.V1ResourceRequirements(
                        requests={"cpu": "10m", "memory": "16Mi"},
                        limits={"cpu": "50m", "memory": "32Mi"},
                    ),
                    security_context=client.V1SecurityContext(
                        allow_privilege_escalation=False,
                        read_only_root_filesystem=True,
                        run_as_non_root=True,
                        run_as_user=65534,
                        capabilities=client.V1Capabilities(drop=["ALL"]),
                    ),
                )
            )
        return containers

    def _build_app_container(
        self,
        *,
        image: str,
        git_repo_url: str,
        git_branch: str,
        commit_sha: str | None = None,
        listen_port: int,
        enable_postgres: bool = False,
        enable_redis: bool = False,
    ) -> object:
        """Hardened app container spec shared by provision and rebuild paths."""
        from kubernetes import client

        effective_port = 80 if "http-echo" in image else listen_port
        nginx = _is_nginx_image(image)
        wants_secrets = enable_postgres or enable_redis

        env = [
            client.V1EnvVar(name="GIT_REPO_URL", value=git_repo_url),
            client.V1EnvVar(name="GIT_BRANCH", value=git_branch),
            client.V1EnvVar(name="PORT", value=str(effective_port)),
        ]
        if commit_sha:
            env.append(client.V1EnvVar(name="GIT_COMMIT_SHA", value=commit_sha))
        if wants_secrets:
            env.extend(
                [
                    client.V1EnvVar(
                        name="HAS_DATABASE",
                        value="true" if enable_postgres else "false",
                    ),
                    client.V1EnvVar(
                        name="HAS_REDIS",
                        value="true" if enable_redis else "false",
                    ),
                ]
            )
        if not nginx and not "http-echo" in image:
            # Frontend dev servers often require binding to all interfaces for probes.
            env.extend(
                [
                    client.V1EnvVar(name="HOST", value="0.0.0.0"),
                    client.V1EnvVar(name="APP_PORT", value=str(effective_port)),
                ]
            )

        resources = (
            # Non-nginx workloads can cold-start longer and need more headroom.
            client.V1ResourceRequirements(
                requests={"cpu": "100m", "memory": "256Mi"},
                limits={"cpu": "500m", "memory": "768Mi"},
            )
            if not nginx and "http-echo" not in image
            else client.V1ResourceRequirements(
                requests={"cpu": "100m", "memory": "128Mi"},
                limits={"cpu": "500m", "memory": "512Mi"},
            )
        )

        read_only_root_filesystem = nginx and "http-echo" not in image
        env_from = (
            [client.V1EnvFromSource(secret_ref=client.V1SecretEnvSource(name="app-secrets"))]
            if wants_secrets
            else None
        )

        container = client.V1Container(
            name="app",
            image=image,
            image_pull_policy="IfNotPresent",
            ports=[client.V1ContainerPort(container_port=effective_port)],
            resources=resources,
            readiness_probe=client.V1Probe(
                http_get=client.V1HTTPGetAction(path="/", port=effective_port) if nginx else None,
                tcp_socket=client.V1TCPSocketAction(port=effective_port) if not nginx else None,
                initial_delay_seconds=5 if nginx else 5,
                period_seconds=10 if nginx else 5,
                timeout_seconds=3,
                failure_threshold=3 if nginx else 12,
                success_threshold=1,
            ),
            liveness_probe=client.V1Probe(
                http_get=client.V1HTTPGetAction(path="/", port=effective_port) if nginx else None,
                tcp_socket=client.V1TCPSocketAction(port=effective_port) if not nginx else None,
                initial_delay_seconds=15 if nginx else 120,
                period_seconds=20 if nginx else 20,
                timeout_seconds=3,
                failure_threshold=3 if nginx else 6,
                success_threshold=1,
            ),
            startup_probe=client.V1Probe(
                tcp_socket=client.V1TCPSocketAction(port=effective_port) if not nginx else None,
                period_seconds=5 if not nginx else None,
                timeout_seconds=3 if not nginx else None,
                failure_threshold=48 if not nginx else None,
                success_threshold=1 if not nginx else None,
                initial_delay_seconds=0 if not nginx else None,
            )
            if not nginx
            else None,
            env=env,
            env_from=env_from,
            security_context=client.V1SecurityContext(
                allow_privilege_escalation=False,
                read_only_root_filesystem=bool(read_only_root_filesystem),
                capabilities=client.V1Capabilities(drop=["ALL"]),
            ),
            volume_mounts=[
                client.V1VolumeMount(name="tmp", mount_path="/tmp"),
                client.V1VolumeMount(name="cache", mount_path="/var/cache/nginx"),
                client.V1VolumeMount(name="run", mount_path="/var/run"),
            ],
        )
        if "http-echo" in image:
            container.args = ["-listen=:80", "-text=launchpad-preview"]
            container.volume_mounts = None
            container.security_context = client.V1SecurityContext(
                allow_privilege_escalation=False,
                read_only_root_filesystem=False,
                capabilities=client.V1Capabilities(drop=["ALL"]),
            )
        return container

    def _build_app_deployment(
        self,
        *,
        namespace: str,
        labels: dict[str, str],
        annotations: dict[str, str],
        image: str,
        git_repo_url: str,
        git_branch: str,
        commit_sha: str | None = None,
        listen_port: int,
        enable_postgres: bool = False,
        enable_redis: bool = False,
    ) -> object:
        from kubernetes import client

        selector = preview_workload_selector()
        container = self._build_app_container(
            image=image,
            git_repo_url=git_repo_url,
            git_branch=git_branch,
            commit_sha=commit_sha,
            listen_port=listen_port,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
        )
        volumes = [
            client.V1Volume(name="tmp", empty_dir=client.V1EmptyDirVolumeSource()),
            client.V1Volume(name="cache", empty_dir=client.V1EmptyDirVolumeSource()),
            client.V1Volume(name="run", empty_dir=client.V1EmptyDirVolumeSource()),
        ]
        pod_security: object | None = None
        if _is_nginx_image(image):
            pod_security = client.V1PodSecurityContext(
                run_as_non_root=True,
                run_as_user=101,
                seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
            )
        if "http-echo" in image:
            volumes = []
            pod_security = None

        init_containers = self._datastore_init_containers(
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
        ) or None
        merged_annotations = {
            **annotations,
            "launchpad.io/enable-postgres": "true" if enable_postgres else "false",
            "launchpad.io/enable-redis": "true" if enable_redis else "false",
        }

        return client.V1Deployment(
            metadata=client.V1ObjectMeta(
                name="app",
                namespace=namespace,
                labels=labels,
                annotations=merged_annotations,
            ),
            spec=client.V1DeploymentSpec(
                replicas=1,
                selector=client.V1LabelSelector(match_labels=selector),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(
                        labels={**labels, **selector},
                        annotations=merged_annotations,
                    ),
                    spec=client.V1PodSpec(
                        init_containers=init_containers,
                        containers=[container],
                        volumes=volumes or None,
                        security_context=pod_security,
                    ),
                ),
            ),
        )

    def _apply_workload(
        self,
        *,
        namespace: str,
        labels: dict[str, str],
        git_branch: str,
        git_repo_url: str,
        image: str,
        listen_port: int,
        node_port: int,
        used_ports: set[int] | None = None,
        environment_id: str | None = None,
        enable_postgres: bool = False,
        enable_redis: bool = False,
    ) -> int:
        assert self._core is not None and self._apps is not None
        from kubernetes import client
        from kubernetes.client.rest import ApiException

        annotations = {
            "launchpad.io/git-repo": git_repo_url,
            "launchpad.io/git-branch": git_branch,
        }
        deployment = self._build_app_deployment(
            namespace=namespace,
            labels=labels,
            annotations=annotations,
            image=image,
            git_repo_url=git_repo_url,
            git_branch=git_branch,
            listen_port=listen_port,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
        )
        selector = preview_workload_selector()
        try:
            self._apps.read_namespaced_deployment("app", namespace)
            self._apps.replace_namespaced_deployment("app", namespace, deployment)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self._apps.create_namespaced_deployment(namespace, deployment)

        claimed = set(used_ports or ())
        candidates = [node_port]
        # On collision races, walk the configured range for another free port.
        port_min = self._settings.preview_node_port_min
        port_max = self._settings.preview_node_port_max
        for port in range(port_min, port_max + 1):
            if port not in candidates and port not in claimed:
                candidates.append(port)

        last_error: ApiException | None = None
        applied_port = node_port
        for candidate in candidates:
            service = client.V1Service(
                metadata=client.V1ObjectMeta(name="app", namespace=namespace, labels=labels),
                spec=client.V1ServiceSpec(
                    selector=selector,
                    ports=[
                        client.V1ServicePort(
                            port=80,
                            target_port=listen_port,
                            protocol="TCP",
                            node_port=candidate,
                        )
                    ],
                    type="NodePort",
                ),
            )
            try:
                existing = self._core.read_namespaced_service("app", namespace)
                if (
                    existing.spec is not None
                    and existing.spec.type == "NodePort"
                    and existing.spec.ports
                    and existing.spec.ports[0].node_port == candidate
                ):
                    self._core.replace_namespaced_service("app", namespace, service)
                else:
                    _ignore_404(self._core.delete_namespaced_service, "app", namespace)
                    self._core.create_namespaced_service(namespace, service)
                applied_port = candidate
                last_error = None
                break
            except ApiException as exc:
                if exc.status == 404:
                    try:
                        self._core.create_namespaced_service(namespace, service)
                        applied_port = candidate
                        last_error = None
                        break
                    except ApiException as create_exc:
                        if _is_node_port_allocated_error(create_exc):
                            logger.warning(
                                "kubernetes_node_port_collision",
                                namespace=namespace,
                                node_port=candidate,
                            )
                            claimed.add(candidate)
                            last_error = create_exc
                            continue
                        raise
                if _is_node_port_allocated_error(exc):
                    logger.warning(
                        "kubernetes_node_port_collision",
                        namespace=namespace,
                        node_port=candidate,
                    )
                    claimed.add(candidate)
                    last_error = exc
                    continue
                raise

        if last_error is not None:
            raise last_error

        logger.info(
            "kubernetes_workload_applied",
            namespace=namespace,
            image=image,
            git_branch=git_branch,
            node_port=applied_port,
            environment_id=environment_id,
        )
        return applied_port

    def rebuild_workload(
        self,
        *,
        namespace: str,
        environment_id: str,
        name: str,
        git_branch: str,
        git_repo_url: str,
        commit_sha: str,
        owner_label: str = "launchpad",
        image: str | None = None,
        enable_postgres: bool = False,
        enable_redis: bool = False,
    ) -> None:
        """Roll the app Deployment to a commit-tagged image annotation/env."""
        resolved_image = image or self._image_for_commit(commit_sha)
        listen_port = _resolve_listen_port_from_image(resolved_image)
        ready_timeout = _workload_ready_timeout_seconds(
            image=resolved_image, base_timeout_seconds=self._settings.kubernetes_ready_timeout_seconds
        )
        labels = build_preview_labels(
            environment_id=environment_id,
            name=name,
            git_branch=git_branch,
            git_repo_url=git_repo_url,
            owner_label=owner_label,
        )
        labels["launchpad.io/git-commit"] = sanitize_label(commit_sha)

        if not self._settings.kubernetes_enabled:
            logger.info(
                "kubernetes_simulate_rebuild",
                namespace=namespace,
                environment_id=environment_id,
                commit_sha=commit_sha,
                image=resolved_image,
                enable_postgres=enable_postgres,
                enable_redis=enable_redis,
            )
            return

        assert self._apps is not None and self._core is not None
        from kubernetes.client.rest import ApiException

        annotations = {
            "launchpad.io/git-repo": git_repo_url,
            "launchpad.io/git-branch": git_branch,
            "launchpad.io/git-commit": commit_sha,
        }
        deployment = self._build_app_deployment(
            namespace=namespace,
            labels=labels,
            annotations=annotations,
            image=resolved_image,
            git_repo_url=git_repo_url,
            git_branch=git_branch,
            commit_sha=commit_sha,
            listen_port=listen_port,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
        )
        try:
            self._apps.read_namespaced_deployment("app", namespace)
            self._apps.replace_namespaced_deployment("app", namespace, deployment)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self._apps.create_namespaced_deployment(namespace, deployment)

        # Best-effort: align the Service targetPort with the Deployment containerPort.
        try:
            existing_svc = self._core.read_namespaced_service("app", namespace)
            if existing_svc.spec and existing_svc.spec.ports:
                existing_svc.spec.ports[0].target_port = listen_port
                self._core.replace_namespaced_service("app", namespace, existing_svc)
        except Exception:
            pass

        self.wait_for_workload_ready(
            namespace=namespace,
            timeout_seconds=ready_timeout,
            expected_image=resolved_image,
        )

        logger.info(
            "kubernetes_workload_rebuilt",
            namespace=namespace,
            image=resolved_image,
            commit_sha=commit_sha,
        )

    def _image_for_commit(self, commit_sha: str) -> str:
        base = (self._settings.default_workload_image or "app").strip() or "app"
        if ":" in base.rsplit("/", 1)[-1]:
            repo = base.rsplit(":", 1)[0]
        else:
            repo = base
        tag = commit_sha if commit_sha else "latest"
        return f"{repo}:{tag}"

    def teardown(self, namespace: str) -> None:
        if not self._settings.kubernetes_enabled:
            logger.info("kubernetes_simulate_teardown", namespace=namespace)
            return

        if self._core is None:
            # Clients failed to load (kubeconfig/context unreachable). There is
            # nothing we can delete from here; treat the namespace as already gone
            # so the environment can still be marked DESTROYED instead of getting
            # stuck. Any live namespace is left to the cluster's GC / TTL reaper.
            logger.warning("kubernetes_teardown_skipped_no_client", namespace=namespace)
            return

        from kubernetes.client.rest import ApiException

        try:
            # Bound the HTTP wait: a blackholed apiserver must not leave the
            # environment stuck in TEARDOWN_PENDING forever.
            self._core.delete_namespace(
                namespace,
                grace_period_seconds=0,
                propagation_policy="Background",
                _request_timeout=(5, 15),
            )
            logger.info("kubernetes_namespace_deleted", namespace=namespace)
        except ApiException as exc:
            if exc.status == 404:
                logger.info("kubernetes_namespace_already_gone", namespace=namespace)
                return
            logger.warning(
                "kubernetes_teardown_api_error",
                namespace=namespace,
                status=exc.status,
                error=str(exc),
            )
        except Exception as exc:
            logger.warning(
                "kubernetes_teardown_failed",
                namespace=namespace,
                error=str(exc),
            )

    def rollback(self, resources: ProvisionedResources) -> None:
        """Keep the preview namespace after a failed first provision / Ready timeout.

        Deleting the namespace here looked like a user-initiated teardown and removed
        Retry/Destroy targets. Rebuild failures restore a prior good revision via
        ``_attempt_rebuild_rollback`` instead. Explicit Destroy still calls ``teardown``.
        """
        if not self._settings.kubernetes_enabled:
            return
        logger.info(
            "kubernetes_rollback_skipped_keep_namespace",
            namespace=resources.namespace,
            created_namespace=resources.created_namespace,
            created_workload=resources.created_workload,
            image=resources.image,
            node_port=resources.node_port,
        )


def allocate_node_port(
    environment_id: str,
    *,
    port_min: int,
    port_max: int,
    used_ports: set[int] | frozenset[int] | None = None,
) -> int:
    """Pick a NodePort in [port_min, port_max], preferring a stable hash of env id.

    Skips ports listed in ``used_ports`` (already allocated in the cluster).
    """
    if port_max < port_min:
        raise ValueError("preview_node_port_max must be >= preview_node_port_min")
    span = port_max - port_min + 1
    digest = hashlib.sha256(environment_id.encode("utf-8")).hexdigest()
    start = int(digest[:8], 16) % span
    used = used_ports or set()
    for step in range(span):
        candidate = port_min + ((start + step) % span)
        if candidate not in used:
            return candidate
    raise RuntimeError(
        f"No free NodePort in [{port_min}, {port_max}] "
        f"({len(used)} already allocated). Destroy a preview or widen "
        "PREVIEW_NODE_PORT_MIN/MAX (and kind port mappings)."
    )


def _detect_kind_forwarded_node_ports(cluster_name: str | None = None) -> list[int]:
    """Inspect local K3s / Kind control-plane containers to find host-forwarded NodePorts."""
    import subprocess
    import shutil
    import re
    if not shutil.which("docker"):
        return []
    from app.services.manifest_deploy import resolve_local_cluster_name
    real_cluster = resolve_local_cluster_name(cluster_name)
    # Only probe THIS cluster's containers - generic fallback names (e.g.
    # "launchpad-control-plane") would leak another local cluster's host ports and
    # hand previews a NodePort that isn't actually forwarded here.
    # k3d publishes the host NodePort range on its loadbalancer (serverlb), not the
    # server node; kind publishes on "<cluster>-control-plane".
    containers = (
        f"k3d-{real_cluster}-serverlb",
        f"k3d-{real_cluster}-server-0",
        f"{real_cluster}-k3s",
        f"{real_cluster}-control-plane",
    )
    ports: list[int] = []
    for c_name in containers:
        try:
            res = subprocess.run(
                ["docker", "port", c_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode != 0:
                continue
            for line in res.stdout.splitlines():
                m = re.search(r"(\d+)/tcp\s*->", line)
                if m:
                    port = int(m.group(1))
                    if 30000 <= port <= 32767:
                        ports.append(port)
        except Exception:
            continue
    return sorted(set(ports))


def _lb_ingress_address(status: object) -> str | None:
    """First external hostname/IP from a Service/Ingress ``status.loadBalancer``."""
    lb = getattr(status, "load_balancer", None)
    ingress = getattr(lb, "ingress", None) if lb is not None else None
    if not ingress:
        return None
    first = ingress[0]
    return getattr(first, "hostname", None) or getattr(first, "ip", None)


def _external_url(addr: str, port: int | None) -> str:
    """Build a public URL, using https for :443 and omitting default ports."""
    scheme = "https" if port == 443 else "http"
    if port in (None, 80, 443):
        return f"{scheme}://{addr}"
    return f"{scheme}://{addr}:{port}"


def resolve_preview_node_port(
    environment_id: str,
    *,
    existing_port: int | None,
    port_min: int,
    port_max: int,
    used_ports: set[int] | frozenset[int] | None = None,
    cluster_name: str | None = None,
) -> int:
    """Keep an in-range sticky NodePort; otherwise allocate inside the mapped window.

    Kubernetes auto-assigns NodePorts in 30000-32767 when a Service is created as
    NodePort without an explicit ``nodePort``. Those ports are usually outside the
    kind ``extraPortMappings`` range and will not load from the host.
    """
    forwarded = _detect_kind_forwarded_node_ports(cluster_name)
    if forwarded:
        port_min = max(port_min, min(forwarded))
        port_max = min(port_max, max(forwarded))
        if port_max < port_min:
            port_min = min(forwarded)
            port_max = max(forwarded)
    if existing_port is not None and port_min <= existing_port <= port_max:
        return existing_port
    return allocate_node_port(
        environment_id,
        port_min=port_min,
        port_max=port_max,
        used_ports=used_ports,
    )


def _is_node_port_allocated_error(exc: Exception) -> bool:
    body = getattr(exc, "body", None) or str(exc)
    text = body if isinstance(body, str) else str(body)
    return "nodePort" in text and "already allocated" in text



"""Kubernetes raw-manifest and Helm chart layout generators under ``infra/``.

Emits either:

- ``infra/k8s/manifests/`` - production-oriented raw Kubernetes objects
- ``infra/helm/app-chart/`` - standard Helm v3 chart layout

Optional add-ons (ingress-nginx) land under ``infra/k8s/addons/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings
from app.schemas.cloud import (
    CostOptimizationConfig,
    IngressClassName,
    KubernetesPackaging,
    KubernetesWorkloadOptions,
    SpotProvisionerStrategy,
    WorkloadDependenciesConfig,
)
from app.services.workload_dependencies import (
    dependency_secret_string_data,
    in_cluster_manifest_files,
    init_container_wait_blocks,
    _in_cluster_kinds,
)
from app.services.cost_optimization import (
    cluster_autoscaler_notes_yaml,
    cost_marker_comment,
    idle_shutdown_cronjobs_yaml,
    karpenter_nodepool_yaml,
    resolve_resources,
    spot_affinity_tolerations_yaml,
    spot_helm_values_fragment,
)
from app.services.k8s_spec import (
    render_limit_range_yaml,
    render_resource_quota_yaml,
    render_workspace_network_policy_yaml,
)

K8S_MANIFESTS_ROOT = Path("infra") / "k8s" / "manifests"
K8S_ADDONS_ROOT = Path("infra") / "k8s" / "addons"
HELM_CHART_ROOT = Path("infra") / "helm" / "app-chart"
INGRESS_NGINX_VALUES = K8S_ADDONS_ROOT / "ingress-nginx-values.yaml"


@dataclass(frozen=True)
class WorkloadImageSpec:
    """Describes the container image + runtime shape the workload manifests deploy.

    The default value reproduces the historical Nginx placeholder exactly so
    manifests generated without a scaffolded application are byte-for-byte
    unchanged. When Launchpad scaffolds a runnable mini-application it passes a
    populated spec so the Deployment/Pod deploy the built image (with correct
    port, health-probe paths, non-root UID, and writable mounts) instead of the
    generic Nginx container.
    """

    image: str = "nginx:1.27-alpine"
    image_pull_policy: str = "IfNotPresent"
    container_port: int = 80
    liveness_path: str = "/"
    readiness_path: str = "/"
    run_as_user: int = 101
    read_only_root_fs: bool = True
    writable_mounts: tuple[tuple[str, str], ...] = (
        ("tmp", "/tmp"),
        ("cache", "/var/cache/nginx"),
        ("run", "/var/run"),
    )
    app_version: str = "1.0.0"
    replicas: int = 2

    @property
    def image_repository(self) -> str:
        return self.image.rsplit(":", 1)[0] if ":" in self.image else self.image

    @property
    def image_tag(self) -> str:
        return self.image.rsplit(":", 1)[1] if ":" in self.image else "latest"


def _render_volume_mounts_block(spec: WorkloadImageSpec) -> str:
    if not spec.writable_mounts:
        return ""
    lines = ["\n          volumeMounts:"]
    for vol_name, mount_path in spec.writable_mounts:
        lines.append(f"            - name: {vol_name}")
        lines.append(f"              mountPath: {mount_path}")
    return "\n".join(lines)


def _render_volumes_block(spec: WorkloadImageSpec) -> str:
    if not spec.writable_mounts:
        return ""
    lines = ["\n      volumes:"]
    for vol_name, _ in spec.writable_mounts:
        lines.append(f"        - name: {vol_name}")
        lines.append("          emptyDir: {}")
    return "\n".join(lines)


def _render_workload_env_block(
    name: str,
    spec: WorkloadImageSpec,
    dependencies: WorkloadDependenciesConfig,
) -> str:
    """Render the shared application env vars surfaced to the health dashboard."""
    has_db = (
        dependencies.postgres.enabled
        or dependencies.mysql.enabled
        or dependencies.mariadb.enabled
        or dependencies.mongodb.enabled
    )
    has_redis = dependencies.redis.enabled
    return (
        f'            - name: ENVIRONMENT_NAME\n              value: "{name}"\n'
        f'            - name: APP_VERSION\n              value: "{spec.app_version}"\n'
        f'            - name: PORT\n              value: "{spec.container_port}"\n'
        f'            - name: REPLICA_COUNT\n              value: "{spec.replicas}"\n'
        f'            - name: HAS_DATABASE\n              value: "{str(has_db).lower()}"\n'
        f'            - name: HAS_REDIS\n              value: "{str(has_redis).lower()}"\n'
        # NOTE: API_URL / BACKEND_URL / NEXT_PUBLIC_API_URL are injected per-workload
        # (via extra_env) only for frontends, pointing at the real backend Service.
        # A backend has no upstream API, and hardcoding a nonexistent "api-server"
        # host here would leave every workload advertising a dead backend target.
        "            - name: POD_NAME\n"
        "              valueFrom:\n"
        "                fieldRef:\n"
        "                  fieldPath: metadata.name\n"
        "            - name: POD_NAMESPACE\n"
        "              valueFrom:\n"
        "                fieldRef:\n"
        "                  fieldPath: metadata.namespace\n"
        "            - name: POD_IP\n"
        "              valueFrom:\n"
        "                fieldRef:\n"
        "                  fieldPath: status.podIP"
    )


def write_kubernetes_layout(
    workspace_dir: Path,
    *,
    name: str,
    packaging: KubernetesPackaging,
    options: KubernetesWorkloadOptions | None = None,
    cost_optimization: CostOptimizationConfig | None = None,
    dependencies: WorkloadDependenciesConfig | None = None,
    cloud: object | None = None,
    workload: WorkloadImageSpec | None = None,
) -> list[str]:
    """Writes the selected Kubernetes packaging layout; returns relative paths."""
    opts = options or KubernetesWorkloadOptions()
    cost = cost_optimization or CostOptimizationConfig()
    deps = dependencies or WorkloadDependenciesConfig()
    spec = workload or WorkloadImageSpec()
    if deps.any_enabled() and not opts.secret:
        opts = opts.model_copy(update={"secret": True})
    if packaging == KubernetesPackaging.NONE:
        return []
    written: list[str] = []
    if packaging == KubernetesPackaging.RAW_MANIFESTS:
        written.extend(_write_raw_manifests(workspace_dir, name, opts, cost, deps, cloud, spec))
    elif packaging == KubernetesPackaging.HELM:
        written.extend(_write_helm_chart(workspace_dir, name, opts, cost, deps, cloud, spec))
    elif packaging == KubernetesPackaging.KUSTOMIZE:
        written.extend(_write_kustomize_layout(workspace_dir, name, opts, cost, deps, cloud, spec))
    else:
        raise ValueError(f"Unsupported Kubernetes packaging: {packaging!r}")

    if opts.install_ingress_nginx:
        written.extend(_write_ingress_nginx_addon(workspace_dir))

    if cost.spot_scheduling.enabled:
        written.extend(_write_spot_provisioner_addon(workspace_dir, name, cost))

    return written


def _write_spot_provisioner_addon(
    workspace_dir: Path,
    name: str,
    cost: CostOptimizationConfig,
) -> list[str]:
    pct = cost.spot_scheduling.allocation_percent
    if cost.spot_scheduling.provisioner == SpotProvisionerStrategy.KARPENTER:
        rel = K8S_ADDONS_ROOT / "karpenter-nodepool.yaml"
        return [
            _write_relative(
                workspace_dir,
                rel,
                cost_marker_comment(cost) + karpenter_nodepool_yaml(name, pct),
            )
        ]
    rel = K8S_ADDONS_ROOT / "cluster-autoscaler-spot.md"
    return [
        _write_relative(
            workspace_dir,
            rel,
            cluster_autoscaler_notes_yaml(name, pct),
        )
    ]


def _write_relative(workspace_dir: Path, relative: Path, content: str) -> str:
    path = workspace_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(relative).replace("\\", "/")


def _namespace_name(name: str) -> str:
    """DNS-1123 subdomain namespace (max 63 chars)."""
    from app.services.terraform_bundle import sanitize_dns1123_name

    return sanitize_dns1123_name(name, max_len=63, prefix="lp")


# --------------------------------------------------------------------------- #
# Raw manifests
# --------------------------------------------------------------------------- #


def _write_raw_manifests(
    workspace_dir: Path,
    name: str,
    options: KubernetesWorkloadOptions,
    cost: CostOptimizationConfig,
    dependencies: WorkloadDependenciesConfig,
    cloud: object | None,
    workload: WorkloadImageSpec | None = None,
) -> list[str]:
    ns = _namespace_name(name)
    app = "app"
    spec = workload or WorkloadImageSpec()
    in_cluster = _in_cluster_kinds(dependencies)
    files: dict[str, str] = {
        "namespace.yaml": _namespace_yaml(ns, name),
    }
    for filename, content in in_cluster_manifest_files(ns=ns, name=name, kinds=in_cluster).items():
        files[filename] = content
    if options.deployment:
        files["deployment.yaml"] = _deployment_yaml(
            ns, name, app, options, cost, dependencies=dependencies, workload=spec
        )
    if options.service:
        files["service.yaml"] = _service_yaml(ns, name, app, workload=spec)
    if options.pod:
        files["pod.yaml"] = _pod_yaml(ns, name, app, workload=spec)
    if options.job:
        files["job.yaml"] = _job_yaml(ns, name, app)
    if options.cronjob:
        files["cronjob.yaml"] = _cronjob_yaml(ns, name, app)
    if options.statefulset:
        files["statefulset.yaml"] = _statefulset_yaml(ns, name, app, workload=spec)
    if options.daemonset:
        files["daemonset.yaml"] = _daemonset_yaml(ns, name, app)
    if options.service_account:
        files["serviceaccount.yaml"] = _serviceaccount_yaml(ns, name, app)
    if options.config_map:
        files["configmap.yaml"] = _configmap_yaml(ns, name, app, workload=spec)
    if options.secret or dependencies.any_enabled():
        files["secret.yaml"] = _secret_yaml(ns, name, app, dependencies, cloud)
    if options.ingress:
        files["ingress.yaml"] = _ingress_yaml(ns, name, app, options.ingress_class, workload=spec)
    if options.pvc:
        files["pvc.yaml"] = _pvc_yaml(ns, name, app)
    if options.role:
        files["role.yaml"] = _role_yaml(ns, name, app)
    if options.role_binding:
        files["rolebinding.yaml"] = _rolebinding_yaml(ns, name, app)
    if options.hpa:
        files["hpa.yaml"] = _hpa_yaml(ns, name, app, cost)
    if options.vpa:
        files["vpa.yaml"] = _vpa_yaml(ns, name, app, cost)
    if options.pdb:
        files["pdb.yaml"] = _pdb_yaml(ns, name, app)
    if options.network_policy:
        files["networkpolicy.yaml"] = _networkpolicy_yaml(ns, name, app)
    if options.resource_quota:
        settings = get_settings()
        files["resourcequota.yaml"] = render_resource_quota_yaml(
            namespace=ns,
            environment_name=name,
            settings=settings,
        )
    if options.limit_range:
        files["limitrange.yaml"] = render_limit_range_yaml(
            namespace=ns, environment_name=name
        )
    if cost.idle_shutdown.enabled and options.deployment:
        files["idle-shutdown.yaml"] = idle_shutdown_cronjobs_yaml(ns, name, app)

    written: list[str] = []
    for filename, content in files.items():
        written.append(_write_relative(workspace_dir, K8S_MANIFESTS_ROOT / filename, content))
    return written


def _common_labels(name: str, app: str, *, indent: int = 4) -> str:
    """Render common label key/value lines at the given indent (spaces)."""
    pad = " " * indent
    return (
        f"{pad}app: {app}\n"
        f"{pad}app.kubernetes.io/name: {app}\n"
        f"{pad}app.kubernetes.io/instance: {name}\n"
        f"{pad}app.kubernetes.io/managed-by: launchpad\n"
        f"{pad}launchpad.io/environment-name: {name}\n"
        f"{pad}launchpad.io/managed-by: launchpad-idp\n"
    )


def _namespace_yaml(ns: str, name: str) -> str:
    return f"""\
apiVersion: v1
kind: Namespace
metadata:
  name: {ns}
  labels:
    app.kubernetes.io/name: {name}
    launchpad.io/environment-name: {name}
    launchpad.io/managed-by: launchpad-idp
"""


def _serviceaccount_yaml(ns: str, name: str, app: str) -> str:
    return f"""\
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {app}
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
automountServiceAccountToken: false
"""


def _configmap_yaml(
    ns: str, name: str, app: str, workload: WorkloadImageSpec | None = None
) -> str:
    spec = workload or WorkloadImageSpec()
    return f"""\
apiVersion: v1
kind: ConfigMap
metadata:
  name: {app}-config
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
data:
  ENVIRONMENT_NAME: "{name}"
  LOG_LEVEL: "info"
  APP_PORT: "{spec.container_port}"
"""


def _secret_yaml(
    ns: str,
    name: str,
    app: str,
    dependencies: WorkloadDependenciesConfig | None = None,
    cloud: object | None = None,
) -> str:
    deps = dependencies or WorkloadDependenciesConfig()
    dep_data = dependency_secret_string_data(deps, name=name, cloud=cloud if isinstance(cloud, object) else None)
    string_lines = [
        "  # Replace placeholders before applying to a shared cluster.",
        "  APP_API_KEY: \"change-me\"",
    ]
    if not dep_data:
        string_lines.append(f'  DATABASE_URL: "postgres://user:pass@db:5432/{name}"')
    else:
        for key, value in dep_data.items():
            string_lines.append(f'  {key}: "{value}"')
    string_data = "\n".join(string_lines)
    return f"""\
apiVersion: v1
kind: Secret
metadata:
  name: {app}-secrets
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
type: Opaque
stringData:
{string_data}
"""


def _deployment_yaml(
    ns: str,
    name: str,
    app: str,
    options: KubernetesWorkloadOptions,
    cost: CostOptimizationConfig | None = None,
    dependencies: WorkloadDependenciesConfig | None = None,
    workload: WorkloadImageSpec | None = None,
) -> str:
    cost = cost or CostOptimizationConfig()
    deps = dependencies or WorkloadDependenciesConfig()
    spec = workload or WorkloadImageSpec()
    in_cluster = _in_cluster_kinds(deps)
    init_block = init_container_wait_blocks(in_cluster)
    sa_block = f"\n      serviceAccountName: {app}" if options.service_account else ""
    env_from_items: list[str] = []
    if options.config_map:
        env_from_items.append(
            f"""\
            - configMapRef:
                name: {app}-config"""
        )
    if options.secret or deps.any_enabled():
        env_from_items.append(
            f"""\
            - secretRef:
                name: {app}-secrets"""
        )
    env_from_block = ""
    if env_from_items:
        env_from_block = "\n          envFrom:\n" + "\n".join(env_from_items)

    cpu_req, mem_req, cpu_lim, mem_lim = resolve_resources(cost.resources)
    spot_block = spot_affinity_tolerations_yaml(cost, indent=6)
    spot_annotation = ""
    if cost.spot_scheduling.enabled:
        spot_annotation = (
            f"\n        launchpad.io/spot-allocation-percent: "
            f'"{cost.spot_scheduling.allocation_percent}"'
        )
    idle_annotation = ""
    if cost.idle_shutdown.enabled:
        idle_annotation = (
            '\n        downscaler/uptime: "Mon-Fri 07:00-19:00 Europe/Amsterdam"'
        )

    init_containers_section = f"\n      initContainers:{init_block}" if init_block else ""
    env_block = _render_workload_env_block(name, spec, deps)
    volume_mounts_block = _render_volume_mounts_block(spec)
    volumes_block = _render_volumes_block(spec)
    read_only = str(spec.read_only_root_fs).lower()

    return f"""\
{cost_marker_comment(cost)}\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {app}
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
spec:
  replicas: {spec.replicas}
  selector:
    matchLabels:
      app: {app}
      launchpad.io/managed-by: launchpad-idp
  template:
    metadata:
      labels:
{_common_labels(name, app, indent=8).rstrip()}
      annotations:
        launchpad.io/cost-optimized: "true"{spot_annotation}{idle_annotation}
    spec:{sa_block}
{spot_block}\
      securityContext:
        runAsNonRoot: true
        runAsUser: {spec.run_as_user}
        runAsGroup: {spec.run_as_user}
        seccompProfile:
          type: RuntimeDefault
{init_containers_section}
      containers:
        - name: {app}
          image: {spec.image}
          imagePullPolicy: {spec.image_pull_policy}
          ports:
            - name: http
              containerPort: {spec.container_port}
              protocol: TCP
          env:
{env_block}{env_from_block}
          resources:
            requests:
              cpu: {cpu_req}
              memory: {mem_req}
            limits:
              cpu: "{cpu_lim}"
              memory: {mem_lim}
          readinessProbe:
            httpGet:
              path: {spec.readiness_path}
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 3
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: {spec.liveness_path}
              port: http
            initialDelaySeconds: 15
            periodSeconds: 20
            timeoutSeconds: 3
            failureThreshold: 3
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: {read_only}
            runAsNonRoot: true
            runAsUser: {spec.run_as_user}
            capabilities:
              drop:
                - ALL{volume_mounts_block}{volumes_block}
"""


def additional_workload_manifests(
    workspace_dir: Path,
    *,
    env_name: str,
    services: list[dict[str, object]],
    dependencies: WorkloadDependenciesConfig | None = None,
) -> list[str]:
    """Write a Deployment + Service manifest pair per extra workload.

    Each entry is ``{name, image, port, service_type, selector}``. This lets a
    workspace host more than one Deployment/Service - the user picks the stack
    (which decides the image), the Service type, and the selector label per
    workload. Every workload also receives the ``app-secrets`` connection strings
    (``DATABASE_URL`` / ``REDIS_URL``) and ``HAS_DATABASE`` / ``HAS_REDIS`` flags
    when the workspace has in-cluster datastores, so each app can actually connect
    and surface database/Redis status.
    """
    deps = dependencies or WorkloadDependenciesConfig()
    ns = _namespace_name(env_name)
    written: list[str] = []
    for svc in services:
        wl_name = str(svc.get("name") or "app")
        selector = str(svc.get("selector") or wl_name)
        port = int(svc.get("port") or 8080)
        image = str(svc.get("image") or f"{wl_name}:latest")
        service_type = str(svc.get("service_type") or "ClusterIP")
        health_path = str(svc.get("health_path") or "/health")
        run_as_user = int(svc.get("run_as_user") or 10001)
        expose_preview = bool(svc.get("expose_preview"))
        service_name = f"{wl_name}-service"
        extra_env = svc.get("extra_env") if isinstance(svc.get("extra_env"), dict) else None
        written.append(
            _write_relative(
                workspace_dir,
                K8S_MANIFESTS_ROOT / f"{wl_name}-deployment.yaml",
                _named_deployment_yaml(
                    ns, env_name, wl_name, selector, image, port, health_path,
                    run_as_user, expose_preview, deps, extra_env,
                ),
            )
        )
        written.append(
            _write_relative(
                workspace_dir,
                K8S_MANIFESTS_ROOT / f"{wl_name}-service.yaml",
                _named_service_yaml(
                    ns, env_name, service_name, selector, port, service_type, expose_preview
                ),
            )
        )
    return written


def prune_orphan_default_manifests(workspace_dir: Path) -> list[str]:
    """Delete the generic ``deployment.yaml``/``service.yaml`` when stack-specific
    ``launch-*-deployment.yaml`` files exist.

    Prevents Launch Preview from picking the nginx-fallback ``deployment.yaml``
    over the real per-stack workloads. Returns the removed relative paths.
    """
    mdir = workspace_dir / K8S_MANIFESTS_ROOT
    if not mdir.is_dir():
        return []
    if not any(mdir.glob("launch-*-deployment.yaml")):
        return []
    removed: list[str] = []
    for fname in ("deployment.yaml", "service.yaml"):
        target = mdir / fname
        if target.is_file():
            target.unlink()
            removed.append(str(K8S_MANIFESTS_ROOT / fname).replace("\\", "/"))
    return removed


def _preview_target_annotation(expose_preview: bool, indent: int = 2) -> str:
    """Render the metadata.annotations block (empty when not exposed)."""
    if not expose_preview:
        return ""
    pad = " " * indent
    return f"{pad}annotations:\n{pad}  launchpad.io/preview-target: \"true\"\n"


def _named_deployment_yaml(
    ns: str,
    env_name: str,
    wl_name: str,
    selector: str,
    image: str,
    port: int,
    health_path: str,
    run_as_user: int,
    expose_preview: bool = False,
    dependencies: WorkloadDependenciesConfig | None = None,
    extra_env: dict[str, object] | None = None,
) -> str:
    deps = dependencies or WorkloadDependenciesConfig()
    annotations = _preview_target_annotation(expose_preview)
    # Full env block (HAS_DATABASE/HAS_REDIS + downward API) and connection strings
    # from app-secrets so this workload can reach the datastores and report status.
    spec = WorkloadImageSpec(image=image, container_port=port, app_version="1.0.0", replicas=1)
    env_block = _render_workload_env_block(env_name, spec, deps)
    # Per-workload extras - e.g. a frontend's API_URL pointing at the backend
    # Service so its dashboard can display the backend's live DB/Redis status.
    for key, value in (extra_env or {}).items():
        env_block += f'\n            - name: {key}\n              value: "{value}"'
    env_from_block = ""
    if deps.any_enabled():
        env_from_block = (
            "\n          envFrom:\n"
            "            - secretRef:\n"
            "                name: app-secrets\n"
            "                optional: true"
        )
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {wl_name}
  namespace: {ns}
{annotations}\
  labels:
    app: {selector}
    app.kubernetes.io/name: {wl_name}
    app.kubernetes.io/instance: {env_name}
    launchpad.io/environment-name: {env_name}
    launchpad.io/managed-by: launchpad-idp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {selector}
      launchpad.io/managed-by: launchpad-idp
  template:
    metadata:
      labels:
        app: {selector}
        app.kubernetes.io/name: {wl_name}
        launchpad.io/managed-by: launchpad-idp
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: {run_as_user}
        runAsGroup: {run_as_user}
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: {wl_name}
          image: {image}
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: {port}
              protocol: TCP
          env:
{env_block}{env_from_block}
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: "500m"
              memory: 512Mi
          readinessProbe:
            httpGet:
              path: {health_path}
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: {health_path}
              port: http
            initialDelaySeconds: 15
            periodSeconds: 20
          securityContext:
            allowPrivilegeEscalation: false
            runAsNonRoot: true
            runAsUser: {run_as_user}
            capabilities:
              drop:
                - ALL
"""


def _named_service_yaml(
    ns: str,
    env_name: str,
    service_name: str,
    selector: str,
    port: int,
    service_type: str,
    expose_preview: bool = False,
) -> str:
    annotations = _preview_target_annotation(expose_preview)
    return f"""\
apiVersion: v1
kind: Service
metadata:
  name: {service_name}
  namespace: {ns}
{annotations}\
  labels:
    app: {selector}
    app.kubernetes.io/name: {service_name}
    app.kubernetes.io/instance: {env_name}
    launchpad.io/environment-name: {env_name}
    launchpad.io/managed-by: launchpad-idp
spec:
  type: {service_type}
  selector:
    app: {selector}
    launchpad.io/managed-by: launchpad-idp
  ports:
    - name: http
      port: {port}
      targetPort: http
      protocol: TCP
"""


def build_multi_service_ingress(
    *,
    env_name: str,
    services: list[dict[str, object]],
    ingress_class: "IngressClassName | None" = None,
    host: str | None = None,
) -> str:
    """Assemble one workspace Ingress routing exposed services by path.

    The exposed frontend (``expose_preview`` / web-stack) is routed at ``/``; the
    remaining service is routed at ``/api`` (additional services get
    ``/api/<name>``). Backend service names are ``<name>-service`` and the
    backend port is the app's real container port.
    """
    ns = _namespace_name(env_name)
    class_name = (ingress_class or IngressClassName.NGINX).value
    ingress_host = host or f"{env_name}.preview.127.0.0.1.nip.io"

    primary = None
    for svc in services:
        if svc.get("expose_preview"):
            primary = svc
            break
    if primary is None and services:
        primary = services[0]

    ordered: list[tuple[str, dict]] = []
    if primary is not None:
        ordered.append(("/", primary))
    backends = [s for s in services if s is not primary]
    for idx, svc in enumerate(backends):
        path = "/api" if idx == 0 else f"/api/{svc.get('name')}"
        ordered.append((path, svc))

    path_lines: list[str] = []
    for path, svc in ordered:
        service_name = f"{svc.get('name')}-service"
        port = int(svc.get("port") or 8080)
        path_lines.append(
            "          - path: " + path + "\n"
            "            pathType: Prefix\n"
            "            backend:\n"
            "              service:\n"
            f"                name: {service_name}\n"
            "                port:\n"
            f"                  number: {port}"
        )
    paths_block = "\n".join(path_lines)

    return f"""\
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: launch-preview
  namespace: {ns}
  labels:
    app.kubernetes.io/instance: {env_name}
    launchpad.io/environment-name: {env_name}
    launchpad.io/managed-by: launchpad-idp
  annotations:
    kubernetes.io/ingress.class: {class_name}
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  ingressClassName: {class_name}
  rules:
    - host: {ingress_host}
      http:
        paths:
{paths_block}
"""


def _service_yaml(ns: str, name: str, app: str, workload: WorkloadImageSpec | None = None) -> str:
    spec = workload or WorkloadImageSpec()
    port = spec.container_port
    return f"""\
apiVersion: v1
kind: Service
metadata:
  name: {app}
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
spec:
  type: ClusterIP
  selector:
    app: {app}
    launchpad.io/managed-by: launchpad-idp
  ports:
    - name: http
      port: {port}
      targetPort: http
      protocol: TCP
"""


def _pod_yaml(
    ns: str, name: str, app: str, workload: WorkloadImageSpec | None = None
) -> str:
    spec = workload or WorkloadImageSpec()
    read_only = str(spec.read_only_root_fs).lower()
    return f"""\
apiVersion: v1
kind: Pod
metadata:
  name: {app}
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
spec:
  securityContext:
    runAsNonRoot: true
    runAsUser: {spec.run_as_user}
    runAsGroup: {spec.run_as_user}
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: {app}
      image: {spec.image}
      imagePullPolicy: {spec.image_pull_policy}
      ports:
        - name: http
          containerPort: {spec.container_port}
      resources:
        requests:
          cpu: 100m
          memory: 128Mi
        limits:
          cpu: 500m
          memory: 512Mi
      readinessProbe:
        httpGet:
          path: {spec.readiness_path}
          port: http
        initialDelaySeconds: 5
        periodSeconds: 10
      livenessProbe:
        httpGet:
          path: {spec.liveness_path}
          port: http
        initialDelaySeconds: 15
        periodSeconds: 20
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: {read_only}
        runAsNonRoot: true
        runAsUser: {spec.run_as_user}
        capabilities:
          drop:
            - ALL
"""


def _job_yaml(ns: str, name: str, app: str) -> str:
    return f"""\
apiVersion: batch/v1
kind: Job
metadata:
  name: {app}-migrate
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
spec:
  backoffLimit: 3
  template:
    metadata:
      labels:
{_common_labels(name, app, indent=8).rstrip()}
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: busybox:1.36
          command: ["sh", "-c", "echo migrate-{name} && exit 0"]
"""


def _cronjob_yaml(ns: str, name: str, app: str) -> str:
    return f"""\
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {app}-nightly
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
spec:
  schedule: "0 2 * * *"
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 1
  jobTemplate:
    spec:
      template:
        metadata:
          labels:
{_common_labels(name, app, indent=12).rstrip()}
        spec:
          restartPolicy: OnFailure
          containers:
            - name: task
              image: busybox:1.36
              command: ["sh", "-c", "echo nightly-{name} && exit 0"]
"""


def _statefulset_yaml(
    ns: str, name: str, app: str, workload: WorkloadImageSpec | None = None
) -> str:
    spec = workload or WorkloadImageSpec()
    return f"""\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {app}
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
spec:
  serviceName: {app}-headless
  replicas: {spec.replicas}
  selector:
    matchLabels:
      app: {app}
      launchpad.io/managed-by: launchpad-idp
  template:
    metadata:
      labels:
{_common_labels(name, app, indent=8).rstrip()}
    spec:
      containers:
        - name: {app}
          image: {spec.image}
          imagePullPolicy: {spec.image_pull_policy}
          ports:
            - name: http
              containerPort: {spec.container_port}
          volumeMounts:
            - name: data
              mountPath: /data
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 5Gi
"""


def _daemonset_yaml(ns: str, name: str, app: str) -> str:
    return f"""\
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: {app}-node-agent
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
spec:
  selector:
    matchLabels:
      app: {app}-node-agent
      launchpad.io/managed-by: launchpad-idp
  template:
    metadata:
      labels:
        app: {app}-node-agent
        app.kubernetes.io/name: {app}-node-agent
        app.kubernetes.io/instance: {name}
        launchpad.io/managed-by: launchpad-idp
    spec:
      containers:
        - name: agent
          image: busybox:1.36
          command: ["sh", "-c", "sleep infinity"]
"""


def _pvc_yaml(ns: str, name: str, app: str) -> str:
    return f"""\
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {app}-data
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
"""


def _role_yaml(ns: str, name: str, app: str) -> str:
    return f"""\
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {app}-reader
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
rules:
  - apiGroups: [""]
    resources: ["pods", "services", "configmaps", "secrets"]
    verbs: ["get", "list", "watch"]
"""


def _rolebinding_yaml(ns: str, name: str, app: str) -> str:
    return f"""\
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {app}-reader
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
subjects:
  - kind: ServiceAccount
    name: {app}
    namespace: {ns}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: {app}-reader
"""


def _ingress_yaml(
    ns: str,
    name: str,
    app: str,
    ingress_class: IngressClassName,
    workload: WorkloadImageSpec | None = None,
) -> str:
    spec = workload or WorkloadImageSpec()
    host = f"{name}.launchpad.local"
    class_name = ingress_class.value
    annotations = _ingress_annotations(ingress_class)
    # Route the Ingress backend to the app's real Service port (e.g. 8000 for
    # FastAPI, 3000 for Node/NestJS, 80 for a static SPA) instead of hardcoding 80.
    return f"""\
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {app}
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
  annotations:
{annotations}
spec:
  ingressClassName: {class_name}
  rules:
    - host: {host}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {app}
                port:
                  number: {spec.container_port}
"""


def _ingress_annotations(ingress_class: IngressClassName) -> str:
    if ingress_class == IngressClassName.NGINX:
        return (
            '    kubernetes.io/ingress.class: nginx\n'
            '    nginx.ingress.kubernetes.io/ssl-redirect: "true"\n'
        )
    if ingress_class == IngressClassName.TRAEFIK:
        return '    traefik.ingress.kubernetes.io/router.entrypoints: web,websecure\n'
    if ingress_class == IngressClassName.GCE:
        return '    kubernetes.io/ingress.class: gce\n'
    if ingress_class == IngressClassName.ALB:
        return (
            '    kubernetes.io/ingress.class: alb\n'
            '    alb.ingress.kubernetes.io/scheme: internet-facing\n'
            '    alb.ingress.kubernetes.io/target-type: ip\n'
        )
    if ingress_class == IngressClassName.AZURE_APPLICATION_GATEWAY:
        return '    kubernetes.io/ingress.class: azure-application-gateway\n'
    return '    projectcontour.io/ingress.class: contour\n'


def _hpa_yaml(
    ns: str,
    name: str,
    app: str,
    cost: CostOptimizationConfig | None = None,
) -> str:
    cost = cost or CostOptimizationConfig()
    min_r = cost.hpa.min_replicas if cost.hpa.enabled else 2
    max_r = cost.hpa.max_replicas if cost.hpa.enabled else 10
    cpu_target = cost.hpa.target_cpu_utilization if cost.hpa.enabled else 70
    return f"""\
{cost_marker_comment(cost)}\
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {app}
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {app}
  minReplicas: {min_r}
  maxReplicas: {max_r}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {cpu_target}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
"""


def _vpa_yaml(
    ns: str,
    name: str,
    app: str,
    cost: CostOptimizationConfig | None = None,
) -> str:
    cost = cost or CostOptimizationConfig()
    # Cost suite uses recommendation-only (Off). Bare toggle defaults to Auto.
    update_mode = "Off" if cost.vpa.enabled else "Auto"
    return f"""\
{cost_marker_comment(cost)}\
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: {app}
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {app}
  updatePolicy:
    updateMode: "{update_mode}"
  resourcePolicy:
    containerPolicies:
      - containerName: {app}
        minAllowed:
          cpu: 50m
          memory: 64Mi
        maxAllowed:
          cpu: "1"
          memory: 1Gi
        controlledResources:
          - cpu
          - memory
"""


def _pdb_yaml(ns: str, name: str, app: str) -> str:
    return f"""\
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {app}
  namespace: {ns}
  labels:
{_common_labels(name, app).rstrip()}
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: {app}
      launchpad.io/managed-by: launchpad-idp
"""


def _networkpolicy_yaml(ns: str, name: str, app: str) -> str:
    return render_workspace_network_policy_yaml(
        namespace=ns,
        environment_name=name,
        app=app,
        common_labels=_common_labels(name, app),
    )


def _resourcequota_yaml(ns: str, name: str) -> str:
    settings = get_settings()
    return render_resource_quota_yaml(namespace=ns, environment_name=name, settings=settings)


def _limitrange_yaml(ns: str, name: str) -> str:
    return render_limit_range_yaml(namespace=ns, environment_name=name)


def _write_ingress_nginx_addon(workspace_dir: Path) -> list[str]:
    values = """\
controller:
  replicaCount: 2
  service:
    type: LoadBalancer
  metrics:
    enabled: true
  allowSnippetAnnotations: false
  config:
    use-forwarded-headers: "true"
    compute-full-forwarded-for: "true"
  admissionWebhooks:
    enabled: true
defaultBackend:
  enabled: true
"""
    return [_write_relative(workspace_dir, INGRESS_NGINX_VALUES, values)]


# --------------------------------------------------------------------------- #
# Kustomize layout
# --------------------------------------------------------------------------- #

KUSTOMIZE_BASE_ROOT = Path("infra") / "kustomize" / "base"
KUSTOMIZE_OVERLAY_ROOT = Path("infra") / "kustomize" / "overlays" / "prod"


def _write_kustomize_layout(
    workspace_dir: Path,
    name: str,
    options: KubernetesWorkloadOptions,
    cost: CostOptimizationConfig,
    dependencies: WorkloadDependenciesConfig,
    cloud: object | None,
    workload: WorkloadImageSpec | None = None,
) -> list[str]:
    ns = _namespace_name(name)
    spec = workload or WorkloadImageSpec()
    in_cluster = _in_cluster_kinds(dependencies)
    base_resources = ["namespace.yaml", "deployment.yaml", "service.yaml"]
    for filename in in_cluster_manifest_files(ns=ns, name=name, kinds=in_cluster):
        base_resources.append(filename.replace(".yaml", ".yaml"))
    kustomization_resources = "\n".join(f"  - {r}" for r in base_resources)
    if dependencies.any_enabled():
        base_resources.append("secret.yaml")
        kustomization_resources = "\n".join(f"  - {r}" for r in base_resources)
    files: dict[Path, str] = {
        KUSTOMIZE_BASE_ROOT / "kustomization.yaml": "\n".join(
            [
                "apiVersion: kustomize.config.k8s.io/v1beta1",
                "kind: Kustomization",
                "resources:",
                kustomization_resources,
                "",
            ]
        ),
        KUSTOMIZE_BASE_ROOT / "namespace.yaml": _namespace_yaml(ns, name),
        KUSTOMIZE_BASE_ROOT / "deployment.yaml": _deployment_yaml(
            ns, name, "app", options, cost, dependencies=dependencies, workload=spec
        ),
        KUSTOMIZE_BASE_ROOT / "service.yaml": _service_yaml(ns, name, "app", workload=spec),
    }
    for filename, content in in_cluster_manifest_files(ns=ns, name=name, kinds=in_cluster).items():
        files[KUSTOMIZE_BASE_ROOT / filename] = content
    if dependencies.any_enabled() or options.secret:
        files[KUSTOMIZE_BASE_ROOT / "secret.yaml"] = _secret_yaml(
            ns, name, "app", dependencies, cloud
        )
    files[KUSTOMIZE_OVERLAY_ROOT / "kustomization.yaml"] = "\n".join(
        [
            "apiVersion: kustomize.config.k8s.io/v1beta1",
            "kind: Kustomization",
            "resources:",
            "  - ../../base",
            "namePrefix: prod-",
            "",
        ]
    )
    written: list[str] = []
    for rel_path, content in files.items():
        written.append(_write_relative(workspace_dir, rel_path, content))
    return written


# --------------------------------------------------------------------------- #
# Helm chart
# --------------------------------------------------------------------------- #


def _write_helm_chart(
    workspace_dir: Path,
    name: str,
    options: KubernetesWorkloadOptions,
    cost: CostOptimizationConfig,
    dependencies: WorkloadDependenciesConfig,
    cloud: object | None,
    workload: WorkloadImageSpec | None = None,
) -> list[str]:
    ns = _namespace_name(name)
    spec = workload or WorkloadImageSpec()
    in_cluster = _in_cluster_kinds(dependencies)
    files: dict[Path, str] = {
        HELM_CHART_ROOT / "Chart.yaml": _helm_chart_yaml(name),
        HELM_CHART_ROOT / "values.yaml": _helm_values_yaml(name, options, cost, spec),
        HELM_CHART_ROOT / "templates" / "_helpers.tpl": _helm_helpers_tpl(),
    }
    if options.deployment:
        files[HELM_CHART_ROOT / "templates" / "deployment.yaml"] = _helm_deployment_yaml()
    if options.service:
        files[HELM_CHART_ROOT / "templates" / "service.yaml"] = _helm_service_yaml()
    if options.service_account:
        files[HELM_CHART_ROOT / "templates" / "serviceaccount.yaml"] = _helm_serviceaccount_yaml()
    if options.config_map:
        files[HELM_CHART_ROOT / "templates" / "configmap.yaml"] = _helm_configmap_yaml()
    if options.secret or dependencies.any_enabled():
        files[HELM_CHART_ROOT / "templates" / "secret.yaml"] = _helm_secret_yaml()
    for filename, content in in_cluster_manifest_files(ns=ns, name=name, kinds=in_cluster).items():
        files[HELM_CHART_ROOT / "templates" / filename] = content
    if options.ingress:
        files[HELM_CHART_ROOT / "templates" / "ingress.yaml"] = _helm_ingress_yaml()
    if options.hpa:
        files[HELM_CHART_ROOT / "templates" / "hpa.yaml"] = _helm_hpa_yaml()
    if options.vpa:
        files[HELM_CHART_ROOT / "templates" / "vpa.yaml"] = _helm_vpa_yaml()
    if options.pdb:
        files[HELM_CHART_ROOT / "templates" / "pdb.yaml"] = _helm_pdb_yaml()
    if options.network_policy:
        files[HELM_CHART_ROOT / "templates" / "networkpolicy.yaml"] = (
            _helm_networkpolicy_yaml()
        )
    if options.resource_quota:
        files[HELM_CHART_ROOT / "templates" / "resourcequota.yaml"] = (
            _helm_resourcequota_yaml()
        )
    if options.limit_range:
        files[HELM_CHART_ROOT / "templates" / "limitrange.yaml"] = _helm_limitrange_yaml()
    # Raw-style extras as optional chart templates when selected
    if options.pod:
        files[HELM_CHART_ROOT / "templates" / "pod.yaml"] = _pod_yaml(
            "{{ .Release.Namespace }}", name, "app"
        )
    if options.job:
        files[HELM_CHART_ROOT / "templates" / "job.yaml"] = _job_yaml(
            "{{ .Release.Namespace }}", name, "app"
        )
    if options.cronjob:
        files[HELM_CHART_ROOT / "templates" / "cronjob.yaml"] = _cronjob_yaml(
            "{{ .Release.Namespace }}", name, "app"
        )
    if options.statefulset:
        files[HELM_CHART_ROOT / "templates" / "statefulset.yaml"] = _statefulset_yaml(
            "{{ .Release.Namespace }}", name, "app"
        )
    if options.daemonset:
        files[HELM_CHART_ROOT / "templates" / "daemonset.yaml"] = _daemonset_yaml(
            "{{ .Release.Namespace }}", name, "app"
        )
    if options.pvc:
        files[HELM_CHART_ROOT / "templates" / "pvc.yaml"] = _pvc_yaml(
            "{{ .Release.Namespace }}", name, "app"
        )
    if options.role:
        files[HELM_CHART_ROOT / "templates" / "role.yaml"] = _role_yaml(
            "{{ .Release.Namespace }}", name, "app"
        )
    if options.role_binding:
        files[HELM_CHART_ROOT / "templates" / "rolebinding.yaml"] = _rolebinding_yaml(
            "{{ .Release.Namespace }}", name, "app"
        )

    written: list[str] = []
    for relative, content in files.items():
        written.append(_write_relative(workspace_dir, relative, content))
    return written


def _helm_chart_yaml(name: str) -> str:
    return f"""\
apiVersion: v2
name: app-chart
description: Launchpad application chart for {name}
type: application
version: 0.1.0
appVersion: "1.0.0"
keywords:
  - launchpad
  - application
maintainers:
  - name: launchpad
"""


def _helm_values_yaml(
    name: str,
    options: KubernetesWorkloadOptions,
    cost: CostOptimizationConfig,
    workload: WorkloadImageSpec | None = None,
) -> str:
    spec = workload or WorkloadImageSpec()
    cpu_req, mem_req, cpu_lim, mem_lim = resolve_resources(cost.resources)
    min_r = cost.hpa.min_replicas if cost.hpa.enabled else 2
    max_r = cost.hpa.max_replicas if cost.hpa.enabled else 10
    cpu_target = cost.hpa.target_cpu_utilization if cost.hpa.enabled else 70
    vpa_mode = "Off" if cost.vpa.enabled else "Auto"
    spot_fragment = spot_helm_values_fragment(cost)
    idle_enabled = str(cost.idle_shutdown.enabled).lower()
    return f"""\
{cost_marker_comment(cost)}\
nameOverride: ""
fullnameOverride: ""

replicaCount: 2

image:
  repository: {spec.image_repository}
  tag: "{spec.image_tag}"
  pullPolicy: {spec.image_pull_policy}

serviceAccount:
  create: {str(options.service_account).lower()}
  name: ""
  automount: false

service:
  type: ClusterIP
  port: {spec.container_port}
  targetPort: {spec.container_port}

configMap:
  enabled: {str(options.config_map).lower()}
  data:
    ENVIRONMENT_NAME: "{name}"
    LOG_LEVEL: "info"
    APP_PORT: "{spec.container_port}"

secret:
  enabled: {str(options.secret).lower()}
  stringData:
    APP_API_KEY: "change-me"
    DATABASE_URL: "postgres://user:pass@db:5432/{name}"

ingress:
  enabled: {str(options.ingress).lower()}
  className: {options.ingress_class.value}
  annotations:
{_helm_ingress_annotations_block(options.ingress_class)}
  hosts:
    - host: {name}.launchpad.local
      paths:
        - path: /
          pathType: Prefix
  tls: []

autoscaling:
  enabled: {str(options.hpa).lower()}
  minReplicas: {min_r}
  maxReplicas: {max_r}
  targetCPUUtilizationPercentage: {cpu_target}
  targetMemoryUtilizationPercentage: 80

vpa:
  enabled: {str(options.vpa).lower()}
  updateMode: {vpa_mode}

pdb:
  enabled: {str(options.pdb).lower()}
  minAvailable: 1

networkPolicy:
  enabled: {str(options.network_policy).lower()}

resourceQuota:
  enabled: {str(options.resource_quota).lower()}
  hard:
    requests.cpu: "2"
    requests.memory: 4Gi
    limits.cpu: "4"
    limits.memory: 8Gi
    pods: "20"

limitRange:
  enabled: {str(options.limit_range).lower()}

resources:
  requests:
    cpu: {cpu_req}
    memory: {mem_req}
  limits:
    cpu: {cpu_lim}
    memory: {mem_lim}

idleShutdown:
  enabled: {idle_enabled}
  schedule: weeknights_weekends

{spot_fragment.rstrip()}

readinessProbe:
  httpGet:
    path: {spec.readiness_path}
    port: http
  initialDelaySeconds: 5
  periodSeconds: 10

livenessProbe:
  httpGet:
    path: {spec.liveness_path}
    port: http
  initialDelaySeconds: 15
  periodSeconds: 20

env:
  ENVIRONMENT_NAME: {name}
  APP_VERSION: "{spec.app_version}"

podSecurityContext:
  runAsNonRoot: true
  runAsUser: {spec.run_as_user}
  runAsGroup: {spec.run_as_user}
  seccompProfile:
    type: RuntimeDefault

securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: {str(spec.read_only_root_fs).lower()}
  runAsNonRoot: true
  runAsUser: {spec.run_as_user}
  capabilities:
    drop:
      - ALL
"""


def _helm_ingress_annotations_block(ingress_class: IngressClassName) -> str:
    raw = _ingress_annotations(ingress_class)
    # Convert "    key: value\n" annotation lines into YAML mapping under values.
    lines = []
    for line in raw.strip("\n").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lines.append(f"    {stripped}")
    return "\n".join(lines) if lines else "    {}"


def _helm_helpers_tpl() -> str:
    return """\
{{/*
Expand the name of the chart.
*/}}
{{- define "app-chart.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "app-chart.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "app-chart.labels" -}}
helm.sh/chart: {{ include "app-chart.chart" . }}
{{ include "app-chart.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
launchpad.io/managed-by: launchpad-idp
{{- end }}

{{/*
Selector labels
*/}}
{{- define "app-chart.selectorLabels" -}}
app.kubernetes.io/name: {{ include "app-chart.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app: {{ include "app-chart.name" . }}
{{- end }}

{{/*
Service account name
*/}}
{{- define "app-chart.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "app-chart.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Chart label value
*/}}
{{- define "app-chart.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}
"""


def _helm_serviceaccount_yaml() -> str:
    return """\
{{- if .Values.serviceAccount.create -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "app-chart.serviceAccountName" . }}
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
automountServiceAccountToken: {{ .Values.serviceAccount.automount }}
{{- end }}
"""


def _helm_configmap_yaml() -> str:
    return """\
{{- if .Values.configMap.enabled -}}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "app-chart.fullname" . }}-config
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
data:
  {{- toYaml .Values.configMap.data | nindent 2 }}
{{- end }}
"""


def _helm_secret_yaml() -> str:
    return """\
{{- if .Values.secret.enabled -}}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "app-chart.fullname" . }}-secrets
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
type: Opaque
stringData:
  {{- toYaml .Values.secret.stringData | nindent 2 }}
{{- end }}
"""


def _helm_deployment_yaml() -> str:
    return """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "app-chart.fullname" . }}
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
spec:
  {{- if not .Values.autoscaling.enabled }}
  replicas: {{ .Values.replicaCount }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "app-chart.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "app-chart.labels" . | nindent 8 }}
    spec:
      {{- if .Values.serviceAccount.create }}
      serviceAccountName: {{ include "app-chart.serviceAccountName" . }}
      {{- end }}
      {{- with .Values.podSecurityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: {{ .Chart.Name }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - name: http
              containerPort: {{ .Values.service.targetPort }}
              protocol: TCP
          {{- if .Values.env }}
          env:
            {{- range $key, $value := .Values.env }}
            - name: {{ $key }}
              value: {{ $value | quote }}
            {{- end }}
          {{- end }}
          envFrom:
            {{- if .Values.configMap.enabled }}
            - configMapRef:
                name: {{ include "app-chart.fullname" . }}-config
            {{- end }}
            {{- if .Values.secret.enabled }}
            - secretRef:
                name: {{ include "app-chart.fullname" . }}-secrets
            {{- end }}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          readinessProbe:
            {{- toYaml .Values.readinessProbe | nindent 12 }}
          livenessProbe:
            {{- toYaml .Values.livenessProbe | nindent 12 }}
          {{- with .Values.securityContext }}
          securityContext:
            {{- toYaml . | nindent 12 }}
          {{- end }}
"""


def _helm_service_yaml() -> str:
    return """\
apiVersion: v1
kind: Service
metadata:
  name: {{ include "app-chart.fullname" . }}
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type }}
  selector:
    {{- include "app-chart.selectorLabels" . | nindent 4 }}
  ports:
    - name: http
      port: {{ .Values.service.port }}
      targetPort: http
      protocol: TCP
"""


def _helm_ingress_yaml() -> str:
    return """\
{{- if .Values.ingress.enabled -}}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "app-chart.fullname" . }}
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
  {{- with .Values.ingress.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  {{- if .Values.ingress.className }}
  ingressClassName: {{ .Values.ingress.className }}
  {{- end }}
  {{- if .Values.ingress.tls }}
  tls:
    {{- toYaml .Values.ingress.tls | nindent 4 }}
  {{- end }}
  rules:
    {{- range .Values.ingress.hosts }}
    - host: {{ .host | quote }}
      http:
        paths:
          {{- range .paths }}
          - path: {{ .path }}
            pathType: {{ .pathType }}
            backend:
              service:
                name: {{ include "app-chart.fullname" $ }}
                port:
                  name: http
          {{- end }}
    {{- end }}
{{- end }}
"""


def _helm_hpa_yaml() -> str:
    return """\
{{- if .Values.autoscaling.enabled }}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "app-chart.fullname" . }}
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "app-chart.fullname" . }}
  minReplicas: {{ .Values.autoscaling.minReplicas }}
  maxReplicas: {{ .Values.autoscaling.maxReplicas }}
  metrics:
    {{- if .Values.autoscaling.targetCPUUtilizationPercentage }}
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .Values.autoscaling.targetCPUUtilizationPercentage }}
    {{- end }}
    {{- if .Values.autoscaling.targetMemoryUtilizationPercentage }}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: {{ .Values.autoscaling.targetMemoryUtilizationPercentage }}
    {{- end }}
{{- end }}
"""


def _helm_vpa_yaml() -> str:
    return """\
{{- if .Values.vpa.enabled }}
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: {{ include "app-chart.fullname" . }}
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "app-chart.fullname" . }}
  updatePolicy:
    updateMode: {{ .Values.vpa.updateMode | quote }}
  resourcePolicy:
    containerPolicies:
      - containerName: {{ .Chart.Name }}
        controlledResources:
          - cpu
          - memory
{{- end }}
"""


def _helm_pdb_yaml() -> str:
    return """\
{{- if .Values.pdb.enabled }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "app-chart.fullname" . }}
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
spec:
  minAvailable: {{ .Values.pdb.minAvailable }}
  selector:
    matchLabels:
      {{- include "app-chart.selectorLabels" . | nindent 6 }}
{{- end }}
"""


def _helm_networkpolicy_yaml() -> str:
    return """\
{{- if .Values.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "app-chart.fullname" . }}-zero-trust
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "app-chart.selectorLabels" . | nindent 6 }}
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress-nginx
        - podSelector:
            matchLabels:
              {{- include "app-chart.selectorLabels" . | nindent 14 }}
      ports:
        - protocol: TCP
          port: {{ .Values.service.port }}
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # Same-namespace pods (any port) so the app can reach in-cluster datastores.
    - to:
        - podSelector: {}
{{- end }}
"""


def _helm_resourcequota_yaml() -> str:
    return """\
{{- if .Values.resourceQuota.enabled }}
apiVersion: v1
kind: ResourceQuota
metadata:
  name: {{ include "app-chart.fullname" . }}-quota
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
spec:
  hard:
    {{- toYaml .Values.resourceQuota.hard | nindent 4 }}
{{- end }}
"""


def _helm_limitrange_yaml() -> str:
    return """\
{{- if .Values.limitRange.enabled }}
apiVersion: v1
kind: LimitRange
metadata:
  name: {{ include "app-chart.fullname" . }}-limits
  labels:
    {{- include "app-chart.labels" . | nindent 4 }}
spec:
  limits:
    - type: Container
      default:
        cpu: 250m
        memory: 256Mi
      defaultRequest:
        cpu: 100m
        memory: 128Mi
{{- end }}
"""

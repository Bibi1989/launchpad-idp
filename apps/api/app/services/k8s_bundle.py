"""Kubernetes raw-manifest and Helm chart layout generators under ``infra/``.

Emits either:

- ``infra/k8s/manifests/`` — production-oriented raw Kubernetes objects
- ``infra/helm/app-chart/`` — standard Helm v3 chart layout

Optional add-ons (ingress-nginx) land under ``infra/k8s/addons/``.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.schemas.cloud import (
    CostOptimizationConfig,
    IngressClassName,
    KubernetesPackaging,
    KubernetesWorkloadOptions,
    SpotProvisionerStrategy,
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


def write_kubernetes_layout(
    workspace_dir: Path,
    *,
    name: str,
    packaging: KubernetesPackaging,
    options: KubernetesWorkloadOptions | None = None,
    cost_optimization: CostOptimizationConfig | None = None,
) -> list[str]:
    """Writes the selected Kubernetes packaging layout; returns relative paths."""
    opts = options or KubernetesWorkloadOptions()
    cost = cost_optimization or CostOptimizationConfig()
    if packaging == KubernetesPackaging.NONE:
        return []
    written: list[str] = []
    if packaging == KubernetesPackaging.RAW_MANIFESTS:
        written.extend(_write_raw_manifests(workspace_dir, name, opts, cost))
    elif packaging == KubernetesPackaging.HELM:
        written.extend(_write_helm_chart(workspace_dir, name, opts, cost))
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
    return f"lp-{name}"


# --------------------------------------------------------------------------- #
# Raw manifests
# --------------------------------------------------------------------------- #


def _write_raw_manifests(
    workspace_dir: Path,
    name: str,
    options: KubernetesWorkloadOptions,
    cost: CostOptimizationConfig,
) -> list[str]:
    ns = _namespace_name(name)
    app = "app"
    files: dict[str, str] = {
        "namespace.yaml": _namespace_yaml(ns, name),
    }
    if options.deployment:
        files["deployment.yaml"] = _deployment_yaml(ns, name, app, options, cost)
    if options.service:
        files["service.yaml"] = _service_yaml(ns, name, app)
    if options.pod:
        files["pod.yaml"] = _pod_yaml(ns, name, app)
    if options.job:
        files["job.yaml"] = _job_yaml(ns, name, app)
    if options.cronjob:
        files["cronjob.yaml"] = _cronjob_yaml(ns, name, app)
    if options.statefulset:
        files["statefulset.yaml"] = _statefulset_yaml(ns, name, app)
    if options.daemonset:
        files["daemonset.yaml"] = _daemonset_yaml(ns, name, app)
    if options.service_account:
        files["serviceaccount.yaml"] = _serviceaccount_yaml(ns, name, app)
    if options.config_map:
        files["configmap.yaml"] = _configmap_yaml(ns, name, app)
    if options.secret:
        files["secret.yaml"] = _secret_yaml(ns, name, app)
    if options.ingress:
        files["ingress.yaml"] = _ingress_yaml(ns, name, app, options.ingress_class)
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


def _configmap_yaml(ns: str, name: str, app: str) -> str:
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
  APP_PORT: "80"
"""


def _secret_yaml(ns: str, name: str, app: str) -> str:
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
  # Replace placeholders before applying to a shared cluster.
  APP_API_KEY: "change-me"
  DATABASE_URL: "postgres://user:pass@db:5432/{name}"
"""


def _deployment_yaml(
    ns: str,
    name: str,
    app: str,
    options: KubernetesWorkloadOptions,
    cost: CostOptimizationConfig | None = None,
) -> str:
    cost = cost or CostOptimizationConfig()
    sa_block = f"\n      serviceAccountName: {app}" if options.service_account else ""
    env_from_items: list[str] = []
    if options.config_map:
        env_from_items.append(
            f"""\
            - configMapRef:
                name: {app}-config"""
        )
    if options.secret:
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
  replicas: 2
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
        runAsUser: 101
        runAsGroup: 101
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: {app}
          image: nginx:1.27-alpine
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 80
              protocol: TCP
          env:
            - name: ENVIRONMENT_NAME
              value: {name}{env_from_block}
          resources:
            requests:
              cpu: {cpu_req}
              memory: {mem_req}
            limits:
              cpu: "{cpu_lim}"
              memory: {mem_lim}
          readinessProbe:
            httpGet:
              path: /
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 3
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /
              port: http
            initialDelaySeconds: 15
            periodSeconds: 20
            timeoutSeconds: 3
            failureThreshold: 3
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            runAsUser: 101
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: tmp
              mountPath: /tmp
            - name: cache
              mountPath: /var/cache/nginx
            - name: run
              mountPath: /var/run
      volumes:
        - name: tmp
          emptyDir: {{}}
        - name: cache
          emptyDir: {{}}
        - name: run
          emptyDir: {{}}
"""


def _service_yaml(ns: str, name: str, app: str) -> str:
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
      port: 80
      targetPort: http
      protocol: TCP
"""


def _pod_yaml(ns: str, name: str, app: str) -> str:
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
    runAsUser: 101
    runAsGroup: 101
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: {app}
      image: nginx:1.27-alpine
      ports:
        - name: http
          containerPort: 80
      resources:
        requests:
          cpu: 100m
          memory: 128Mi
        limits:
          cpu: 500m
          memory: 512Mi
      readinessProbe:
        httpGet:
          path: /
          port: http
        initialDelaySeconds: 5
        periodSeconds: 10
      livenessProbe:
        httpGet:
          path: /
          port: http
        initialDelaySeconds: 15
        periodSeconds: 20
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        runAsNonRoot: true
        runAsUser: 101
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


def _statefulset_yaml(ns: str, name: str, app: str) -> str:
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
  replicas: 2
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
          image: nginx:1.27-alpine
          ports:
            - name: http
              containerPort: 80
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


def _ingress_yaml(ns: str, name: str, app: str, ingress_class: IngressClassName) -> str:
    host = f"{name}.launchpad.local"
    class_name = ingress_class.value
    annotations = _ingress_annotations(ingress_class)
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
                  name: http
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
# Helm chart
# --------------------------------------------------------------------------- #


def _write_helm_chart(
    workspace_dir: Path,
    name: str,
    options: KubernetesWorkloadOptions,
    cost: CostOptimizationConfig,
) -> list[str]:
    files: dict[Path, str] = {
        HELM_CHART_ROOT / "Chart.yaml": _helm_chart_yaml(name),
        HELM_CHART_ROOT / "values.yaml": _helm_values_yaml(name, options, cost),
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
    if options.secret:
        files[HELM_CHART_ROOT / "templates" / "secret.yaml"] = _helm_secret_yaml()
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
) -> str:
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
  repository: nginx
  tag: "1.27-alpine"
  pullPolicy: IfNotPresent

serviceAccount:
  create: {str(options.service_account).lower()}
  name: ""
  automount: false

service:
  type: ClusterIP
  port: 80
  targetPort: 80

configMap:
  enabled: {str(options.config_map).lower()}
  data:
    ENVIRONMENT_NAME: "{name}"
    LOG_LEVEL: "info"
    APP_PORT: "80"

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
    path: /
    port: http
  initialDelaySeconds: 5
  periodSeconds: 10

livenessProbe:
  httpGet:
    path: /
    port: http
  initialDelaySeconds: 15
  periodSeconds: 20

env:
  ENVIRONMENT_NAME: {name}

podSecurityContext:
  runAsNonRoot: true
  runAsUser: 101
  runAsGroup: 101
  seccompProfile:
    type: RuntimeDefault

securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  runAsUser: 101
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

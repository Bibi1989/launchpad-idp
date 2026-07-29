"""Rich Kubernetes and Terraform file templates for the workspace IDE."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkspaceTemplate:
    id: str
    label: str
    category: str
    description: str
    default_path: str
    content: str


def _k8s(
    kind_id: str,
    label: str,
    description: str,
    filename: str,
    content: str,
) -> WorkspaceTemplate:
    return WorkspaceTemplate(
        id=f"k8s.{kind_id}",
        label=label,
        category="kubernetes",
        description=description,
        default_path=f"infra/k8s/manifests/{filename}",
        content=content,
    )


def _tf(
    kind_id: str,
    label: str,
    description: str,
    filename: str,
    content: str,
) -> WorkspaceTemplate:
    return WorkspaceTemplate(
        id=f"terraform.{kind_id}",
        label=label,
        category="terraform",
        description=description,
        default_path=f"infra/terraform/{filename}",
        content=content,
    )


_K8S_TEMPLATES: tuple[WorkspaceTemplate, ...] = (
    _k8s(
        "namespace",
        "Namespace",
        "Isolated cluster scope for related objects",
        "namespace.yaml",
        """\
apiVersion: v1
kind: Namespace
metadata:
  name: lp-app
  labels:
    app.kubernetes.io/managed-by: launchpad
""",
    ),
    _k8s(
        "pod",
        "Pod",
        "Single runnable unit (prefer Deployment for production)",
        "pod.yaml",
        """\
apiVersion: v1
kind: Pod
metadata:
  name: app
  namespace: lp-app
  labels:
    app: app
spec:
  containers:
    - name: app
      image: nginx:1.27-alpine
      ports:
        - containerPort: 80
      resources:
        requests:
          cpu: 100m
          memory: 128Mi
        limits:
          cpu: 500m
          memory: 256Mi
""",
    ),
    _k8s(
        "deployment",
        "Deployment",
        "Stateless workload with rolling updates",
        "deployment.yaml",
        """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
  namespace: lp-app
  labels:
    app: app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: app
  template:
    metadata:
      labels:
        app: app
    spec:
      containers:
        - name: app
          image: nginx:1.27-alpine
          ports:
            - name: http
              containerPort: 80
          readinessProbe:
            httpGet:
              path: /
              port: http
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 256Mi
""",
    ),
    _k8s(
        "statefulset",
        "StatefulSet",
        "Ordered pods with stable network identity",
        "statefulset.yaml",
        """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: app
  namespace: lp-app
spec:
  serviceName: app-headless
  replicas: 2
  selector:
    matchLabels:
      app: app
  template:
    metadata:
      labels:
        app: app
    spec:
      containers:
        - name: app
          image: nginx:1.27-alpine
          ports:
            - containerPort: 80
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
""",
    ),
    _k8s(
        "daemonset",
        "DaemonSet",
        "One pod per node (agents, log collectors)",
        "daemonset.yaml",
        """\
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: node-agent
  namespace: lp-app
spec:
  selector:
    matchLabels:
      app: node-agent
  template:
    metadata:
      labels:
        app: node-agent
    spec:
      containers:
        - name: agent
          image: busybox:1.36
          command: ["sh", "-c", "sleep infinity"]
""",
    ),
    _k8s(
        "job",
        "Job",
        "Run-to-completion batch task",
        "job.yaml",
        """\
apiVersion: batch/v1
kind: Job
metadata:
  name: migrate
  namespace: lp-app
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: busybox:1.36
          command: ["sh", "-c", "echo migrate && exit 0"]
""",
    ),
    _k8s(
        "cronjob",
        "CronJob",
        "Scheduled Jobs on a cron timetable",
        "cronjob.yaml",
        """\
apiVersion: batch/v1
kind: CronJob
metadata:
  name: nightly
  namespace: lp-app
spec:
  schedule: "0 2 * * *"
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 1
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: task
              image: busybox:1.36
              command: ["sh", "-c", "echo nightly && exit 0"]
""",
    ),
    _k8s(
        "service",
        "Service",
        "Stable ClusterIP / NodePort / LoadBalancer endpoint",
        "service.yaml",
        """\
apiVersion: v1
kind: Service
metadata:
  name: app
  namespace: lp-app
spec:
  type: ClusterIP
  selector:
    app: app
  ports:
    - name: http
      port: 80
      targetPort: 80
""",
    ),
    _k8s(
        "ingress",
        "Ingress",
        "HTTP(S) routing to Services",
        "ingress.yaml",
        """\
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: app
  namespace: lp-app
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  rules:
    - host: app.launchpad.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: app
                port:
                  number: 80
""",
    ),
    _k8s(
        "configmap",
        "ConfigMap",
        "Non-secret configuration data",
        "configmap.yaml",
        """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
  namespace: lp-app
data:
  LOG_LEVEL: info
  APP_PORT: "80"
""",
    ),
    _k8s(
        "secret",
        "Secret",
        "Opaque secret material (base64 or stringData)",
        "secret.yaml",
        """\
apiVersion: v1
kind: Secret
metadata:
  name: app-secrets
  namespace: lp-app
type: Opaque
stringData:
  APP_API_KEY: change-me
""",
    ),
    _k8s(
        "serviceaccount",
        "ServiceAccount",
        "Identity for pods talking to the API",
        "serviceaccount.yaml",
        """\
apiVersion: v1
kind: ServiceAccount
metadata:
  name: app
  namespace: lp-app
automountServiceAccountToken: false
""",
    ),
    _k8s(
        "pvc",
        "PersistentVolumeClaim",
        "Request durable storage for a pod",
        "pvc.yaml",
        """\
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: app-data
  namespace: lp-app
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
""",
    ),
    _k8s(
        "hpa",
        "HorizontalPodAutoscaler",
        "Scale Deployments on CPU/memory",
        "hpa.yaml",
        """\
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: app
  namespace: lp-app
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: app
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
""",
    ),
    _k8s(
        "pdb",
        "PodDisruptionBudget",
        "Limit voluntary disruptions during drains",
        "pdb.yaml",
        """\
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: app
  namespace: lp-app
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: app
""",
    ),
    _k8s(
        "networkpolicy",
        "NetworkPolicy",
        "Pod-level ingress/egress firewall rules",
        "networkpolicy.yaml",
        """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: app-allow
  namespace: lp-app
spec:
  podSelector:
    matchLabels:
      app: app
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - podSelector: {}
      ports:
        - protocol: TCP
          port: 80
  egress:
    - {}
""",
    ),
    _k8s(
        "resourcequota",
        "ResourceQuota",
        "Hard limits for a namespace",
        "resourcequota.yaml",
        """\
apiVersion: v1
kind: ResourceQuota
metadata:
  name: launchpad-quota
  namespace: lp-app
spec:
  hard:
    requests.cpu: "2"
    requests.memory: 4Gi
    limits.cpu: "4"
    limits.memory: 8Gi
    pods: "20"
""",
    ),
    _k8s(
        "limitrange",
        "LimitRange",
        "Default and max container resource bounds",
        "limitrange.yaml",
        """\
apiVersion: v1
kind: LimitRange
metadata:
  name: launchpad-defaults
  namespace: lp-app
spec:
  limits:
    - type: Container
      default:
        cpu: 250m
        memory: 256Mi
      defaultRequest:
        cpu: 100m
        memory: 128Mi
""",
    ),
    _k8s(
        "role",
        "Role",
        "Namespaced RBAC permissions",
        "role.yaml",
        """\
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: app-reader
  namespace: lp-app
rules:
  - apiGroups: [""]
    resources: ["pods", "services", "configmaps"]
    verbs: ["get", "list", "watch"]
""",
    ),
    _k8s(
        "rolebinding",
        "RoleBinding",
        "Bind a Role to a ServiceAccount",
        "rolebinding.yaml",
        """\
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: app-reader
  namespace: lp-app
subjects:
  - kind: ServiceAccount
    name: app
    namespace: lp-app
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: app-reader
""",
    ),
)

_TF_TEMPLATES: tuple[WorkspaceTemplate, ...] = (
    _tf(
        "variables",
        "variables.tf",
        "Input variable declarations",
        "variables.tf",
        """\
variable "environment_name" {
  type        = string
  description = "Launchpad environment name"
}

variable "region" {
  type        = string
  description = "Primary cloud region"
}
""",
    ),
    _tf(
        "outputs",
        "outputs.tf",
        "Stack outputs",
        "outputs.tf",
        """\
output "environment_name" {
  value       = var.environment_name
  description = "Provisioned environment name"
}
""",
    ),
    _tf(
        "vpc_module",
        "VPC module stub",
        "Reusable VPC module skeleton",
        "modules/vpc/main.tf",
        """\
resource "null_resource" "vpc_placeholder" {
  triggers = {
    name = var.name
  }
}
""",
    ),
    _tf(
        "locals",
        "locals.tf",
        "Shared locals and tags",
        "locals.tf",
        """\
locals {
  common_tags = {
    ManagedBy   = "launchpad"
    Environment = var.environment_name
  }
}
""",
    ),
    _tf(
        "tfvars",
        "terraform.tfvars",
        "Variable assignments for local applies",
        "terraform.tfvars",
        """\
environment_name = "demo"
region           = "us-central1"
""",
    ),
)

_ALL_TEMPLATES: dict[str, WorkspaceTemplate] = {
    t.id: t for t in (*_K8S_TEMPLATES, *_TF_TEMPLATES)
}


def list_templates(*, category: str | None = None) -> list[WorkspaceTemplate]:
    items = list(_ALL_TEMPLATES.values())
    if category:
        items = [t for t in items if t.category == category]
    return items


def get_template(template_id: str) -> WorkspaceTemplate:
    try:
        return _ALL_TEMPLATES[template_id]
    except KeyError as exc:
        raise KeyError(f"Unknown template: {template_id}") from exc

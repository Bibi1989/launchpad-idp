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


def _cicd(
    kind_id: str,
    label: str,
    description: str,
    default_path: str,
    content: str,
) -> WorkspaceTemplate:
    return WorkspaceTemplate(
        id=f"cicd.{kind_id}",
        label=label,
        category="cicd",
        description=description,
        default_path=default_path,
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
      image: app:latest
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
          image: app:latest
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
          image: app:latest
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


_CICD_TEMPLATES: tuple[WorkspaceTemplate, ...] = (
    _cicd(
        "github_actions_k8s",
        "GitHub Actions (Kubernetes Deploy)",
        "Shift-Left CI/CD pipeline blocking vulnerabilities from the registry, deploying to Kubernetes.",
        ".github/workflows/deploy.yml",
        """\
name: Deploy to Kubernetes

on:
  push:
    branches:
      - main
  pull_request:
    branches:
      - main

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: my-org/my-app

jobs:
  sast:
    name: Static Application Security Testing (SAST)
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Run Semgrep
        run: |
          docker run --rm -v "${{ github.workspace }}:/src" -w /src returntocorp/semgrep:1.97.0 \\
            semgrep scan --config "p/ci" --config "p/security-audit" --error .

  build-scan-push:
    name: Local Build, Scan, & Push
    needs: sast
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log into Registry
        if: github.event_name == 'push' && github.ref == 'refs/heads/main'
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Local Container Build
        uses: docker/build-push-action@v5
        with:
          context: .
          load: true 
          tags: |
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }}
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Pre-Push Security Scan with Trivy
        run: |
          docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \\
            aquasec/trivy image \\
            --severity CRITICAL,HIGH \\
            --exit-code 1 \\
            --no-progress \\
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }}

      - name: Push Container Image to Registry
        if: github.event_name == 'push' && github.ref == 'refs/heads/main'
        run: |
          docker push ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }}
          docker push ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest

  deploy:
    name: Kubernetes Deployment
    needs: build-scan-push
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    container:
      image: bitnami/kubectl:1.30
    steps:
      - name: Configure Kubeconfig
        run: |
          export KUBECONFIG=/tmp/kubeconfig
          echo "${{ secrets.KUBECONFIG }}" > $KUBECONFIG
          chmod 600 $KUBECONFIG
          echo "KUBECONFIG=$KUBECONFIG" >> $GITHUB_ENV
          
      - name: Update Deployment Image
        run: |
          kubectl set image deployment/app app=${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }} -n lp-app
          
      - name: Rollout Health Check and Automated Rollback
        run: |
          if ! kubectl rollout status deployment/app -n lp-app --timeout=120s; then
            echo "Rollout failed or timed out! Initiating automated rollback..."
            kubectl rollout undo deployment/app -n lp-app
            exit 1
          fi
""",
    ),
    _cicd(
        "gitlab_ci_k8s",
        "GitLab CI (Kubernetes Deploy)",
        "Shift-Left CI/CD pipeline blocking vulnerabilities from the registry, deploying to Kubernetes.",
        ".gitlab-ci.yml",
        """\
stages:
  - sast
  - build-scan-push
  - deploy

variables:
  IMAGE_TAG_SHA: $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  IMAGE_TAG_LATEST: $CI_REGISTRY_IMAGE:latest

sast:
  stage: sast
  image: returntocorp/semgrep:1.97.0
  script:
    - semgrep scan --config "p/ci" --config "p/security-audit" --error .
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == "main"

build_scan_push:
  stage: build-scan-push
  image: docker:24.0.5
  services:
    - docker:24.0.5-dind
  before_script:
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
  script:
    - docker pull $IMAGE_TAG_LATEST || true
    - >
      docker build
      --cache-from $IMAGE_TAG_LATEST
      -t $IMAGE_TAG_SHA
      -t $IMAGE_TAG_LATEST
      .
    - |
      docker run --rm \\
        -v /var/run/docker.sock:/var/run/docker.sock \\
        aquasec/trivy image \\
        --severity CRITICAL,HIGH \\
        --exit-code 1 \\
        --no-progress \\
        $IMAGE_TAG_SHA
    - |
      if [ "$CI_COMMIT_BRANCH" == "main" ]; then
        echo "Trivy scan passed. Pushing verified image to registry..."
        docker push $IMAGE_TAG_SHA
        docker push $IMAGE_TAG_LATEST
      else
        echo "Merge Request pipeline: skipping registry push."
      fi
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == "main"

deploy:
  stage: deploy
  image: bitnami/kubectl:1.30
  before_script:
    - export KUBECONFIG=/tmp/kubeconfig
    - echo "$KUBE_CONFIG" > $KUBECONFIG
    - chmod 600 $KUBECONFIG
  script:
    - kubectl set image deployment/app app=$IMAGE_TAG_SHA -n lp-app
    - |
      if ! kubectl rollout status deployment/app -n lp-app --timeout=120s; then
        echo "Rollout failed or timed out! Initiating automated rollback..."
        kubectl rollout undo deployment/app -n lp-app
        exit 1
      fi
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
""",
    ),
)

_ALL_TEMPLATES: dict[str, WorkspaceTemplate] = {
    t.id: t for t in (*_K8S_TEMPLATES, *_TF_TEMPLATES, *_CICD_TEMPLATES)
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

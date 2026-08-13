from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CloudProvider(str, Enum):
    LOCAL = "local"
    GCP = "gcp"
    AWS = "aws"
    AZURE = "azure"
    CLOUDFLARE = "cloudflare"


class IaCEngine(str, Enum):
    TERRAFORM = "terraform"
    OPENTOFU = "opentofu"
    PULUMI = "pulumi"
    ANSIBLE = "ansible"


class SecretBackend(str, Enum):
    SECRET_MANAGER = "secret_manager"
    NATIVE_K8S = "native_k8s"


class NetworkTopology(str, Enum):
    """VPC/subnet layout when networking resources are enabled.

    - simple: one subnet (fast demos / ephemeral stacks)
    - standard: public + private with NAT/egress for production-ish golden paths
    """

    SIMPLE = "simple"
    STANDARD = "standard"


class SqlDatabaseEngine(str, Enum):
    """Managed relational engine for Cloud SQL / RDS."""

    POSTGRES = "postgres"
    MYSQL = "mysql"
    MARIADB = "mariadb"


class CacheEngine(str, Enum):
    """Managed cache engine for Memorystore / ElastiCache."""

    REDIS = "redis"
    MEMCACHED = "memcached"


class CosmosApiKind(str, Enum):
    """Azure Cosmos DB API surface."""

    MONGODB = "mongodb"
    SQL = "sql"


class LambdaRuntime(str, Enum):
    """AWS Lambda runtime for scaffolded functions."""

    NODEJS20 = "nodejs20.x"
    PYTHON312 = "python3.12"
    GO1X = "provided.al2023"


class KubernetesPackaging(str, Enum):
    """Workload packaging layout written under ``infra/`` when a cluster is selected."""

    NONE = "none"
    RAW_MANIFESTS = "raw_manifests"
    HELM = "helm"
    KUSTOMIZE = "kustomize"


class WorkspaceArtifactsMode(str, Enum):
    """Which artifact families Launchpad writes into a workspace."""

    IAC_ONLY = "iac_only"
    MANIFEST_ONLY = "manifest_only"
    BOTH = "both"


class WorkspaceRuntimeMode(str, Enum):
    """How the workspace runs at preview time (independent of cloud provider)."""

    KUBERNETES = "kubernetes"
    DOCKER_COMPOSE = "docker_compose"
    RUNNING_INSTANCE = "running_instance"


class RunningInstanceKind(str, Enum):
    """Compute target for ``running_instance`` mode (preview runs on this host/runtime)."""

    SERVERLESS = "serverless"
    """GCP Cloud Run or Azure Container Apps."""

    VM = "vm"
    """EC2, VPS, or any SSH-reachable Linux host."""

    LOCAL_MACHINE = "local_machine"
    """Operator machine via local Docker (no Kubernetes)."""


class InstanceProcessStrategy(str, Enum):
    """How the app process is supervised on a VM / local host.

    Serverless targets always use container images (``docker``).
    """

    DOCKER = "docker"
    """Build/run OCI image (default; matches attach deploy today)."""

    SYSTEMD = "systemd"
    """Native process under a systemd unit (best for production VMs)."""

    PM2 = "pm2"
    """Node.js process manager (Node stacks only)."""


class InstanceCodeSource(str, Enum):
    """How application source reaches a cloud VM (ignored for serverless / docker)."""

    SSH = "ssh"
    """Copy workspace files from the control plane over SSH (rsync/scp)."""

    GITHUB = "github"
    """Clone/pull from the environment git repository on the VM."""


class InstanceReverseProxy(str, Enum):
    """Optional TLS/HTTP edge in front of the app listen port."""

    NONE = "none"
    NGINX = "nginx"
    CADDY = "caddy"


class AnsibleAppDeployMode(str, Enum):
    """How the Ansible app role starts the workload on the target host."""

    DOCKER_RUN = "docker_run"
    DOCKER_COMPOSE = "docker_compose"
    SYSTEMD = "systemd"
    PM2 = "pm2"
    NONE = "none"


class AnsibleConfig(BaseModel):
    """Interactive Ansible scaffold options for VM / Compose host provisioning."""

    enabled: bool = False
    # Inventory / connection
    hosts: str = Field(
        default="127.0.0.1",
        max_length=2048,
        description="Comma or newline separated hosts / IPs for inventory",
    )
    inventory_group: str = Field(default="app_servers", max_length=64)
    ssh_user: str = Field(default="ubuntu", max_length=64)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_private_key_path: str | None = Field(default="~/.ssh/id_ed25519", max_length=512)
    become: bool = True
    become_user: str = Field(default="root", max_length=64)
    python_interpreter: str = Field(default="auto", max_length=128)
    # Host bootstrap
    set_hostname: bool = True
    hostname: str | None = Field(default=None, max_length=253)
    timezone: str = Field(default="UTC", max_length=64)
    packages: list[str] = Field(
        default_factory=lambda: ["curl", "ca-certificates", "gnupg", "jq", "htop"],
    )
    # Docker
    install_docker: bool = True
    install_compose_plugin: bool = True
    # Firewall / hardening
    enable_ufw: bool = True
    ufw_allow_ports: list[int] = Field(default_factory=lambda: [22, 80, 443])
    enable_fail2ban: bool = True
    enable_unattended_upgrades: bool = True
    # Deploy user
    create_deploy_user: bool = True
    deploy_user: str = Field(default="deploy", max_length=64)
    deploy_user_groups: list[str] = Field(default_factory=lambda: ["docker"])
    # App
    app_deploy_mode: AnsibleAppDeployMode = AnsibleAppDeployMode.DOCKER_RUN
    app_dir: str = Field(default="/opt/launchpad/app", max_length=512)
    app_listen_port: int = Field(default=8080, ge=1, le=65535)
    reverse_proxy: InstanceReverseProxy = InstanceReverseProxy.NONE
    app_start_command: str | None = Field(default=None, max_length=512)
    sync_workspace: bool = True
    # Vault
    use_vault: bool = False
    vault_password_file: str | None = Field(default=None, max_length=512)

    @field_validator("hosts", "inventory_group", "ssh_user", "become_user", "deploy_user", "timezone", "app_dir")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return value.strip()

    @field_validator("hostname", "ssh_private_key_path", "vault_password_file", "python_interpreter", "app_start_command")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("packages", "deploy_user_groups")
    @classmethod
    def clean_str_lists(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            cleaned = str(item).strip()
            if cleaned and cleaned not in out:
                out.append(cleaned)
        return out

    @field_validator("ufw_allow_ports")
    @classmethod
    def clean_ports(cls, value: list[int]) -> list[int]:
        ports = sorted({int(p) for p in value if 1 <= int(p) <= 65535})
        return ports or [22]


class RunningInstanceConfig(BaseModel):
    """Where a running-instance preview should deploy.

    Preview URL is an *output* of deploy (optional override only). SSH private key
    material must not live here; use ``ssh_key_path`` on the control plane host.
    """

    kind: RunningInstanceKind = RunningInstanceKind.LOCAL_MACHINE
    # Serverless (Cloud Run / Container Apps)
    service_name: str | None = Field(default=None, max_length=63)
    region: str | None = Field(default=None, max_length=64)
    # VM / VPS / EC2
    host: str | None = Field(default=None, max_length=255)
    ssh_user: str | None = Field(default="ubuntu", max_length=64)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_key_path: str | None = Field(default=None, max_length=512)
    # Local machine
    listen_port: int = Field(default=8080, ge=1, le=65535)
    # How the app is supervised on VM / local (ignored for serverless → forced docker).
    process_strategy: InstanceProcessStrategy = InstanceProcessStrategy.DOCKER
    # How source reaches a cloud VM when process_strategy is not docker.
    code_source: InstanceCodeSource = InstanceCodeSource.SSH
    # Optional HTTP edge in front of listen_port (VM / local only).
    reverse_proxy: InstanceReverseProxy = InstanceReverseProxy.NONE
    # Optional: force Open-app URL after deploy (advanced)
    preview_url_override: str | None = Field(default=None, max_length=512)
    # Legacy fields (coerced from older wizard snapshots)
    kube_context: str | None = Field(default=None, max_length=128)
    endpoint_url: str | None = Field(default=None, max_length=512)

    @field_validator("kind", mode="before")
    @classmethod
    def coerce_legacy_kind(cls, value: object) -> object:
        if value == "kube_context":
            return RunningInstanceKind.LOCAL_MACHINE
        if value == "endpoint":
            return RunningInstanceKind.VM
        return value

    @field_validator(
        "service_name",
        "region",
        "host",
        "ssh_user",
        "ssh_key_path",
        "preview_url_override",
        "kube_context",
        "endpoint_url",
    )
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def coerce_legacy_and_override(self) -> RunningInstanceConfig:
        if self.preview_url_override is None and self.endpoint_url:
            self.preview_url_override = self.endpoint_url
        # Serverless runtimes only accept OCI images; no PM2/systemd/nginx on the host.
        if self.kind == RunningInstanceKind.SERVERLESS:
            self.process_strategy = InstanceProcessStrategy.DOCKER
            self.reverse_proxy = InstanceReverseProxy.NONE
        return self


class IngressClassName(str, Enum):
    NGINX = "nginx"
    TRAEFIK = "traefik"
    GCE = "gce"
    ALB = "alb"
    AZURE_APPLICATION_GATEWAY = "azure-application-gateway"
    CONTOUR = "contour"


class SpotWorkloadPlacement(str, Enum):
    """Where spot/preemptible capacity may place pods."""

    STATELESS_NONPROD = "stateless_nonprod"
    PRODUCTION_ONDEMAND_FALLBACK = "production_ondemand_fallback"


class SpotProvisionerStrategy(str, Enum):
    KARPENTER = "karpenter"
    CLUSTER_AUTOSCALER = "cluster_autoscaler"


class ResourceSizingPreset(str, Enum):
    DEVELOPER = "developer"
    BALANCED = "balanced"
    PERFORMANCE = "performance"
    CUSTOM = "custom"


class IdleShutdownSchedule(str, Enum):
    """Scale deployment to 0 outside business hours."""

    WEEKNIGHTS_WEEKENDS = "weeknights_weekends"


class SpotSchedulingConfig(BaseModel):
    enabled: bool = False
    placement: SpotWorkloadPlacement = SpotWorkloadPlacement.STATELESS_NONPROD
    allocation_percent: int = Field(default=80, ge=0, le=100)
    provisioner: SpotProvisionerStrategy = SpotProvisionerStrategy.KARPENTER


class CostHpaConfig(BaseModel):
    enabled: bool = False
    min_replicas: int = Field(default=2, ge=1, le=100)
    max_replicas: int = Field(default=10, ge=1, le=200)
    target_cpu_utilization: int = Field(default=70, ge=1, le=100)

    @model_validator(mode="after")
    def max_gte_min(self) -> CostHpaConfig:
        if self.max_replicas < self.min_replicas:
            raise ValueError("max_replicas must be >= min_replicas")
        return self


class CostVpaConfig(BaseModel):
    """VPA recommendation-only mode (updateMode Off) - no automatic restarts."""

    enabled: bool = False


class CostResourceConfig(BaseModel):
    preset: ResourceSizingPreset = ResourceSizingPreset.DEVELOPER
    cpu_request: str = Field(default="100m", min_length=1, max_length=32)
    cpu_limit: str = Field(default="500m", min_length=1, max_length=32)
    memory_request: str = Field(default="256Mi", min_length=1, max_length=32)
    memory_limit: str = Field(default="768Mi", min_length=1, max_length=32)


class IdleShutdownConfig(BaseModel):
    enabled: bool = False
    schedule: IdleShutdownSchedule = IdleShutdownSchedule.WEEKNIGHTS_WEEKENDS


class DependencyPlacement(str, Enum):
    IN_CLUSTER = "in_cluster"
    MANAGED = "managed"
    EXTERNAL = "external"


class DataStoreDependency(BaseModel):
    enabled: bool = False
    placement: DependencyPlacement = DependencyPlacement.IN_CLUSTER
    # Used when placement=external (Neon, Railway, Upstash, self-hosted, etc.).
    connection_url: str | None = Field(default=None, max_length=2048)


class WorkloadDependenciesConfig(BaseModel):
    postgres: DataStoreDependency = Field(default_factory=DataStoreDependency)
    mysql: DataStoreDependency = Field(default_factory=DataStoreDependency)
    mariadb: DataStoreDependency = Field(default_factory=DataStoreDependency)
    mongodb: DataStoreDependency = Field(default_factory=DataStoreDependency)
    redis: DataStoreDependency = Field(default_factory=DataStoreDependency)

    def any_enabled(self) -> bool:
        return any(
            store.enabled
            for store in (
                self.postgres,
                self.mysql,
                self.mariadb,
                self.mongodb,
                self.redis,
            )
        )


class CostOptimizationConfig(BaseModel):
    """Cloud infrastructure & Kubernetes cost optimization suite.

    When enabled features are selected, Launchpad injects right-sizing,
    autoscaling, spot affinity, and idle-shutdown policies into ``infra/``.
    """

    spot_scheduling: SpotSchedulingConfig = Field(default_factory=SpotSchedulingConfig)
    hpa: CostHpaConfig = Field(default_factory=CostHpaConfig)
    vpa: CostVpaConfig = Field(default_factory=CostVpaConfig)
    resources: CostResourceConfig = Field(default_factory=CostResourceConfig)
    idle_shutdown: IdleShutdownConfig = Field(default_factory=IdleShutdownConfig)

    def any_enabled(self) -> bool:
        return (
            self.spot_scheduling.enabled
            or self.hpa.enabled
            or self.vpa.enabled
            or self.idle_shutdown.enabled
            or self.resources.preset != ResourceSizingPreset.DEVELOPER
            or self.resources.cpu_request != "100m"
            or self.resources.memory_request != "256Mi"
        )


class KubernetesWorkloadOptions(BaseModel):
    """Kubernetes objects scaffolded under ``infra/k8s`` or Helm templates.

    Core workloads (Deployment, Service, Pod, …) are selectable so users can
    choose exactly which YAML kinds to generate. Namespace is always written
    when packaging is enabled.
    """

    # Workloads / networking
    deployment: bool = True
    service: bool = True
    pod: bool = False
    job: bool = False
    cronjob: bool = False
    statefulset: bool = False
    daemonset: bool = False
    ingress: bool = False
    ingress_class: IngressClassName = IngressClassName.NGINX
    install_ingress_nginx: bool = False
    # Configuration & identity
    config_map: bool = False
    secret: bool = False
    service_account: bool = False
    # Storage & RBAC
    pvc: bool = False
    role: bool = False
    role_binding: bool = False
    # Autoscaling & policy
    hpa: bool = False
    vpa: bool = False
    pdb: bool = False
    network_policy: bool = False
    resource_quota: bool = False
    limit_range: bool = False

    @model_validator(mode="after")
    def ingress_nginx_requires_ingress(self) -> KubernetesWorkloadOptions:
        if self.install_ingress_nginx and not self.ingress:
            raise ValueError("install_ingress_nginx requires ingress to be enabled")
        if self.install_ingress_nginx and self.ingress_class != IngressClassName.NGINX:
            raise ValueError("install_ingress_nginx requires ingress_class=nginx")
        return self

    @model_validator(mode="after")
    def require_at_least_one_workload(self) -> KubernetesWorkloadOptions:
        workloads = (
            self.deployment,
            self.service,
            self.pod,
            self.job,
            self.cronjob,
            self.statefulset,
            self.daemonset,
            self.ingress,
            self.config_map,
            self.secret,
            self.service_account,
            self.pvc,
            self.role,
            self.role_binding,
            self.hpa,
            self.vpa,
            self.pdb,
            self.network_policy,
            self.resource_quota,
            self.limit_range,
        )
        if not any(workloads):
            raise ValueError("Select at least one Kubernetes object to scaffold")
        return self


# --- Local / kind ---


class LocalResources(BaseModel):
    """Dev cluster on the operator machine (kind). No cloud credentials required."""

    cluster_name: str = Field(default="launchpad", min_length=1, max_length=64)
    context: str = Field(default="kind-launchpad", min_length=1, max_length=128)


class LocalCloudConfig(BaseModel):
    provider: Literal[CloudProvider.LOCAL] = CloudProvider.LOCAL
    resources: LocalResources = Field(default_factory=LocalResources)


# --- GCP ---


class GcpResources(BaseModel):
    vpc: bool = True
    subnets: bool = True
    network_topology: NetworkTopology = NetworkTopology.SIMPLE
    gke: bool = False
    artifact_registry: bool = False
    secret_backend: SecretBackend = SecretBackend.SECRET_MANAGER
    cloud_run: bool = False
    cloud_functions: bool = False
    cloud_sql: bool = False
    cloud_sql_engine: SqlDatabaseEngine = SqlDatabaseEngine.POSTGRES
    cloud_storage: bool = False
    pubsub: bool = False
    memorystore: bool = False
    memorystore_engine: CacheEngine = CacheEngine.REDIS
    bigquery: bool = False
    region: str = Field(default="us-central1", min_length=2, max_length=64)
    machine_type: str = Field(default="e2-standard-4", min_length=3, max_length=64)
    project_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")

    @model_validator(mode="after")
    def validate_gcp_service_options(self) -> GcpResources:
        if self.cloud_sql and self.cloud_sql_engine == SqlDatabaseEngine.MARIADB:
            raise ValueError("Cloud SQL supports postgres or mysql (not mariadb)")
        return self


# --- AWS ---


class AwsResources(BaseModel):
    vpc: bool = True
    subnets: bool = True
    network_topology: NetworkTopology = NetworkTopology.SIMPLE
    ec2: bool = False
    s3: bool = False
    eks: bool = False
    secrets_manager: bool = True
    rds: bool = False
    rds_engine: SqlDatabaseEngine = SqlDatabaseEngine.POSTGRES
    ecr: bool = False
    elasticache: bool = False
    elasticache_engine: CacheEngine = CacheEngine.REDIS
    lambda_fn: bool = False
    lambda_runtime: LambdaRuntime = LambdaRuntime.NODEJS20
    dynamodb: bool = False
    sqs: bool = False
    alb: bool = False
    app_runner: bool = False
    """AWS App Runner: managed container service (Docker OCI images)."""
    region: str = Field(default="us-east-1", min_length=2, max_length=32)
    instance_type: str = Field(default="t3.medium", min_length=3, max_length=64)
    account_alias: str | None = Field(default=None, max_length=64)


# --- Azure ---


class AzureResources(BaseModel):
    vnet: bool = True
    subnets: bool = True
    network_topology: NetworkTopology = NetworkTopology.SIMPLE
    aks: bool = False
    key_vault: bool = True
    container_apps: bool = False
    acr: bool = False
    storage_account: bool = False
    cosmos_db: bool = False
    cosmos_api: CosmosApiKind = CosmosApiKind.MONGODB
    redis_cache: bool = False
    app_service: bool = False
    log_analytics: bool = False
    location: str = Field(default="eastus", min_length=2, max_length=64)
    vm_size: str = Field(default="Standard_D2_v2", min_length=3, max_length=64)
    resource_group: str = Field(min_length=3, max_length=90, pattern=r"^[-\w\._\(\)]+$")


# --- Cloudflare ---


class CloudflareResources(BaseModel):
    workers: bool = False
    r2: bool = False
    dns_records: bool = False
    pages: bool = False
    kv: bool = False
    d1: bool = False
    tunnels: bool = False
    queues: bool = False
    account_id: str = Field(min_length=8, max_length=64)
    zone_name: str | None = Field(default=None, max_length=253)

    @model_validator(mode="after")
    def require_zone_for_dns(self) -> CloudflareResources:
        if self.dns_records and not self.zone_name:
            raise ValueError("zone_name is required when dns_records is enabled")
        return self


class GcpCloudConfig(BaseModel):
    provider: Literal[CloudProvider.GCP] = CloudProvider.GCP
    resources: GcpResources


class AwsCloudConfig(BaseModel):
    provider: Literal[CloudProvider.AWS] = CloudProvider.AWS
    resources: AwsResources


class AzureCloudConfig(BaseModel):
    provider: Literal[CloudProvider.AZURE] = CloudProvider.AZURE
    resources: AzureResources


class CloudflareCloudConfig(BaseModel):
    provider: Literal[CloudProvider.CLOUDFLARE] = CloudProvider.CLOUDFLARE
    resources: CloudflareResources


CloudConfig = Annotated[
    LocalCloudConfig
    | GcpCloudConfig
    | AwsCloudConfig
    | AzureCloudConfig
    | CloudflareCloudConfig,
    Field(discriminator="provider"),
]


class CloudCredentials(BaseModel):
    """Ephemeral credentials injected into the sandbox - never logged in plaintext."""

    gcp_sa_key_json: str | None = None
    # Target GCP project for gcloud / compute (required for Connect OAuth; SA JSON embeds its own).
    gcp_project_id: str | None = None
    # Preferred deploy region (non-secret preference stored with vault).
    gcp_region: str | None = None
    # GCP Workload Identity Federation (Keyless OIDC)
    gcp_wif_project_number: str | None = None
    gcp_wif_pool_id: str | None = None
    gcp_wif_provider_id: str | None = None
    gcp_wif_target_sa_email: str | None = None
    # Interactive user OAuth (gcloud auth login style) - CloudTokenSet JSON
    gcp_oauth_token_json: str | None = None

    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    # Preferred AWS region for deploy wizards.
    aws_region: str | None = None
    # AWS IAM Roles with Web Identity (Keyless OIDC)
    aws_role_arn: str | None = None
    aws_role_session_name: str | None = None
    # Interactive AWS IAM Identity Center (SSO) - CloudTokenSet JSON
    aws_oauth_token_json: str | None = None
    aws_sso_account_id: str | None = None
    aws_sso_role_name: str | None = None

    azure_client_id: str | None = None
    azure_client_secret: str | None = None
    azure_tenant_id: str | None = None
    azure_subscription_id: str | None = None
    # Preferred Azure location for deploy wizards.
    azure_location: str | None = None
    # Interactive Entra ID user OAuth - CloudTokenSet JSON
    azure_oauth_token_json: str | None = None

    cloudflare_api_token: str | None = None

    @field_validator(
        "gcp_sa_key_json",
        "gcp_project_id",
        "gcp_region",
        "gcp_wif_project_number",
        "gcp_wif_pool_id",
        "gcp_wif_provider_id",
        "gcp_wif_target_sa_email",
        "gcp_oauth_token_json",
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
        "aws_region",
        "aws_role_arn",
        "aws_role_session_name",
        "aws_oauth_token_json",
        "aws_sso_account_id",
        "aws_sso_role_name",
        "azure_client_id",
        "azure_client_secret",
        "azure_tenant_id",
        "azure_subscription_id",
        "azure_location",
        "azure_oauth_token_json",
        "cloudflare_api_token",
        mode="before",
    )
    @classmethod
    def strip_optional(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value



class ServiceTypeName(str, Enum):
    CLUSTER_IP = "ClusterIP"
    NODE_PORT = "NodePort"
    LOAD_BALANCER = "LoadBalancer"


class ContainerServiceSpec(BaseModel):
    name: str = Field(default="app", min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    stack: str = Field(default="node", max_length=50)
    app_kind: str = Field(default="backend", description="Service tier type: 'frontend' or 'backend'")
    listen_port: int = Field(default=8080, ge=1, le=65535)
    dockerfile_path: str | None = None
    # Kubernetes Service shape for this workload. Each service in the list yields
    # its own Deployment + Service manifest so a workspace can host >1 workload.
    service_type: ServiceTypeName = ServiceTypeName.CLUSTER_IP
    selector: str | None = Field(
        default=None,
        max_length=63,
        pattern=r"^[a-z][a-z0-9-]*$",
        description="app label / selector; defaults to the service name",
    )
    # Which workload Launch Preview routes to. None = auto (frontend/web stacks
    # are exposed at "/", backends at "/api"). True/False overrides the default.
    expose_preview: bool | None = None


class ContainerScaffoldConfig(BaseModel):
    """Options for generating multi-stage Dockerfile and docker-compose.yml."""

    enabled: bool = False
    generate_dockerfile: bool = True
    generate_docker_compose: bool = True
    stack: str = Field(default="node", max_length=50)
    frameworks: list[str] = Field(default_factory=list)
    app_name: str = Field(default="app", max_length=100)
    listen_port: int = Field(default=8080, ge=1, le=65535)
    services: list[ContainerServiceSpec] = Field(default_factory=list)



class ProvisioningWizardRequest(BaseModel):
    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    iac_engine: IaCEngine = IaCEngine.TERRAFORM
    cloud: CloudConfig
    credentials: CloudCredentials = Field(default_factory=CloudCredentials)
    run_init: bool = True
    runtime_mode: WorkspaceRuntimeMode = WorkspaceRuntimeMode.KUBERNETES
    running_instance: RunningInstanceConfig = Field(default_factory=RunningInstanceConfig)
    artifact_mode: WorkspaceArtifactsMode = WorkspaceArtifactsMode.IAC_ONLY
    kubernetes_packaging: KubernetesPackaging = KubernetesPackaging.NONE
    kubernetes_options: KubernetesWorkloadOptions = Field(
        default_factory=KubernetesWorkloadOptions
    )
    cost_optimization: CostOptimizationConfig = Field(
        default_factory=CostOptimizationConfig
    )
    container_scaffold: ContainerScaffoldConfig = Field(
        default_factory=ContainerScaffoldConfig
    )
    dependencies: WorkloadDependenciesConfig = Field(
        default_factory=WorkloadDependenciesConfig
    )
    ansible: AnsibleConfig = Field(default_factory=AnsibleConfig)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def apply_runtime_mode_matrix(self) -> ProvisioningWizardRequest:
        from app.services.runtime_mode import (
            normalize_artifacts_for_runtime_mode,
            validate_runtime_mode,
        )

        validate_runtime_mode(self.cloud, self.runtime_mode, self.running_instance)
        (
            self.artifact_mode,
            self.kubernetes_packaging,
            self.container_scaffold,
            self.running_instance,
        ) = normalize_artifacts_for_runtime_mode(
            cloud=self.cloud,
            runtime_mode=self.runtime_mode,
            artifact_mode=self.artifact_mode,
            kubernetes_packaging=self.kubernetes_packaging,
            container_scaffold=self.container_scaffold,
            running_instance=self.running_instance,
        )
        return self

    @model_validator(mode="after")
    def sync_cost_autoscaling_flags(self) -> ProvisioningWizardRequest:
        """Enable HPA/VPA object scaffolding when cost suite toggles them on."""
        if self.runtime_mode != WorkspaceRuntimeMode.KUBERNETES:
            return self
        opts = self.kubernetes_options
        if self.cost_optimization.hpa.enabled and not opts.hpa:
            self.kubernetes_options = opts.model_copy(update={"hpa": True})
        opts = self.kubernetes_options
        if self.cost_optimization.vpa.enabled and not opts.vpa:
            self.kubernetes_options = opts.model_copy(update={"vpa": True})
        return self

    @model_validator(mode="after")
    def packaging_requires_cluster(self) -> ProvisioningWizardRequest:
        if self.runtime_mode != WorkspaceRuntimeMode.KUBERNETES:
            self.kubernetes_packaging = KubernetesPackaging.NONE
            return self

        # Local kind always targets an existing Kubernetes cluster.
        if isinstance(self.cloud, LocalCloudConfig):
            self.artifact_mode = WorkspaceArtifactsMode.MANIFEST_ONLY
            if self.kubernetes_packaging == KubernetesPackaging.NONE:
                self.kubernetes_packaging = KubernetesPackaging.RAW_MANIFESTS
            return self

        if self.artifact_mode == WorkspaceArtifactsMode.IAC_ONLY:
            self.kubernetes_packaging = KubernetesPackaging.NONE
            return self

        if self.kubernetes_packaging == KubernetesPackaging.NONE:
            raise ValueError(
                "kubernetes_packaging is required when artifact_mode includes manifests"
            )

        if not _cloud_has_kubernetes_runtime(self.cloud):
            raise ValueError(
                "kubernetes_packaging requires a Kubernetes or container runtime "
                "(GKE, EKS, AKS, Cloud Run, or Container Apps)"
            )
        return self

    @model_validator(mode="after")
    def validate_workload_dependencies(self) -> ProvisioningWizardRequest:
        from app.services.workload_dependencies import validate_managed_dependencies

        validate_managed_dependencies(self.cloud, self.dependencies)
        return self


def _cloud_has_kubernetes_runtime(cloud: CloudConfig) -> bool:
    if isinstance(cloud, LocalCloudConfig):
        return True
    if isinstance(cloud, GcpCloudConfig):
        return cloud.resources.gke or cloud.resources.cloud_run
    if isinstance(cloud, AwsCloudConfig):
        return cloud.resources.eks or cloud.resources.app_runner
    if isinstance(cloud, AzureCloudConfig):
        return cloud.resources.aks or cloud.resources.container_apps
    return False


class GeneratedManifest(BaseModel):
    path: str
    content: str


class WorkspaceFileNode(BaseModel):
    path: str
    type: Literal["file", "directory"]
    size: int | None = None


class WorkspaceFileContent(BaseModel):
    path: str
    content: str


class WorkspaceFileWriteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(default="", max_length=2_000_000)


class WorkspaceMkdirRequest(BaseModel):
    path: str = Field(min_length=1, max_length=512)


class WorkspaceRenameRequest(BaseModel):
    from_path: str = Field(min_length=1, max_length=512)
    to_path: str = Field(min_length=1, max_length=512)


class WorkspaceFormatRequest(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(default="", max_length=2_000_000)


class WorkspaceFormatResponse(BaseModel):
    path: str
    content: str


class WorkspaceTemplateInfo(BaseModel):
    id: str
    label: str
    category: str
    description: str
    default_path: str


class WorkspaceTemplateApplyRequest(BaseModel):
    template_id: str = Field(min_length=1, max_length=128)
    path: str | None = Field(default=None, max_length=512)
    overwrite: bool = False


class WorkspacePushRequest(BaseModel):
    installation_id: int = Field(ge=1)
    existing_full_name: str = Field(min_length=3, max_length=200)
    commit_message: str = Field(
        default="chore: update Launchpad workspace files",
        min_length=1,
        max_length=200,
    )
    include_workflow: bool = False
    include_dockerfiles: bool = False

    @field_validator("existing_full_name")
    @classmethod
    def validate_repo(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned.count("/") != 1:
            raise ValueError("existing_full_name must be owner/repo")
        owner, repo = cleaned.split("/", 1)
        if not owner or not repo:
            raise ValueError("existing_full_name must be owner/repo")
        return cleaned


class IaCBundleSummary(BaseModel):
    workspace_id: str
    engine: IaCEngine
    provider: CloudProvider
    root_dir: str
    files: list[str]
    artifact_mode: WorkspaceArtifactsMode | None = None
    runtime_mode: WorkspaceRuntimeMode | None = None
    name: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    starred: bool = False


class WorkspaceWizardConfig(BaseModel):
    """Persisted provision wizard selections (credentials never included)."""

    name: str
    iac_engine: IaCEngine
    cloud: CloudConfig
    run_init: bool = True
    runtime_mode: WorkspaceRuntimeMode = WorkspaceRuntimeMode.KUBERNETES
    running_instance: RunningInstanceConfig = Field(default_factory=RunningInstanceConfig)
    artifact_mode: WorkspaceArtifactsMode = WorkspaceArtifactsMode.IAC_ONLY
    kubernetes_packaging: KubernetesPackaging = KubernetesPackaging.NONE
    kubernetes_options: KubernetesWorkloadOptions = Field(
        default_factory=KubernetesWorkloadOptions
    )
    cost_optimization: CostOptimizationConfig = Field(
        default_factory=CostOptimizationConfig
    )
    container_scaffold: ContainerScaffoldConfig = Field(
        default_factory=ContainerScaffoldConfig
    )
    dependencies: WorkloadDependenciesConfig = Field(
        default_factory=WorkloadDependenciesConfig
    )
    ansible: AnsibleConfig = Field(default_factory=AnsibleConfig)
    has_credentials: bool = False
    """Safe display name for the stored cloud key (never the secret itself)."""
    credential_label: str | None = None


class WorkspacePromotionTarget(str, Enum):
    STAGING = "staging"
    PROD = "prod"


class WorkspacePromoteRequest(BaseModel):
    target_environment: WorkspacePromotionTarget
    promoted_name: str | None = Field(default=None, min_length=3, max_length=128)
    project_id: UUID | None = None
    run_init: bool | None = None

    @field_validator("promoted_name")
    @classmethod
    def normalize_promoted_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not cleaned:
            return None
        if not re.fullmatch(r"[a-z][a-z0-9-]*", cleaned):
            raise ValueError(
                "promoted_name must match ^[a-z][a-z0-9-]*$"
            )
        return cleaned


class CostEstimateLineItem(BaseModel):
    id: str
    label: str
    hourly_usd: float = Field(default=0.0, ge=0)
    monthly_usd: float = Field(default=0.0, ge=0)
    note: str | None = None


class ProvisioningCostEstimate(BaseModel):
    currency: str = "USD"
    provider: CloudProvider
    hourly_usd: float = Field(default=0.0, ge=0)
    monthly_usd: float = Field(default=0.0, ge=0)
    breakdown: list[CostEstimateLineItem] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class GcpApiEnablementResponse(BaseModel):
    """Result of enabling required Google APIs before provision."""

    project_id: str
    required: list[str] = Field(default_factory=list)
    already_enabled: list[str] = Field(default_factory=list)
    newly_enabled: list[str] = Field(default_factory=list)
    waited_seconds: float = 0.0
    message: str = ""


class WorkspaceListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    engine: str
    provider: str
    status: str
    artifact_mode: WorkspaceArtifactsMode = WorkspaceArtifactsMode.IAC_ONLY
    created_at: datetime
    root_dir: str
    starred: bool = False
    project_id: UUID | None = None


class WorkspaceStarRequest(BaseModel):
    starred: bool


class GitHubRepoRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    description: str = Field(default="", max_length=350)
    private: bool = True
    installation_id: int | None = Field(default=None, ge=1)
    organization: str | None = Field(default=None, max_length=100)
    workspace_id: str | None = None
    set_cloud_secrets: bool = True
    include_workflow: bool = True
    include_dockerfiles: bool = True
    existing_full_name: str | None = Field(
        default=None,
        max_length=200,
        description="owner/repo when importing an existing repository instead of creating one",
    )
    credentials: CloudCredentials = Field(default_factory=CloudCredentials)

    @field_validator("organization", "existing_full_name")
    @classmethod
    def normalize_optional_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("existing_full_name")
    @classmethod
    def validate_existing_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "/" not in value or value.count("/") != 1:
            raise ValueError("existing_full_name must be in owner/repo form")
        owner, repo = value.split("/", 1)
        if not owner or not repo:
            raise ValueError("existing_full_name must be in owner/repo form")
        return value


class GitHubRepoResult(BaseModel):
    full_name: str
    html_url: str
    private: bool
    default_branch: str
    secrets_set: list[str]
    workflow_path: str | None = None
    installation_id: int | None = None
    auth_method: str = "github_app"
    created: bool = False


class GitHubInstallationItem(BaseModel):
    id: int
    account_login: str
    account_type: str
    target_type: str | None = None
    repository_selection: str | None = None


class GitHubRepositoryItem(BaseModel):
    id: int
    name: str
    full_name: str
    private: bool
    html_url: str
    default_branch: str
    owner_login: str


class GitHubRepositorySearchItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    fullName: str = Field(..., alias="fullName")
    isPrivate: bool = Field(..., alias="isPrivate")
    owner: str
    defaultBranch: str = Field(..., alias="defaultBranch")
    htmlUrl: str = Field(..., alias="htmlUrl")


class GitHubRepositorySearchResponse(BaseModel):
    repositories: list[GitHubRepositorySearchItem] = Field(default_factory=list)


class GitHubAppStatusResponse(BaseModel):
    configured: bool
    app_id: int | None = None
    app_slug: str | None = None
    install_url: str | None = None
    default_installation_id: int | None = None
    message: str
    installations: list[GitHubInstallationItem] = Field(default_factory=list)


class GitlabStatusResponse(BaseModel):
    connected: bool
    oauth_configured: bool
    authorize_url: str | None = None
    base_url: str
    username: str | None = None
    token_type: str | None = None
    message: str


class GitlabPatConnectRequest(BaseModel):
    token: str = Field(min_length=8, max_length=512)
    base_url: str | None = Field(default=None, max_length=256)


class GitlabOAuthCallbackRequest(BaseModel):
    code: str = Field(min_length=4, max_length=256)
    state: str = Field(min_length=8, max_length=2048)


class GitlabProjectItem(BaseModel):
    id: int
    name: str
    path_with_namespace: str
    http_url_to_repo: str
    web_url: str
    visibility: str
    default_branch: str


class GitlabRepoRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    description: str = Field(default="", max_length=350)
    private: bool = True
    workspace_id: str | None = None
    existing_path: str | None = Field(
        default=None,
        max_length=200,
        description="group/project path when importing an existing project",
    )
    include_ci: bool = False

    @field_validator("existing_path")
    @classmethod
    def normalize_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().strip("/")
        return cleaned or None


class GitlabRepoResult(BaseModel):
    id: int
    path_with_namespace: str
    web_url: str
    http_url_to_repo: str
    default_branch: str
    visibility: str
    created: bool
    files_committed: int = 0


class GitlabPushRequest(BaseModel):
    project_path: str = Field(min_length=3, max_length=200)
    commit_message: str = Field(
        default="chore: update Launchpad workspace files",
        min_length=1,
        max_length=200,
    )

    @field_validator("project_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        cleaned = value.strip().strip("/")
        if cleaned.count("/") < 1:
            raise ValueError("project_path must be namespace/project")
        return cleaned


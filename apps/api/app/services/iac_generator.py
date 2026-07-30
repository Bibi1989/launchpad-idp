"""Generates ephemeral Terraform/Pulumi infrastructure-as-code bundles.

Given a validated `ProvisioningWizardRequest`, `IaCGenerator` renders a
self-contained IaC workspace on disk (Terraform HCL or Pulumi TypeScript)
scoped to the requested cloud provider and resource set. Every generated
resource is stamped with governance metadata (EnvironmentId, Owner,
CreatedBy, TTL_Expiration) so ephemeral environments remain auditable and
reapable by the TTL reaper worker.

Terraform bundles are written as a modular tree under ``infra/terraform/``
(see ``app.services.terraform_bundle``).
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.cloud import (
    AwsCloudConfig,
    AzureCloudConfig,
    CloudConfig,
    GcpCloudConfig,
    IaCBundleSummary,
    IaCEngine,
    KubernetesPackaging,
    LocalCloudConfig,
    ProvisioningWizardRequest,
    SecretBackend,
    WorkspaceArtifactsMode,
)
from app.services.k8s_bundle import write_kubernetes_layout
from app.services.terraform_bundle import write_terraform_bundle
from app.services.workspace_files import is_denied_workspace_path

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Pulumi: dependency manifest + program
# --------------------------------------------------------------------------- #


def _pulumi_deps(cloud: CloudConfig) -> dict[str, str]:
    deps: dict[str, str] = {"@pulumi/pulumi": "^3.136.0"}

    if isinstance(cloud, GcpCloudConfig):
        deps["@pulumi/gcp"] = "^8.9.0"
        if cloud.resources.secret_backend == SecretBackend.NATIVE_K8S:
            deps["@pulumi/kubernetes"] = "^4.18.0"
    elif isinstance(cloud, AwsCloudConfig):
        deps["@pulumi/aws"] = "^6.57.0"
    elif isinstance(cloud, AzureCloudConfig):
        deps["@pulumi/azure-native"] = "^2.75.0"
    else:
        deps["@pulumi/cloudflare"] = "^5.42.0"

    return deps


def _pulumi_index(name: str, cloud: CloudConfig) -> str:
    tags_obj = (
        "{ EnvironmentId: environmentName, Owner: 'launchpad', "
        "CreatedBy: 'launchpad-control-plane', TTL_Expiration: ttlExpiration }"
    )
    env_name_lit = json.dumps(name)

    if isinstance(cloud, GcpCloudConfig):
        r = cloud.resources
        lines = [
            "import * as pulumi from '@pulumi/pulumi';",
            "import * as gcp from '@pulumi/gcp';",
            "const config = new pulumi.Config();",
            f"const environmentName = config.get('environmentName') ?? {env_name_lit};",
            "const ttlExpiration = config.get('ttlExpiration') ?? 'unset';",
            f"const region = {json.dumps(r.region)};",
        ]
        if r.vpc:
            lines.append(
                "const vpc = new gcp.compute.Network('lp-vpc', {"
                " autoCreateSubnetworks: false,"
                " name: `lp-${environmentName}-vpc`,"
                f" labels: {tags_obj} }});"
            )
        if r.subnets and r.vpc:
            lines.append(
                "const subnet = new gcp.compute.Subnetwork('lp-subnet', {"
                " name: `lp-${environmentName}-subnet`,"
                " ipCidrRange: '10.10.0.0/20',"
                " region,"
                " network: vpc.id,"
                f" labels: {tags_obj} }});"
            )
        if r.artifact_registry:
            lines.append(
                "new gcp.artifactregistry.Repository('lp-ar', {"
                " location: region,"
                " repositoryId: `lp-${environmentName}`,"
                " format: 'DOCKER',"
                f" labels: {tags_obj} }});"
            )
        if r.gke:
            network_arg = "vpc.id" if r.vpc else "undefined"
            lines.append(
                "const cluster = new gcp.container.Cluster('lp-gke', {"
                " name: `lp-${environmentName}-gke`,"
                " location: region,"
                " removeDefaultNodePool: true,"
                " initialNodeCount: 1,"
                f" network: {network_arg},"
                f" resourceLabels: {tags_obj} }});"
            )
            lines.append(
                "new gcp.container.NodePool('lp-gke-primary', {"
                " name: `lp-${environmentName}-primary`,"
                " cluster: cluster.name,"
                " location: region,"
                " nodeCount: 2,"
                " nodeConfig: { machineType: 'e2-standard-4', "
                f"labels: {tags_obj} }} }});"
            )
        if r.secret_backend == SecretBackend.SECRET_MANAGER:
            lines.append(
                "new gcp.secretmanager.Secret('lp-secrets', {"
                " secretId: `lp-${environmentName}-secrets`,"
                " replication: { auto: {} },"
                f" labels: {tags_obj} }});"
            )
        else:
            lines.append(
                "import * as k8s from '@pulumi/kubernetes';"
            )
            lines.append(
                "new k8s.core.v1.Secret('lp-secrets', {"
                " metadata: { name: `lp-${environmentName}-secrets`, "
                f"labels: {tags_obj} }},"
                " type: 'Opaque' });"
            )
        if r.cloud_run:
            lines.append(
                "new gcp.cloudrunv2.Service('lp-run', {"
                " name: `lp-${environmentName}-run`,"
                " location: region,"
                " template: { containers: [{ image: "
                "'us-docker.pkg.dev/cloudrun/container/hello' }] },"
                f" labels: {tags_obj} }});"
            )
        if r.cloud_functions:
            lines.append(
                "new gcp.cloudfunctionsv2.Function('lp-fn', {"
                " name: `lp-${environmentName}-fn`,"
                " location: region,"
                " buildConfig: { runtime: 'nodejs20', entryPoint: 'handler', "
                "source: { storageSource: { bucket: "
                "`lp-${environmentName}-fn-source`, object: 'function-source.zip' } } },"
                " serviceConfig: { maxInstanceCount: 3, availableMemory: '256M' },"
                f" labels: {tags_obj} }});"
            )
        lines.append("export const environment = environmentName;")
        return "\n".join(lines) + "\n"

    if isinstance(cloud, AwsCloudConfig):
        r = cloud.resources
        lines = [
            "import * as pulumi from '@pulumi/pulumi';",
            "import * as aws from '@pulumi/aws';",
            "const config = new pulumi.Config();",
            f"const environmentName = config.get('environmentName') ?? {env_name_lit};",
            "const ttlExpiration = config.get('ttlExpiration') ?? 'unset';",
            f"const region = {json.dumps(r.region)};",
        ]
        if r.vpc:
            lines.append(
                "const vpc = new aws.ec2.Vpc('lp-vpc', {"
                " cidrBlock: '10.20.0.0/16',"
                " enableDnsHostnames: true,"
                " tags: { Name: `lp-${environmentName}-vpc`, "
                "EnvironmentId: environmentName, Owner: 'launchpad', "
                "CreatedBy: 'launchpad-control-plane', TTL_Expiration: ttlExpiration } });"
            )
        if r.subnets and r.vpc:
            lines.append(
                "const publicSubnet = new aws.ec2.Subnet('lp-subnet-public', {"
                " vpcId: vpc.id,"
                " cidrBlock: '10.20.1.0/24',"
                " mapPublicIpOnLaunch: true,"
                " tags: { Name: `lp-${environmentName}-public`, "
                "EnvironmentId: environmentName, Owner: 'launchpad', "
                "CreatedBy: 'launchpad-control-plane', TTL_Expiration: ttlExpiration } });"
            )
            lines.append(
                "const privateSubnet = new aws.ec2.Subnet('lp-subnet-private', {"
                " vpcId: vpc.id,"
                " cidrBlock: '10.20.2.0/24',"
                " tags: { Name: `lp-${environmentName}-private`, "
                "EnvironmentId: environmentName, Owner: 'launchpad', "
                "CreatedBy: 'launchpad-control-plane', TTL_Expiration: ttlExpiration } });"
            )
        if r.ec2:
            lines.append(
                "const ami = aws.ec2.getAmiOutput({"
                " mostRecent: true,"
                " owners: ['amazon'],"
                " filters: [{ name: 'name', values: ['al2023-ami-*-x86_64'] }] });"
            )
            lines.append(
                "new aws.ec2.Instance('lp-ec2', {"
                " ami: ami.id,"
                " instanceType: 't3.medium',"
                f" tags: {tags_obj} }});"
            )
        if r.s3:
            lines.append(
                "new aws.s3.Bucket('lp-data', {"
                " bucket: `lp-${environmentName}-data`,"
                f" tags: {tags_obj} }});"
            )
        if r.eks:
            lines.append(
                "const eksRole = new aws.iam.Role('lp-eks-role', {"
                " assumeRolePolicy: JSON.stringify({ Version: '2012-10-17', "
                "Statement: [{ Action: 'sts:AssumeRole', Effect: 'Allow', "
                "Principal: { Service: 'eks.amazonaws.com' } }] }),"
                f" tags: {tags_obj} }});"
            )
            lines.append(
                "new aws.eks.Cluster('lp-eks', {"
                " name: `lp-${environmentName}-eks`,"
                " roleArn: eksRole.arn,"
                f" tags: {tags_obj} }});"
            )
        if r.secrets_manager:
            lines.append(
                "new aws.secretsmanager.Secret('lp-secrets', {"
                " name: `lp-${environmentName}-secrets`,"
                f" tags: {tags_obj} }});"
            )
        lines.append("export const environment = environmentName;")
        return "\n".join(lines) + "\n"

    if isinstance(cloud, AzureCloudConfig):
        r = cloud.resources
        lines = [
            "import * as pulumi from '@pulumi/pulumi';",
            "import * as azure from '@pulumi/azure-native';",
            "const config = new pulumi.Config();",
            f"const environmentName = config.get('environmentName') ?? {env_name_lit};",
            "const ttlExpiration = config.get('ttlExpiration') ?? 'unset';",
            f"const location = {json.dumps(r.location)};",
            f"const resourceGroupName = {json.dumps(r.resource_group)};",
            (
                "const rg = new azure.resources.ResourceGroup('lp-rg', {"
                " resourceGroupName, location,"
                f" tags: {tags_obj} }});"
            ),
        ]
        if r.vnet:
            lines.append(
                "const vnet = new azure.network.VirtualNetwork('lp-vnet', {"
                " resourceGroupName: rg.name,"
                " location,"
                " addressSpace: { addressPrefixes: ['10.30.0.0/16'] },"
                f" tags: {tags_obj} }});"
            )
        if r.subnets and r.vnet:
            lines.append(
                "new azure.network.Subnet('lp-subnet', {"
                " resourceGroupName: rg.name,"
                " virtualNetworkName: vnet.name,"
                " addressPrefix: '10.30.1.0/24' });"
            )
        if r.aks:
            lines.append(
                "new azure.containerservice.ManagedCluster('lp-aks', {"
                " resourceGroupName: rg.name,"
                " location,"
                " dnsPrefix: `lp-${environmentName}`,"
                " agentPoolProfiles: [{ name: 'default', count: 2, "
                "vmSize: 'Standard_D2_v2', mode: 'System' }],"
                " identity: { type: 'SystemAssigned' },"
                f" tags: {tags_obj} }});"
            )
        if r.key_vault:
            lines.append(
                "const clientConfig = azure.authorization.getClientConfigOutput();"
            )
            lines.append(
                "new azure.keyvault.Vault('lp-kv', {"
                " resourceGroupName: rg.name,"
                " location,"
                " properties: { tenantId: clientConfig.tenantId, "
                "sku: { family: 'A', name: 'standard' }, accessPolicies: [] },"
                f" tags: {tags_obj} }});"
            )
        if r.container_apps:
            lines.append(
                "const cae = new azure.app.ManagedEnvironment('lp-cae', {"
                " resourceGroupName: rg.name,"
                " location,"
                f" tags: {tags_obj} }});"
            )
            lines.append(
                "new azure.app.ContainerApp('lp-app', {"
                " resourceGroupName: rg.name,"
                " managedEnvironmentId: cae.id,"
                " configuration: { activeRevisionsMode: 'Single' },"
                " template: { containers: [{ name: 'app', "
                "image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest' }] },"
                f" tags: {tags_obj} }});"
            )
        lines.append("export const environment = environmentName;")
        return "\n".join(lines) + "\n"

    r = cloud.resources
    lines = [
        "import * as pulumi from '@pulumi/pulumi';",
        "import * as cloudflare from '@pulumi/cloudflare';",
        "const config = new pulumi.Config();",
        f"const environmentName = config.get('environmentName') ?? {env_name_lit};",
        f"const accountId = {json.dumps(r.account_id)};",
    ]
    if r.r2:
        lines.append(
            "new cloudflare.R2Bucket('lp-r2', {"
            " accountId,"
            " name: `lp-${environmentName}-data` });"
        )
    if r.workers:
        lines.append(
            "new cloudflare.WorkersScript('lp-worker', {"
            " accountId,"
            " name: `lp-${environmentName}`,"
            " content: 'export default { async fetch() { return new Response(\"ok\"); } }' });"
        )
    if r.dns_records and r.zone_name:
        lines.append(f"const zoneName = {json.dumps(r.zone_name)};")
        lines.append(
            "const zone = cloudflare.getZoneOutput({ accountId, name: zoneName });"
        )
        lines.append(
            "new cloudflare.Record('lp-dns', {"
            " zoneId: zone.id,"
            " name: `lp-${environmentName}`,"
            " type: 'CNAME',"
            " value: `lp-${environmentName}.workers.dev`,"
            " proxied: true });"
        )
    lines.append("export const environment = environmentName;")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Generator
# --------------------------------------------------------------------------- #


class IaCGenerator:
    """Renders and manages ephemeral Terraform/Pulumi workspaces on disk."""

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        if workspace_root is not None:
            self._root = Path(workspace_root)
        else:
            self._root = Path(get_settings().iac_workspace_root)
        self._root.mkdir(parents=True, exist_ok=True)

    def generate(self, request: ProvisioningWizardRequest) -> IaCBundleSummary:
        """Renders a full IaC bundle for `request` into a new workspace directory."""
        workspace_id = str(uuid.uuid4())
        workspace_dir = self._allocate_workspace_dir(request.name)
        files = self._render_workspace(workspace_dir, request)

        self.write_wizard_snapshot(workspace_dir, request)

        logger.info(
            "iac_bundle_generated",
            workspace_id=workspace_id,
            workspace_name=request.name,
            root_dir=str(workspace_dir),
            engine=request.iac_engine.value,
            provider=request.cloud.provider.value,
            kubernetes_packaging=request.kubernetes_packaging.value,
            kubernetes_options=request.kubernetes_options.model_dump(),
            file_count=len(files),
        )

        return IaCBundleSummary(
            workspace_id=workspace_id,
            engine=request.iac_engine,
            provider=request.cloud.provider,
            root_dir=str(workspace_dir),
            files=sorted(files),
            name=request.name,
        )

    def regenerate(
        self,
        workspace_dir: Path,
        request: ProvisioningWizardRequest,
    ) -> list[str]:
        """Rewrite IaC under an existing workspace directory from an updated wizard request."""
        root = workspace_dir.resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"IaC workspace '{workspace_dir}' does not exist")

        # Clear prior generated layouts so deselected resources do not leave orphans.
        for relative in ("infra", "dockers"):
            target = root / relative
            if target.exists():
                shutil.rmtree(target)
        compose_file = root / "docker-compose.yml"
        if compose_file.is_file():
            compose_file.unlink()
        for pulumi_file in (
            "Pulumi.yaml",
            "Pulumi.dev.yaml",
            "package.json",
            "tsconfig.json",
            "index.ts",
            ".gitignore",
            "README.md",
        ):
            candidate = root / pulumi_file
            if candidate.is_file():
                candidate.unlink()

        files = self._render_workspace(root, request)
        self.write_wizard_snapshot(root, request)
        logger.info(
            "iac_bundle_regenerated",
            workspace_name=request.name,
            root_dir=str(root),
            engine=request.iac_engine.value,
            provider=request.cloud.provider.value,
            file_count=len(files),
        )
        return sorted(files)

    def _render_workspace(
        self,
        workspace_dir: Path,
        request: ProvisioningWizardRequest,
    ) -> list[str]:
        """Write provider-specific IaC (or kind K8s-only) into ``workspace_dir``."""
        if isinstance(request.cloud, LocalCloudConfig):
            return self._write_local_kind(workspace_dir, request)

        files: list[str] = []
        if request.artifact_mode in {
            WorkspaceArtifactsMode.IAC_ONLY,
            WorkspaceArtifactsMode.BOTH,
        }:
            if request.iac_engine in {IaCEngine.TERRAFORM, IaCEngine.OPENTOFU}:
                files.extend(self._write_terraform(workspace_dir, request))
            elif request.iac_engine == IaCEngine.PULUMI:
                files.extend(self._write_pulumi(workspace_dir, request))
            else:  # pragma: no cover
                raise ValueError(f"Unsupported IaC engine: {request.iac_engine!r}")

        if request.artifact_mode in {
            WorkspaceArtifactsMode.MANIFEST_ONLY,
            WorkspaceArtifactsMode.BOTH,
        } and request.kubernetes_packaging != KubernetesPackaging.NONE:
            files.extend(
                write_kubernetes_layout(
                    workspace_dir,
                    name=request.name,
                    packaging=request.kubernetes_packaging,
                    options=request.kubernetes_options,
                    cost_optimization=request.cost_optimization,
                    dependencies=request.dependencies,
                    cloud=request.cloud,
                )
            )
        if request.dependencies.any_enabled():
            from app.services.workload_dependencies import managed_connections_readme

            readme = managed_connections_readme(request.dependencies, request.cloud)
            if readme:
                path = workspace_dir / "infra" / "MANAGED_DATASTORES.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(readme, encoding="utf-8")
                files.append("infra/MANAGED_DATASTORES.md")
        if request.container_scaffold.enabled:
            files.extend(self._write_container_scaffold(workspace_dir, request))

        return files

    def _write_container_scaffold(
        self,
        workspace_dir: Path,
        request: ProvisioningWizardRequest,
    ) -> list[str]:
        from app.services.dockerfile_scaffold import (
            default_listen_port_for_stack,
            dockerfile_path_for_service,
            resolve_scaffold_stacks,
            scaffold_docker_compose_services,
            scaffold_dockerfile,
        )

        written: list[str] = []
        c_cfg = request.container_scaffold
        stacks = resolve_scaffold_stacks(stack=c_cfg.stack, frameworks=c_cfg.frameworks)
        multi = len(stacks) > 1 or bool(c_cfg.frameworks)
        app_name = c_cfg.app_name or request.name
        compose_services: list[dict[str, object]] = []

        for stack in stacks:
            port = (
                default_listen_port_for_stack(stack, c_cfg.listen_port or 8080)
                if multi
                else (c_cfg.listen_port or default_listen_port_for_stack(stack, 8080))
            )
            service_name = f"{app_name}-{stack.value}" if multi else app_name
            rel_dockerfile = dockerfile_path_for_service(
                app_name,
                stack if multi else None,
                multi=multi,
            )
            df_path = workspace_dir / rel_dockerfile

            if c_cfg.generate_dockerfile:
                df_content = scaffold_dockerfile(
                    stack,
                    app_name=service_name,
                    listen_port=port,
                )
                df_path.parent.mkdir(parents=True, exist_ok=True)
                df_path.write_text(df_content, encoding="utf-8")
                written.append(rel_dockerfile)

            compose_services.append(
                {
                    "name": service_name,
                    "listen_port": port,
                    "dockerfile_path": rel_dockerfile,
                }
            )

        if c_cfg.generate_docker_compose:
            from app.services.dockerfile_scaffold import scaffold_dependency_compose_blocks

            dc_content = scaffold_docker_compose_services(
                compose_services,
                dependency_blocks=scaffold_dependency_compose_blocks(request.dependencies),
            )
            dc_path = workspace_dir / "docker-compose.yml"
            dc_path.write_text(dc_content, encoding="utf-8")
            written.append("docker-compose.yml")

        return written

    def _write_local_kind(
        self,
        workspace_dir: Path,
        request: ProvisioningWizardRequest,
    ) -> list[str]:
        """Scaffold a kind-only workspace: K8s manifests/Helm + setup README (no cloud TF)."""
        assert isinstance(request.cloud, LocalCloudConfig)
        resources = request.cloud.resources
        packaging = request.kubernetes_packaging
        if packaging == KubernetesPackaging.NONE:
            packaging = KubernetesPackaging.RAW_MANIFESTS

        readme = f"""# {request.name} — Dev (kind)

Local Kubernetes workspace for verifying Launchpad before switching to a cloud provider.

## Prerequisites

Launchpad starts the kind cluster for you when you provision a Dev (kind) workspace
(``scripts/kind-up.sh``). Destroying the last Dev workspace tears it down
(``scripts/kind-down.sh``).

You still need Docker, kind, and kubectl installed, plus:

```
KUBERNETES_ENABLED=true
KUBERNETES_IN_CLUSTER=false
KUBERNETES_CONTEXT={resources.context}
```

in `apps/api/.env`, then restart the API and Celery worker once.

## Apply from the sandbox terminal

```bash
kubectl config use-context {resources.context}
kubectl apply -f infra/k8s/manifests/
# or: helm upgrade --install app-chart infra/helm/app-chart/
```

When everything looks healthy, reopen **Provision**, pick GCP/AWS/Azure/Cloudflare,
and regenerate this workspace (or create a new one) with real cloud credentials.
"""
        readme_path = workspace_dir / "README.md"
        readme_path.write_text(readme, encoding="utf-8")

        kind_note = workspace_dir / "infra" / "kind" / "README.md"
        kind_note.parent.mkdir(parents=True, exist_ok=True)
        kind_note.write_text(
            f"Cluster: `{resources.cluster_name}`\n"
            f"kubectl context: `{resources.context}`\n"
            "Created by Launchpad Advanced → Dev (kind).\n",
            encoding="utf-8",
        )

        files = [
            "README.md",
            "infra/kind/README.md",
            *write_kubernetes_layout(
                workspace_dir,
                name=request.name,
                packaging=packaging,
                options=request.kubernetes_options,
                cost_optimization=request.cost_optimization,
                dependencies=request.dependencies,
                cloud=request.cloud,
            ),
        ]
        if request.container_scaffold.enabled:
            files.extend(self._write_container_scaffold(workspace_dir, request))
        return files

    @staticmethod
    def wizard_snapshot_path(workspace_dir: Path) -> Path:
        return workspace_dir / ".launchpad" / "wizard.json"

    def write_wizard_snapshot(
        self,
        workspace_dir: Path,
        request: ProvisioningWizardRequest,
    ) -> None:
        """Persist non-secret wizard selections for later edit/prefill."""
        payload = {
            "name": request.name,
            "iac_engine": request.iac_engine.value,
            "cloud": request.cloud.model_dump(mode="json"),
            "run_init": request.run_init,
            "artifact_mode": request.artifact_mode.value,
            "kubernetes_packaging": request.kubernetes_packaging.value,
            "kubernetes_options": request.kubernetes_options.model_dump(mode="json"),
            "cost_optimization": request.cost_optimization.model_dump(mode="json"),
            "container_scaffold": request.container_scaffold.model_dump(mode="json"),
            "dependencies": request.dependencies.model_dump(mode="json"),
        }
        path = self.wizard_snapshot_path(workspace_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def read_wizard_snapshot(self, workspace_dir: Path) -> dict[str, object] | None:
        path = self.wizard_snapshot_path(workspace_dir)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("wizard_snapshot_unreadable", path=str(path))
            return None
        return raw if isinstance(raw, dict) else None

    def _allocate_workspace_dir(self, name: str) -> Path:
        """Create an on-disk directory named after the environment/workspace.

        Uses ``{name}`` when free; on collision appends a short unique suffix so
        the shell cwd stays human-readable (e.g. ``demo-env-a1b2c3d4``).
        """
        candidate = self._root / name
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate

        for _ in range(8):
            suffix = uuid.uuid4().hex[:8]
            candidate = self._root / f"{name}-{suffix}"
            if not candidate.exists():
                candidate.mkdir(parents=True, exist_ok=False)
                return candidate

        raise RuntimeError(f"Unable to allocate workspace directory for '{name}'")

    def get_workspace(self, workspace_ref: str) -> Path:
        """Resolves an on-disk workspace by absolute ``root_dir``, name, or legacy id."""
        as_path = Path(workspace_ref)
        if as_path.is_absolute() and as_path.is_dir():
            return as_path
        if as_path.is_dir():
            return as_path.resolve()

        workspace_dir = self._root / workspace_ref
        if workspace_dir.is_dir():
            return workspace_dir

        raise FileNotFoundError(f"IaC workspace '{workspace_ref}' does not exist")

    def destroy_workspace(self, workspace_ref: str) -> bool:
        """Removes a workspace directory from disk. Returns False if it never existed."""
        try:
            workspace_dir = self.get_workspace(workspace_ref)
        except FileNotFoundError:
            return False
        shutil.rmtree(workspace_dir, ignore_errors=True)
        logger.info("iac_workspace_destroyed", root_dir=str(workspace_dir))
        return True

    def read_bundle_files(self, workspace_ref: str) -> dict[str, str]:
        """Reads every text file in the workspace, keyed by path relative to its root.

        Hidden paths (e.g. `.launchpad/`, which may hold ephemeral sandbox
        credential material such as a mounted GCP service-account key) are
        deliberately excluded so bundle contents are always safe to commit
        or transmit externally.
        """
        workspace_dir = self.get_workspace(workspace_ref)
        files: dict[str, str] = {}
        for path in sorted(workspace_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(workspace_dir)
            if is_denied_workspace_path(relative):
                continue
            try:
                files[str(relative)] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                logger.warning(
                    "iac_bundle_file_skipped_binary",
                    workspace_ref=workspace_ref,
                    path=str(relative),
                )
                continue
        return files

    def _write_terraform(
        self, workspace_dir: Path, request: ProvisioningWizardRequest
    ) -> list[str]:
        return write_terraform_bundle(workspace_dir, request.name, request.cloud)

    def _write_pulumi(
        self, workspace_dir: Path, request: ProvisioningWizardRequest
    ) -> list[str]:
        cloud = request.cloud
        written: list[str] = []

        project_yaml = (
            "name: " + request.name + "\n"
            "runtime: nodejs\n"
            "description: Launchpad ephemeral environment for " + request.name + "\n"
        )
        (workspace_dir / "Pulumi.yaml").write_text(project_yaml, encoding="utf-8")
        written.append("Pulumi.yaml")

        stack_yaml = (
            "config:\n"
            "  environmentName: " + request.name + "\n"
            "  ttlExpiration: unset\n"
        )
        (workspace_dir / "Pulumi.dev.yaml").write_text(stack_yaml, encoding="utf-8")
        written.append("Pulumi.dev.yaml")

        package_json = json.dumps(
            {
                "name": request.name,
                "main": "index.ts",
                "devDependencies": {
                    "typescript": "^5.6.0",
                    "@types/node": "^22.7.0",
                },
                "dependencies": _pulumi_deps(cloud),
            },
            indent=2,
        )
        (workspace_dir / "package.json").write_text(package_json + "\n", encoding="utf-8")
        written.append("package.json")

        tsconfig_json = json.dumps(
            {
                "compilerOptions": {
                    "strict": True,
                    "outDir": "bin",
                    "target": "es2020",
                    "module": "commonjs",
                    "moduleResolution": "node",
                    "sourceMap": True,
                    "experimentalDecorators": True,
                    "pretty": True,
                    "noFallthroughCasesInSwitch": True,
                    "noImplicitReturns": True,
                    "forceConsistentCasingInFileNames": True,
                    "skipLibCheck": True,
                },
                "files": ["index.ts"],
            },
            indent=2,
        )
        (workspace_dir / "tsconfig.json").write_text(tsconfig_json + "\n", encoding="utf-8")
        written.append("tsconfig.json")

        (workspace_dir / "index.ts").write_text(
            _pulumi_index(request.name, cloud), encoding="utf-8"
        )
        written.append("index.ts")

        (workspace_dir / ".gitignore").write_text(
            "node_modules/\nbin/\n*.tsbuildinfo\n", encoding="utf-8"
        )
        written.append(".gitignore")

        return written

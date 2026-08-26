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
    CacheEngine,
    CloudConfig,
    GcpCloudConfig,
    IaCBundleSummary,
    IaCEngine,
    KubernetesPackaging,
    LocalCloudConfig,
    ProvisioningWizardRequest,
    SecretBackend,
    SqlDatabaseEngine,
    WorkspaceArtifactsMode,
    WorkspaceRuntimeMode,
)
from app.services.k8s_bundle import write_kubernetes_layout
from app.services.iac_state import restore_iac_runtime_state, stash_iac_runtime_state
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


def _pulumi_sanitize_helper() -> list[str]:
    """TypeScript helpers for DNS-1123 / RFC 1035 resource names."""
    return [
        "function sanitizeDns1123(value: string, maxLen = 63, prefix = ''): string {",
        "  let slug = value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')"
        ".replace(/-+/g, '-').replace(/^-|-$/g, '');",
        "  if (!slug) slug = 'env';",
        "  let candidate = prefix ? `${prefix.replace(/-$/, '')}-${slug}` "
        ": (slug[0].match(/[a-z]/) ? slug : `lp-${slug}`);",
        "  candidate = candidate.slice(0, maxLen).replace(/-$/, '');",
        "  if (!candidate || !/^[a-z]/.test(candidate)) {",
        "    candidate = `lp-${candidate}`.slice(0, maxLen).replace(/-$/, '');",
        "  }",
        "  return candidate || 'lp-env';",
        "}",
        "function envHash(value: string): string {",
        "  let h = 0;",
        "  for (let i = 0; i < value.length; i++) {",
        "    h = ((h << 5) - h + value.charCodeAt(i)) | 0;",
        "  }",
        "  return Math.abs(h).toString(16).padStart(8, '0').slice(0, 8);",
        "}",
        "function cidrOctet(value: string): number {",
        "  const h = envHash(value);",
        "  return 16 + (parseInt(h.slice(0, 2), 16) % 224);",
        "}",
    ]


def _pulumi_gcp_sql_version(engine: SqlDatabaseEngine) -> str:
    if engine == SqlDatabaseEngine.MYSQL:
        return "MYSQL_8_0"
    if engine == SqlDatabaseEngine.MARIADB:
        return "MYSQL_8_0"
    return "POSTGRES_15"


def _pulumi_index(name: str, cloud: CloudConfig) -> str:
    tags_obj = (
        "{ EnvironmentId: environmentName, Owner: 'launchpad', "
        "CreatedBy: 'launchpad-control-plane', TTL_Expiration: ttlExpiration }"
    )
    gcp_labels_obj = (
        "{ environment_id: environmentName, owner: 'launchpad', "
        "created_by: 'launchpad-control-plane', "
        "ttl_expiration: ttlExpiration.toLowerCase().replace(/[^a-z0-9_-]+/g, '-') }"
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
            f"const projectId = config.get('project_id') ?? {json.dumps(r.project_id)};",
            * _pulumi_sanitize_helper(),
            "const gkeName = sanitizeDns1123(environmentName, 40, 'gke');",
            "const nodePoolName = sanitizeDns1123(environmentName, 40, 'np');",
            "const name55 = sanitizeDns1123(environmentName, 55, 'lp');",
            "const name63 = sanitizeDns1123(environmentName, 63, 'lp');",
            "const bucketName = sanitizeDns1123("
            "`${environmentName}-${envHash(environmentName)}`, 63, 'lp');",
            "const octet = cidrOctet(environmentName);",
            # Provider project must match the SA / vault project or resources land wrong.
            "const gcpProvider = new gcp.Provider('lp-gcp', { project: projectId, region });",
            "const gcpOpts = { provider: gcpProvider };",
        ]
        if r.vpc:
            lines.append(
                "const vpc = new gcp.compute.Network('lp-vpc', {"
                " autoCreateSubnetworks: false,"
                " name: `${name55}-vpc` }, gcpOpts);"
            )
        if r.subnets and r.vpc:
            if r.network_topology.value == "standard":
                lines.append(
                    "const publicSubnet = new gcp.compute.Subnetwork('lp-subnet-public', {"
                    " name: `${name55}-public`,"
                    " ipCidrRange: `10.${octet}.16.0/20`,"
                    " region,"
                    " network: vpc.id }, gcpOpts);"
                )
                lines.append(
                    "const subnet = new gcp.compute.Subnetwork('lp-subnet-private', {"
                    " name: `${name55}-private`,"
                    " ipCidrRange: `10.${octet}.32.0/20`,"
                    " region,"
                    " network: vpc.id,"
                    " privateIpGoogleAccess: true }, gcpOpts);"
                )
                lines.append(
                    "const router = new gcp.compute.Router('lp-router', {"
                    " name: `${name55}-router`, region, network: vpc.id }, gcpOpts);"
                )
                lines.append(
                    "new gcp.compute.RouterNat('lp-nat', {"
                    " name: `${name55}-nat`, router: router.name, region,"
                    " natIpAllocateOption: 'AUTO_ONLY',"
                    " sourceSubnetworkIpRangesToNat: 'LIST_OF_SUBNETWORKS',"
                    " subnetworks: [{ name: subnet.id,"
                    " sourceIpRangesToNats: ['ALL_IP_RANGES'] }] }, gcpOpts);"
                )
            else:
                lines.append(
                    "const subnet = new gcp.compute.Subnetwork('lp-subnet', {"
                    " name: `${name55}-subnet`,"
                    " ipCidrRange: `10.${octet}.0.0/20`,"
                    " region,"
                    " network: vpc.id }, gcpOpts);"
                )
        if r.artifact_registry:
            lines.append(
                "new gcp.artifactregistry.Repository('lp-ar', {"
                " location: region,"
                " repositoryId: name63,"
                " format: 'DOCKER',"
                f" labels: {gcp_labels_obj} }}, gcpOpts);"
            )
        if r.gke:
            network_arg = "vpc.id" if r.vpc else "undefined"
            lines.append(
                "const cluster = new gcp.container.Cluster('lp-gke', {"
                " name: gkeName,"
                " location: region,"
                " removeDefaultNodePool: true,"
                " initialNodeCount: 1,"
                " deletionProtection: false,"
                f" network: {network_arg},"
                f" resourceLabels: {gcp_labels_obj} }}, gcpOpts);"
            )
            lines.append(
                "new gcp.container.NodePool('lp-gke-primary', {"
                " name: nodePoolName,"
                " cluster: cluster.name,"
                " location: region,"
                " nodeCount: 2,"
                f" nodeConfig: {{ machineType: '{r.machine_type}', "
                f"labels: {gcp_labels_obj} }} }},"
                " { ...gcpOpts, dependsOn: [cluster] });"
            )
        if r.secret_backend == SecretBackend.SECRET_MANAGER:
            lines.append(
                "new gcp.secretmanager.Secret('lp-secrets', {"
                " secretId: `${name55}-secrets`,"
                " replication: { auto: {} },"
                f" labels: {gcp_labels_obj} }}, gcpOpts);"
            )
        else:
            lines.append("import * as k8s from '@pulumi/kubernetes';")
            lines.append(
                "new k8s.core.v1.Secret('lp-secrets', {"
                " metadata: { name: `${name55}-secrets`, "
                f"labels: {tags_obj} }},"
                " type: 'Opaque' });"
            )
        if r.cloud_run:
            lines.append(
                "const cloudRun = new gcp.cloudrunv2.Service('lp-run', {"
                " name: `${name55}-run`,"
                " location: region,"
                " template: { containers: [{ image: "
                "'us-docker.pkg.dev/cloudrun/container/hello' }] },"
                f" labels: {gcp_labels_obj} }}, gcpOpts);"
            )
            lines.append("export const cloud_run_url = cloudRun.uri;")
            lines.append("export const preview_url = cloudRun.uri;")
        if r.cloud_functions:
            lines.append(
                "new gcp.cloudfunctionsv2.Function('lp-fn', {"
                " name: `${name55}-fn`,"
                " location: region,"
                " buildConfig: { runtime: 'nodejs20', entryPoint: 'handler', "
                "source: { storageSource: { bucket: "
                "`${bucketName}-fn`, object: 'function-source.zip' } } },"
                " serviceConfig: { maxInstanceCount: 3, availableMemory: '256M' },"
                f" labels: {gcp_labels_obj} }}, gcpOpts);"
            )
        if r.cloud_sql:
            db_version = _pulumi_gcp_sql_version(r.cloud_sql_engine)
            lines.append(
                "new gcp.sql.DatabaseInstance('lp-sql', {"
                " name: `${name55}-sql`,"
                " region,"
                f" databaseVersion: {json.dumps(db_version)},"
                " deletionProtection: false,"
                f" settings: {{ tier: 'db-f1-micro', userLabels: {gcp_labels_obj} }} "
                "}, gcpOpts);"
            )
        if r.cloud_storage:
            lines.append(
                "new gcp.storage.Bucket('lp-data', {"
                " name: bucketName,"
                " location: region,"
                " uniformBucketLevelAccess: true,"
                " forceDestroy: true,"
                f" labels: {gcp_labels_obj} }}, gcpOpts);"
            )
        if r.pubsub:
            lines.append(
                "new gcp.pubsub.Topic('lp-events', {"
                " name: `${name55}-events`,"
                f" labels: {gcp_labels_obj} }}, gcpOpts);"
            )
        if r.memorystore:
            if r.memorystore_engine == CacheEngine.MEMCACHED:
                lines.append(
                    "new gcp.memcache.Instance('lp-memcache', {"
                    " name: `${name55}-memcache`,"
                    " region,"
                    " nodeCount: 1,"
                    " nodeConfig: { cpuCount: 1, memorySizeMb: 1024 },"
                    f" labels: {gcp_labels_obj} }}, gcpOpts);"
                )
            else:
                lines.append(
                    "new gcp.redis.Instance('lp-redis', {"
                    " name: `${name55}-redis`,"
                    " region,"
                    " tier: 'BASIC',"
                    " memorySizeGb: 1,"
                    " redisVersion: 'REDIS_7_0',"
                    f" labels: {gcp_labels_obj} }}, gcpOpts);"
                )
        if r.bigquery:
            lines.append(
                "new gcp.bigquery.Dataset('lp-analytics', {"
                " datasetId: name63.replace(/-/g, '_'),"
                " location: region,"
                f" labels: {gcp_labels_obj} }}, gcpOpts);"
            )
        if getattr(r, "compute_instance", False):
            lines.append(
                "const appListenPort = config.getNumber('app_listen_port') ?? 8080;"
            )
            lines.append("const sshPublicKey = config.get('ssh_public_key') ?? '';")
            lines.append(f"const machineType = {json.dumps(r.machine_type)};")
            lines.append(
                "const ubuntuImage = gcp.compute.getImageOutput({"
                " family: 'ubuntu-2204-lts', project: 'ubuntu-os-cloud' },"
                " { provider: gcpProvider });"
            )
            if r.vpc:
                lines.append("const vmNetwork = vpc.id;")
                if r.subnets:
                    lines.append("const vmSubnetwork = subnet.id;")
                else:
                    lines.append(
                        "const vmSubnetwork: pulumi.Input<string> | undefined = undefined;"
                    )
            else:
                lines.append("const vmNetwork = 'default';")
                lines.append(
                    "const vmSubnetwork: pulumi.Input<string> | undefined = undefined;"
                )
            lines.append(
                "new gcp.compute.Firewall('lp-vm-fw', {"
                " name: `${name55}-vm-fw`,"
                " network: vmNetwork,"
                " allows: [{ protocol: 'tcp', ports: ['22', String(appListenPort), '80', '443'] }],"
                " sourceRanges: ['0.0.0.0/0'],"
                " targetTags: ['launchpad-vm'] }, gcpOpts);"
            )
            lines.append(
                "const startupScript = ["
                "  '#!/bin/bash',"
                "  'set -euo pipefail',"
                "  'export DEBIAN_FRONTEND=noninteractive',"
                "  'apt-get update -y',"
                "  'apt-get install -y curl ca-certificates gnupg git',"
                "  ...(sshPublicKey ? ["
                "    'install -d -m 700 -o ubuntu -g ubuntu /home/ubuntu/.ssh',"
                "    `grep -qxF \"${sshPublicKey}\" /home/ubuntu/.ssh/authorized_keys 2>/dev/null "
                "|| printf \"%s\\n\" \"${sshPublicKey}\" >> /home/ubuntu/.ssh/authorized_keys`,"
                "    'chown -R ubuntu:ubuntu /home/ubuntu/.ssh',"
                "    'chmod 600 /home/ubuntu/.ssh/authorized_keys',"
                "  ] : []),"
                "].join('\\n');"
            )
            lines.append(
                "const vm = new gcp.compute.Instance('lp-vm', {"
                " name: `${name55}-vm`,"
                " machineType,"
                " zone: `${region}-a`,"
                " tags: ['launchpad-vm'],"
                " bootDisk: { initializeParams: { image: ubuntuImage.selfLink, size: 20 } },"
                " networkInterfaces: [{"
                " network: vmNetwork,"
                " subnetwork: vmSubnetwork,"
                " accessConfigs: [{}] }],"
                " metadataStartupScript: startupScript,"
                " metadata: {"
                " 'enable-oslogin': 'FALSE',"
                " ...(sshPublicKey"
                " ? { 'ssh-keys': `ubuntu:${sshPublicKey}` }"
                " : {}) },"
                f" labels: {gcp_labels_obj} }}, gcpOpts);"
            )
            lines.append(
                "export const public_ip = vm.networkInterfaces.apply("
                " (nis) => (nis[0]?.accessConfigs && nis[0].accessConfigs[0]?.natIp) || '');"
            )
            lines.append(
                "export const preview_url = pulumi.interpolate"
                "`http://${public_ip}:${appListenPort}`;"
            )
            lines.append("export const compute_instance_id = vm.id;")
            lines.append("export const app_listen_port = String(appListenPort);")
        lines.append("export const environment = environmentName;")
        lines.append("export const gkeClusterName = gkeName;")
        lines.append("export const project_id = projectId;")
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
            * _pulumi_sanitize_helper(),
            "const name55 = sanitizeDns1123(environmentName, 55, 'lp');",
            "const name63 = sanitizeDns1123(environmentName, 63, 'lp');",
            "const bucketName = sanitizeDns1123("
            "`${environmentName}-${envHash(environmentName)}`, 63, 'lp');",
            "const octet = cidrOctet(environmentName);",
        ]
        if r.vpc:
            lines.append(
                "const vpc = new aws.ec2.Vpc('lp-vpc', {"
                " cidrBlock: `10.${octet}.0.0/16`,"
                " enableDnsHostnames: true,"
                " tags: { Name: `${name55}-vpc`, "
                "EnvironmentId: environmentName, Owner: 'launchpad', "
                "CreatedBy: 'launchpad-control-plane', TTL_Expiration: ttlExpiration } });"
            )
        if r.subnets and r.vpc:
            lines.append(
                "const publicSubnet = new aws.ec2.Subnet('lp-subnet-public', {"
                " vpcId: vpc.id,"
                " cidrBlock: `10.${octet}.1.0/24`,"
                " mapPublicIpOnLaunch: true,"
                " tags: { Name: `${name55}-public`, "
                "EnvironmentId: environmentName, Owner: 'launchpad', "
                "CreatedBy: 'launchpad-control-plane', TTL_Expiration: ttlExpiration } });"
            )
            lines.append(
                "const privateSubnet = new aws.ec2.Subnet('lp-subnet-private', {"
                " vpcId: vpc.id,"
                " cidrBlock: `10.${octet}.2.0/24`,"
                " tags: { Name: `${name55}-private`, "
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
                "const appListenPort = config.getNumber('app_listen_port') ?? 8080;"
            )
            lines.append(
                "const ec2 = new aws.ec2.Instance('lp-ec2', {"
                " ami: ami.id,"
                f" instanceType: '{r.instance_type}',"
                " associatePublicIpAddress: true,"
                f" tags: {tags_obj} }});"
            )
            lines.append("export const public_ip = ec2.publicIp;")
            lines.append(
                "export const preview_url = pulumi.interpolate"
                "`http://${ec2.publicIp}:${appListenPort}`;"
            )
            lines.append("export const ec2_instance_id = ec2.id;")
            lines.append("export const app_listen_port = String(appListenPort);")
        if r.s3:
            lines.append(
                "new aws.s3.Bucket('lp-data', {"
                " bucket: bucketName,"
                " forceDestroy: true,"
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
                " name: `${name55}-eks`,"
                " roleArn: eksRole.arn,"
                f" tags: {tags_obj} }},"
                " { dependsOn: [eksRole] });"
            )
        if r.app_runner:
            lines.append(
                "new aws.apprunner.Service('lp-runner', {"
                " serviceName: `${name55}-runner`,"
                " sourceConfiguration: {"
                " autoDeploymentsEnabled: false,"
                " imageRepository: {"
                " imageIdentifier: 'public.ecr.aws/aws-containers/hello-app-runner:latest',"
                " imageRepositoryType: 'ECR_PUBLIC',"
                " imageConfiguration: { port: '8080' } } },"
                " instanceConfiguration: { cpu: '256', memory: '512' },"
                f" tags: {tags_obj} }});"
            )
        if r.secrets_manager:
            lines.append(
                "new aws.secretsmanager.Secret('lp-secrets', {"
                " name: `${name55}-secrets`,"
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
            * _pulumi_sanitize_helper(),
            "const name63 = sanitizeDns1123(environmentName, 63, 'lp');",
            "const name40 = sanitizeDns1123(environmentName, 40, 'lp');",
            "const octet = cidrOctet(environmentName);",
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
                " addressSpace: { addressPrefixes: [`10.${octet}.0.0/16`] },"
                f" tags: {tags_obj} }});"
            )
        if r.subnets and r.vnet:
            lines.append(
                "new azure.network.Subnet('lp-subnet', {"
                " resourceGroupName: rg.name,"
                " virtualNetworkName: vnet.name,"
                " addressPrefix: `10.${octet}.1.0/24` });"
            )
        if r.aks:
            lines.append(
                "new azure.containerservice.ManagedCluster('lp-aks', {"
                " resourceGroupName: rg.name,"
                " location,"
                " dnsPrefix: name40,"
                " agentPoolProfiles: [{ name: 'default', count: 2, "
                f"vmSize: '{r.vm_size}', mode: 'System' }}],"
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


def _has_container_scaffold_intent(request: ProvisioningWizardRequest) -> bool:
    """True when the workspace actually wants Launchpad to scaffold an app tree.

    ``container_scaffold.enabled`` only means the source panel is shown (Link / Import /
    Services tabs). Linking a repo leaves the card enabled but clears services and the
    generate flags, so scaffolding must NOT run - otherwise a linked workspace gets a
    phantom ``apps/web-ui``. Scaffolding requires explicit services, a generate flag, or
    a multi-framework selection.
    """
    cfg = request.container_scaffold
    if not cfg.enabled:
        return False
    if cfg.services:
        return True
    if cfg.generate_dockerfile or cfg.generate_docker_compose:
        return True
    from app.services.dockerfile_scaffold import resolve_scaffold_stacks

    return len(resolve_scaffold_stacks(stack=cfg.stack, frameworks=cfg.frameworks)) > 1


def _is_multi_stack_workspace(request: ProvisioningWizardRequest) -> bool:
    """True when the workspace hosts >1 stack (explicit services or multi-framework).

    These produce per-stack ``launch-*`` manifests, so the generic single-app
    nginx Deployment/Service/Ingress must not be emitted.
    """
    cfg = request.container_scaffold
    if not cfg.enabled:
        return False
    if cfg.services:
        return True
    from app.services.dockerfile_scaffold import resolve_scaffold_stacks

    return len(resolve_scaffold_stacks(stack=cfg.stack, frameworks=cfg.frameworks)) > 1


def workspace_has_imported_app_artifacts(workspace_dir: Path) -> bool:
    """True when the tree already has a real imported app (not Launchpad's empty scaffold).

    Cloud promote copies the source workspace then calls ``regenerate``. Without this
    guard, regenerate deletes ``package.json``, wipes ``infra/k8s``, and replaces the
    imported website with the Express status-dashboard scaffold.
    """
    root = workspace_dir
    plan_path = root / ".launchpad" / "image-builds.json"
    if plan_path.is_file():
        try:
            raw = json.loads(plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = None
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                image = str(entry.get("image") or "").strip().lower()
                context = str(entry.get("context") or ".").strip() or "."
                dockerfile = str(entry.get("dockerfile") or "").strip().replace("\\", "/")
                if image.startswith("launch-"):
                    return True
                if context in {".", ""} and dockerfile in {"Dockerfile", "./Dockerfile"}:
                    return True
                if dockerfile and not dockerfile.startswith("apps/app/"):
                    return True
                if context not in {".", "", "apps/app"} and not context.startswith("apps/app"):
                    return True

    manifests = root / "infra" / "k8s" / "manifests"
    if manifests.is_dir():
        if any(path.name.startswith("launch-") for path in manifests.glob("*.y*ml")):
            return True

    root_df = root / "Dockerfile"
    if root_df.is_file() and any(
        (root / name).is_file()
        for name in (
            "package.json",
            "nuxt.config.ts",
            "nuxt.config.js",
            "nuxt.config.mjs",
            "next.config.js",
            "next.config.mjs",
            "next.config.ts",
            "vite.config.ts",
            "vite.config.js",
            "angular.json",
        )
    ):
        return True
    return False


def _manifest_options_for(request: ProvisioningWizardRequest) -> "KubernetesWorkloadOptions":
    """Kubernetes options to pass to the generic layout writer.

    When the workspace hosts explicit multi-stack services OR a multi-framework
    (fullstack) selection, the generic single-app Deployment/Service/Ingress
    (nginx fallback) must NOT be emitted - the per-stack ``launch-*`` manifests +
    multi-service Ingress replace them.
    """
    opts = request.kubernetes_options
    if _is_multi_stack_workspace(request):
        opts = opts.model_copy(
            update={"deployment": False, "service": False, "ingress": False}
        )
    return opts


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
        root.mkdir(parents=True, exist_ok=True)
        preserve_imported = workspace_has_imported_app_artifacts(root)

        # Clear prior generated layouts so deselected resources do not leave orphans.
        # Never wipe imported ``infra/k8s`` (repo-import / launch-* manifests).
        # Stash terraform/pulumi local state so retry provision updates in place
        # instead of create → 409 Conflict on existing cloud resources.
        dockers = root / "dockers"
        if dockers.exists():
            shutil.rmtree(dockers)
        infra = root / "infra"
        state_stash: Path | None = None
        if infra.exists():
            state_stash = stash_iac_runtime_state(infra)
            if preserve_imported:
                for child in list(infra.iterdir()):
                    if child.name == "k8s":
                        continue
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
            else:
                shutil.rmtree(infra)
        compose_file = root / "docker-compose.yml"
        if compose_file.is_file() and not preserve_imported:
            compose_file.unlink()

        # Pulumi project files live at workspace root. Only remove them when the
        # engine is Pulumi - never delete an imported Node app's package.json.
        if request.iac_engine == IaCEngine.PULUMI and not preserve_imported:
            for pulumi_file in (
                "Pulumi.yaml",
                "Pulumi.dev.yaml",
                "package.json",
                "tsconfig.json",
                "index.ts",
            ):
                candidate = root / pulumi_file
                if candidate.is_file():
                    candidate.unlink()
        elif request.iac_engine == IaCEngine.PULUMI:
            for pulumi_file in ("Pulumi.yaml", "Pulumi.dev.yaml", "index.ts"):
                candidate = root / pulumi_file
                if candidate.is_file():
                    candidate.unlink()

        effective = request
        if preserve_imported and request.container_scaffold.enabled:
            effective = request.model_copy(
                update={
                    "container_scaffold": request.container_scaffold.model_copy(
                        update={
                            "enabled": False,
                            "generate_dockerfile": False,
                        }
                    )
                }
            )

        files = self._render_workspace(
            root,
            effective,
            skip_kubernetes_layout=preserve_imported,
            skip_container_scaffold=preserve_imported,
        )
        restore_iac_runtime_state(root / "infra", state_stash)
        self.write_wizard_snapshot(root, request)
        logger.info(
            "iac_bundle_regenerated",
            workspace_name=request.name,
            root_dir=str(root),
            engine=request.iac_engine.value,
            provider=request.cloud.provider.value,
            preserve_imported=preserve_imported,
            file_count=len(files),
            state_restored=state_stash is not None,
        )
        return sorted(files)

    def _render_workspace(
        self,
        workspace_dir: Path,
        request: ProvisioningWizardRequest,
        *,
        skip_kubernetes_layout: bool = False,
        skip_container_scaffold: bool = False,
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
            elif request.iac_engine == IaCEngine.LAUNCHPAD:
                files.extend(self._write_launchpad_script(workspace_dir, request))
            elif request.iac_engine == IaCEngine.ANSIBLE:
                from app.services.local_runtime_iac import write_local_runtime_iac

                ansible_cfg = request.ansible
                if not ansible_cfg.enabled:
                    ansible_cfg = ansible_cfg.model_copy(update={"enabled": True})
                files.extend(
                    write_local_runtime_iac(
                        workspace_dir,
                        name=request.name,
                        engine=IaCEngine.ANSIBLE,
                        runtime_mode=request.runtime_mode,
                        ansible=ansible_cfg,
                        listen_port=request.running_instance.listen_port,
                    )
                )
            else:  # pragma: no cover
                raise ValueError(f"Unsupported IaC engine: {request.iac_engine!r}")

        # Ansible under infra/ansible whenever the checkbox/engine asks for it,
        # including manifest-only workspaces (not gated on Terraform/Pulumi mode).
        ansible_already = any(path.startswith("infra/ansible/") for path in files)
        ansible_wanted = (
            request.iac_engine == IaCEngine.ANSIBLE
            or request.ansible.enabled
            or request.config_tool.value == "ansible"
        )
        if ansible_wanted and not ansible_already:
            from app.services.local_runtime_iac import write_local_runtime_iac
            from app.services.scaffold_cloud_deploy import ansible_config_for_runtime

            ansible_cfg = ansible_config_for_runtime(
                source=request.ansible,
                runtime_mode=request.runtime_mode,
                running_instance=request.running_instance,
            )
            files.extend(
                write_local_runtime_iac(
                    workspace_dir,
                    name=request.name,
                    engine=IaCEngine.ANSIBLE,
                    runtime_mode=request.runtime_mode,
                    ansible=ansible_cfg,
                    listen_port=request.running_instance.listen_port,
                )
            )

        if (
            not skip_kubernetes_layout
            and request.artifact_mode
            in {
                WorkspaceArtifactsMode.MANIFEST_ONLY,
                WorkspaceArtifactsMode.BOTH,
            }
            and request.kubernetes_packaging != KubernetesPackaging.NONE
        ):
            from app.services.app_scaffold import workload_image_spec_for_request

            files.extend(
                write_kubernetes_layout(
                    workspace_dir,
                    name=request.name,
                    packaging=request.kubernetes_packaging,
                    options=_manifest_options_for(request),
                    cost_optimization=request.cost_optimization,
                    dependencies=request.dependencies,
                    cloud=request.cloud,
                    workload=workload_image_spec_for_request(request),
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
        if (
            _has_container_scaffold_intent(request)
            and not skip_container_scaffold
        ):
            files.extend(self._write_container_scaffold(workspace_dir, request))

        if (
            request.artifact_mode
            in {
                WorkspaceArtifactsMode.IAC_ONLY,
                WorkspaceArtifactsMode.BOTH,
            }
            and request.iac_engine
            in {IaCEngine.TERRAFORM, IaCEngine.OPENTOFU, IaCEngine.PULUMI, IaCEngine.LAUNCHPAD}
            and not isinstance(request.cloud, LocalCloudConfig)
        ):
            from app.services.cloud_deploy_makefile import write_cloud_deploy_makefile

            files.extend(
                write_cloud_deploy_makefile(workspace_dir, engine=request.iac_engine)
            )

        return files

    def _write_script(self, workspace_dir: Path, relative: str, content: str) -> None:
        """Write an executable helper script (chmod 0755)."""
        path = workspace_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        try:
            path.chmod(0o755)
        except OSError:  # pragma: no cover - non-POSIX filesystems
            pass

    @staticmethod
    def _write_image_builds_plan(
        workspace_dir: Path,
        compose_services: list[dict[str, object]],
    ) -> str | None:
        """Persist Dockerfile → image tag plan so provision builds the right tags.

        Overwrites stale import plans (e.g. only ``launch-app:latest`` from a root
        Dockerfile) when the wizard scaffolds real ``apps/<name>`` workloads.
        """
        import json

        plans: list[dict[str, str]] = []
        seen: set[str] = set()
        for svc in compose_services:
            name = str(svc.get("name") or "").strip()
            if not name or name in seen:
                continue
            context = str(svc.get("context") or f"apps/{name}").strip() or f"apps/{name}"
            df_hint = str(svc.get("dockerfile_path") or "Dockerfile").strip() or "Dockerfile"
            if df_hint == "Dockerfile" or not df_hint.startswith(context):
                df_rel = f"{context.rstrip('/')}/Dockerfile" if context not in {".", ""} else "Dockerfile"
            else:
                df_rel = df_hint
            df_path = workspace_dir / df_rel
            if not df_path.is_file():
                alt = workspace_dir / context / "Dockerfile"
                if alt.is_file():
                    try:
                        df_rel = str(alt.relative_to(workspace_dir)).replace("\\", "/")
                    except ValueError:
                        continue
                else:
                    continue
            seen.add(name)
            plans.append(
                {
                    "service": name,
                    "image": f"{name}:latest",
                    "context": context,
                    "dockerfile": df_rel,
                }
            )
        if not plans:
            return None
        out = workspace_dir / ".launchpad" / "image-builds.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plans, indent=2) + "\n", encoding="utf-8")
        return ".launchpad/image-builds.json"

    def _write_core_app_scaffold(
        self,
        workspace_dir: Path,
        request: ProvisioningWizardRequest,
        scaffold: "object",
    ) -> list[str]:
        """Write a runnable mini-application (source, Dockerfile, scripts, README)."""
        from app.schemas.cloud import LocalCloudConfig
        from app.services.dockerfile_scaffold import (
            scaffold_dependency_compose_blocks,
            scaffold_docker_compose_services,
        )
        from app.services.k8s_bundle import _namespace_name

        written: list[str] = []
        for relative, content in scaffold.files().items():  # type: ignore[attr-defined]
            path = workspace_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(relative)

        build_rel, build_content = scaffold.build_script()  # type: ignore[attr-defined]
        self._write_script(workspace_dir, build_rel, build_content)
        written.append(build_rel)

        if (
            isinstance(request.cloud, LocalCloudConfig)
            and request.runtime_mode == WorkspaceRuntimeMode.KUBERNETES
        ):
            cluster = request.cloud.resources.cluster_name
            namespace = _namespace_name(request.name)
            for rel, content in scaffold.kind_scripts(  # type: ignore[attr-defined]
                cluster_name=cluster, namespace=namespace
            ).items():
                self._write_script(workspace_dir, rel, content)
                written.append(rel)

        if request.container_scaffold.generate_docker_compose:
            port = scaffold.image_spec().container_port  # type: ignore[attr-defined]
            health_path = "/healthz" if scaffold.is_frontend else "/health"  # type: ignore[attr-defined]
            dc_content = scaffold_docker_compose_services(
                [
                    {
                        "name": scaffold.app_name,  # type: ignore[attr-defined]
                        "listen_port": port,
                        "dockerfile_path": "Dockerfile",
                        "context": scaffold.app_dir,  # type: ignore[attr-defined]
                        "health_path": health_path,
                    }
                ],
                dependency_blocks=scaffold_dependency_compose_blocks(request.dependencies),
            )
            (workspace_dir / "docker-compose.yml").write_text(dc_content, encoding="utf-8")
            written.append("docker-compose.yml")

        plan_rel = self._write_image_builds_plan(
            workspace_dir,
            [
                {
                    "name": scaffold.app_name,  # type: ignore[attr-defined]
                    "context": scaffold.app_dir,  # type: ignore[attr-defined]
                    "dockerfile_path": "Dockerfile",
                }
            ],
        )
        if plan_rel:
            written.append(plan_rel)

        return written

    @staticmethod
    def _sanitize_service_name(name: str) -> str:
        from app.services.app_scaffold import _sanitize_name

        return _sanitize_name(name)

    def _emit_launch_manifests(
        self,
        workspace_dir: Path,
        request: ProvisioningWizardRequest,
        workload_specs: list[dict[str, object]],
        written: list[str],
    ) -> None:
        """Write per-stack launch-* Deployment/Service manifests + routing Ingress,
        and prune the generic nginx deployment.yaml/service.yaml fallback."""
        from app.services.k8s_bundle import (
            additional_workload_manifests,
            build_multi_service_ingress,
            prune_orphan_default_manifests,
        )

        # Point each frontend's API_URL at the first backend Service so its SSR
        # dashboard can show the backend's live database/Redis connection status.
        backend = next((w for w in workload_specs if not w.get("_is_frontend")), None)
        if backend is not None:
            api_url = f"http://{backend['name']}-service:{backend['port']}"
            for w in workload_specs:
                if w.get("_is_frontend"):
                    extra = dict(w.get("extra_env") or {})
                    extra.update({
                        "API_URL": api_url,
                        "BACKEND_URL": api_url,
                        "NEXT_PUBLIC_API_URL": api_url,
                    })
                    w["extra_env"] = extra

        written.extend(
            additional_workload_manifests(
                workspace_dir,
                env_name=request.name,
                services=workload_specs,
                dependencies=request.dependencies,
            )
        )
        ingress_yaml = build_multi_service_ingress(
            env_name=request.name,
            services=workload_specs,
            ingress_class=request.kubernetes_options.ingress_class,
        )
        ingress_rel = "infra/k8s/manifests/ingress.yaml"
        (workspace_dir / ingress_rel).write_text(ingress_yaml, encoding="utf-8")
        if ingress_rel not in written:
            written.append(ingress_rel)
        removed = prune_orphan_default_manifests(workspace_dir)
        if removed:
            written[:] = [w for w in written if w not in removed]

    def _write_container_scaffold(
        self,
        workspace_dir: Path,
        request: ProvisioningWizardRequest,
    ) -> list[str]:
        from app.services.app_scaffold import resolve_core_scaffold
        from app.services.dockerfile_scaffold import (
            default_listen_port_for_stack,
            dockerfile_path_for_service,
            resolve_scaffold_stacks,
            scaffold_docker_compose_services,
            scaffold_dockerfile,
        )

        c_cfg = request.container_scaffold
        if (
            not c_cfg.services
            and not c_cfg.generate_dockerfile
            and not c_cfg.generate_docker_compose
        ):
            return []

        core = resolve_core_scaffold(request)
        if core is not None:
            return self._write_core_app_scaffold(workspace_dir, request, core)

        written: list[str] = []
        app_name = c_cfg.app_name or request.name
        compose_services: list[dict[str, object]] = []

        if c_cfg.services:
            from app.schemas.dockerfile_schema import ProjectStack
            from app.services.app_scaffold import (
                _SSR_FRONTEND_STACKS,
                _STATIC_FRONTEND_STACKS,
                CoreScaffold,
                _sanitize_name,
                is_core_stack,
                resolve_app_port,
            )
            from app.services.k8s_bundle import (
                additional_workload_manifests,
                build_multi_service_ingress,
                prune_orphan_default_manifests,
            )

            frontend_stacks = _STATIC_FRONTEND_STACKS | _SSR_FRONTEND_STACKS
            emit_manifests = request.kubernetes_packaging != KubernetesPackaging.NONE
            workload_specs: list[dict[str, object]] = []

            for s_spec in c_cfg.services:
                raw_stack = str(s_spec.stack or "node").lower()
                try:
                    stack = ProjectStack(raw_stack)
                except ValueError:
                    stack = ProjectStack.NODE
                app_slug = _sanitize_name(s_spec.name or f"{app_name}-{raw_stack}")
                # Kubernetes resource name is prefixed launch-<name> so preview
                # workloads are self-documenting and never clash with the generic
                # single-app "app" workload.
                wl_name = app_slug if app_slug.startswith("launch-") else f"launch-{app_slug}"
                selector = s_spec.selector or app_slug
                service_type = s_spec.service_type.value
                is_frontend = stack in frontend_stacks
                # Auto-expose the frontend/web stack; explicit flag always wins.
                expose_preview = (
                    s_spec.expose_preview if s_spec.expose_preview is not None else is_frontend
                )

                if is_core_stack(stack):
                    # Generate a real runnable app under apps/<slug>/ built as <slug>:latest.
                    port = resolve_app_port(stack, s_spec.listen_port)
                    scaffold = CoreScaffold(
                        stack=stack,
                        app_name=app_slug,
                        port=port,
                        dependencies=request.dependencies,
                    )
                    for rel, content in scaffold.files().items():
                        path = workspace_dir / rel
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(content, encoding="utf-8")
                        written.append(rel)
                    img_spec = scaffold.image_spec()
                    image = scaffold.image
                    port = img_spec.container_port
                    health_path = img_spec.liveness_path
                    run_as_user = img_spec.run_as_user
                    compose_services.append(
                        {
                            "name": app_slug,
                            "listen_port": port,
                            "dockerfile_path": "Dockerfile",
                            "context": scaffold.app_dir,
                            "health_path": health_path,
                            "app_kind": (
                                str(s_spec.app_kind or "").strip().lower()
                                or ("frontend" if is_frontend else "backend")
                            ),
                            "expose_preview": expose_preview,
                        }
                    )
                else:
                    # Non-core stack: legacy Dockerfile + pullable placeholder image.
                    port = s_spec.listen_port or default_listen_port_for_stack(stack, 8080)
                    rel_dockerfile = s_spec.dockerfile_path or f"dockers/{app_slug}/Dockerfile"
                    if c_cfg.generate_dockerfile:
                        df_path = workspace_dir / rel_dockerfile
                        df_path.parent.mkdir(parents=True, exist_ok=True)
                        df_path.write_text(
                            scaffold_dockerfile(stack, app_name=app_slug, listen_port=port),
                            encoding="utf-8",
                        )
                        written.append(rel_dockerfile)
                    image = "app:latest"
                    health_path = "/"
                    run_as_user = 1000
                    compose_services.append(
                        {
                            "name": app_slug,
                            "listen_port": port,
                            "dockerfile_path": rel_dockerfile,
                            "app_kind": (
                                str(s_spec.app_kind or "").strip().lower()
                                or ("frontend" if is_frontend else "backend")
                            ),
                            "expose_preview": expose_preview,
                        }
                    )

                workload_specs.append(
                    {
                        "name": wl_name,
                        "image": image,
                        "port": port,
                        "service_type": service_type,
                        "selector": selector,
                        "health_path": health_path,
                        "run_as_user": run_as_user,
                        "expose_preview": expose_preview,
                        "_is_frontend": is_frontend,
                    }
                )

            if emit_manifests and workload_specs:
                self._emit_launch_manifests(workspace_dir, request, workload_specs, written)
        else:
            stacks = resolve_scaffold_stacks(stack=c_cfg.stack, frameworks=c_cfg.frameworks)
            multi = len(stacks) > 1

            if multi:
                # Multi-framework (fullstack) workspace: scaffold a real app per
                # stack and emit per-stack launch-* manifests + Ingress instead of
                # the generic nginx Deployment (which was the "shows nginx" bug for
                # catalog fullstack templates).
                from app.services.app_scaffold import (
                    _SSR_FRONTEND_STACKS,
                    _STATIC_FRONTEND_STACKS,
                    CoreScaffold,
                    is_core_stack,
                    resolve_app_port,
                )

                frontend_stacks = _STATIC_FRONTEND_STACKS | _SSR_FRONTEND_STACKS
                emit_manifests = request.kubernetes_packaging != KubernetesPackaging.NONE
                workload_specs = []
                for stack in stacks:
                    # DNS-1123 safe slug (react_vite -> react-vite) for k8s names.
                    stack_slug = self._sanitize_service_name(stack.value)
                    app_slug = self._sanitize_service_name(f"{app_name}-{stack.value}")
                    wl_name = f"launch-{stack_slug}"
                    is_frontend = stack in frontend_stacks
                    if is_core_stack(stack):
                        port = resolve_app_port(stack, c_cfg.listen_port)
                        scaffold = CoreScaffold(
                            stack=stack,
                            app_name=app_slug,
                            port=port,
                            dependencies=request.dependencies,
                        )
                        for rel, content in scaffold.files().items():
                            path = workspace_dir / rel
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text(content, encoding="utf-8")
                            written.append(rel)
                        img_spec = scaffold.image_spec()
                        image = scaffold.image
                        port = img_spec.container_port
                        health_path = img_spec.liveness_path
                        run_as_user = img_spec.run_as_user
                        compose_services.append(
                            {"name": app_slug, "listen_port": port,
                             "dockerfile_path": "Dockerfile", "context": scaffold.app_dir,
                             "health_path": health_path}
                        )
                    else:
                        port = default_listen_port_for_stack(stack, 8080)
                        rel_dockerfile = f"dockers/{stack.value}/Dockerfile"
                        if c_cfg.generate_dockerfile:
                            df_path = workspace_dir / rel_dockerfile
                            df_path.parent.mkdir(parents=True, exist_ok=True)
                            df_path.write_text(
                                scaffold_dockerfile(stack, app_name=app_slug, listen_port=port),
                                encoding="utf-8",
                            )
                            written.append(rel_dockerfile)
                        image = "app:latest"
                        health_path = "/"
                        run_as_user = 1000
                        compose_services.append(
                            {"name": app_slug, "listen_port": port, "dockerfile_path": rel_dockerfile}
                        )
                    workload_specs.append(
                        {"name": wl_name, "image": image, "port": port,
                         "service_type": "ClusterIP", "selector": stack_slug,
                         "health_path": health_path, "run_as_user": run_as_user,
                         "expose_preview": is_frontend, "_is_frontend": is_frontend}
                    )
                if emit_manifests and workload_specs:
                    self._emit_launch_manifests(workspace_dir, request, workload_specs, written)
            else:
                stack = stacks[0]
                from app.services.app_scaffold import CoreScaffold, is_core_stack, resolve_app_port

                if is_core_stack(stack):
                    # Defense in depth: single core stacks should normally be handled by
                    # resolve_core_scaffold / _write_core_app_scaffold. If we reach here
                    # (e.g. unexpected frameworks), still emit a runnable app + context.
                    port = resolve_app_port(stack, c_cfg.listen_port)
                    scaffold = CoreScaffold(
                        stack=stack,
                        app_name=self._sanitize_service_name(app_name),
                        port=port,
                        dependencies=request.dependencies,
                    )
                    for rel, content in scaffold.files().items():
                        path = workspace_dir / rel
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(content, encoding="utf-8")
                        written.append(rel)
                    compose_services.append(
                        {
                            "name": scaffold.app_name,
                            "listen_port": scaffold.image_spec().container_port,
                            "dockerfile_path": "Dockerfile",
                            "context": scaffold.app_dir,
                            "health_path": scaffold.image_spec().liveness_path,
                        }
                    )
                else:
                    port = c_cfg.listen_port or default_listen_port_for_stack(stack, 8080)
                    service_name = app_name
                    rel_dockerfile = f"dockers/{app_name}/Dockerfile"
                    if c_cfg.generate_dockerfile:
                        df_path = workspace_dir / rel_dockerfile
                        df_path.parent.mkdir(parents=True, exist_ok=True)
                        df_path.write_text(
                            scaffold_dockerfile(stack, app_name=service_name, listen_port=port),
                            encoding="utf-8",
                        )
                        written.append(rel_dockerfile)
                    compose_services.append(
                        {"name": service_name, "listen_port": port, "dockerfile_path": rel_dockerfile}
                    )

        # Compose file only when the wizard opted in (docker_compose runtime sets this).
        # Running-instance keeps attach deploy; Ansible can still provision the VM.
        if c_cfg.generate_docker_compose and compose_services:
            from app.services.dockerfile_scaffold import (
                scaffold_dependency_compose_blocks,
                wire_compose_service_links,
            )

            linked = wire_compose_service_links(
                compose_services,
                dependencies=request.dependencies,
            )
            dc_content = scaffold_docker_compose_services(
                linked,
                dependency_blocks=scaffold_dependency_compose_blocks(request.dependencies),
            )
            dc_path = workspace_dir / "docker-compose.yml"
            dc_path.write_text(dc_content, encoding="utf-8")
            written.append("docker-compose.yml")

        if compose_services:
            plan_rel = self._write_image_builds_plan(workspace_dir, compose_services)
            if plan_rel:
                written.append(plan_rel)

        return written

    def _write_local_kind(
        self,
        workspace_dir: Path,
        request: ProvisioningWizardRequest,
    ) -> list[str]:
        """Scaffold a local workspace (Kubernetes, Compose, or running instance)."""
        assert isinstance(request.cloud, LocalCloudConfig)

        if request.runtime_mode != WorkspaceRuntimeMode.KUBERNETES:
            return self._write_local_non_kubernetes(workspace_dir, request)

        resources = request.cloud.resources
        packaging = request.kubernetes_packaging
        if packaging == KubernetesPackaging.NONE:
            packaging = KubernetesPackaging.RAW_MANIFESTS

        readme = f"""# {request.name} - Local Kubernetes

Local Kubernetes workspace for verifying Launchpad before switching to a cloud provider.

## Prerequisites

Launchpad can start a local cluster when needed (kind/k3d). Destroying the last
local Kubernetes workspace tears it down.

You still need Docker, a local cluster tool, and kubectl installed, plus:

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
            "Created by Launchpad Advanced → Local Kubernetes.\n",
            encoding="utf-8",
        )

        from app.services.app_scaffold import workload_image_spec_for_request

        files = [
            "README.md",
            "infra/kind/README.md",
            *write_kubernetes_layout(
                workspace_dir,
                name=request.name,
                packaging=packaging,
                options=_manifest_options_for(request),
                cost_optimization=request.cost_optimization,
                dependencies=request.dependencies,
                cloud=request.cloud,
                workload=workload_image_spec_for_request(request),
            ),
        ]
        if request.container_scaffold.enabled:
            files.extend(self._write_container_scaffold(workspace_dir, request))
        return files

    def _write_local_non_kubernetes(
        self,
        workspace_dir: Path,
        request: ProvisioningWizardRequest,
    ) -> list[str]:
        """Local Compose or running-instance workspace: containers only, no K8s manifests."""
        mode = request.runtime_mode
        kind = request.running_instance.kind.value
        if mode == WorkspaceRuntimeMode.DOCKER_COMPOSE:
            title = "Local Docker Compose"
            howto = (
                "## Run locally\n\n"
                "```bash\n"
                "docker compose up --build\n"
                "```\n\n"
                "Preview uses published Compose ports (no local Kubernetes cluster).\n"
            )
        else:
            title = f"Local running instance ({kind})"
            strategy = request.running_instance.process_strategy.value
            proxy = request.running_instance.reverse_proxy.value
            howto = (
                "## Run on compute target\n\n"
                f"Runtime mode: `{mode.value}` / kind: `{kind}`.\n"
                f"Process strategy: `{strategy}` / reverse proxy: `{proxy}`.\n\n"
                "Docker strategy: Launchpad attaches a container image to the "
                "selected compute target (local Docker, SSH VM, or managed container "
                "service). PM2/systemd: see `infra/instance/` and Ansible.\n"
                "No Kubernetes manifests are generated for this workspace.\n"
            )

        readme = f"""# {request.name} - {title}

{howto}
When you are ready for managed Kubernetes, reopen **Provision** and choose the
Kubernetes runtime with a cloud provider (or local Kubernetes).
"""
        readme_path = workspace_dir / "README.md"
        readme_path.write_text(readme, encoding="utf-8")
        files = ["README.md"]

        # Ensure container artifacts exist for Compose / instance deploy.
        if not request.container_scaffold.enabled:
            request = request.model_copy(
                update={
                    "container_scaffold": request.container_scaffold.model_copy(
                        update={
                            "enabled": True,
                            "generate_dockerfile": True,
                            "generate_docker_compose": mode
                            == WorkspaceRuntimeMode.DOCKER_COMPOSE,
                        }
                    )
                }
            )
        files.extend(self._write_container_scaffold(workspace_dir, request))

        if mode == WorkspaceRuntimeMode.RUNNING_INSTANCE:
            from app.services.instance_process_scaffold import write_instance_process_scaffold

            files.extend(
                write_instance_process_scaffold(
                    workspace_dir,
                    name=request.name,
                    running_instance=request.running_instance,
                )
            )

        # Optional IaC stubs (Terraform / OpenTofu / Pulumi / Ansible).
        from app.services.local_runtime_iac import write_local_runtime_iac

        ansible_cfg = request.ansible
        # Write Ansible artifacts when the engine or config tool is Ansible.
        if request.iac_engine == IaCEngine.ANSIBLE and not ansible_cfg.enabled:
            ansible_cfg = ansible_cfg.model_copy(update={"enabled": True})
        if request.config_tool.value == "ansible" and not ansible_cfg.enabled:
            ansible_cfg = ansible_cfg.model_copy(update={"enabled": True})
        if mode == WorkspaceRuntimeMode.RUNNING_INSTANCE:
            from app.schemas.cloud import AnsibleAppDeployMode
            from app.services.instance_process_scaffold import ansible_deploy_mode_for_strategy

            ansible_cfg = ansible_cfg.model_copy(
                update={
                    "app_deploy_mode": AnsibleAppDeployMode(
                        ansible_deploy_mode_for_strategy(
                            request.running_instance.process_strategy
                        )
                    ),
                    "app_listen_port": request.running_instance.listen_port,
                }
            )
        if (
            ansible_cfg.enabled
            and mode == WorkspaceRuntimeMode.RUNNING_INSTANCE
            and request.running_instance.kind.value == "vm"
        ):
            ansible_cfg = ansible_cfg.model_copy(
                update={
                    "enabled": True,
                    "hosts": request.running_instance.host
                    or ansible_cfg.hosts
                    or "127.0.0.1",
                    "ssh_user": request.running_instance.ssh_user
                    or ansible_cfg.ssh_user,
                    "ssh_port": request.running_instance.ssh_port,
                    "ssh_private_key_path": request.running_instance.ssh_key_path
                    or ansible_cfg.ssh_private_key_path,
                    "app_listen_port": request.running_instance.listen_port,
                }
            )

        # Always write infra/ansible when Ansible is enabled (independent of
        # artifact_mode so compose / attach workspaces still get the tree).
        if request.iac_engine == IaCEngine.ANSIBLE or ansible_cfg.enabled:
            files.extend(
                write_local_runtime_iac(
                    workspace_dir,
                    name=request.name,
                    engine=IaCEngine.ANSIBLE,
                    runtime_mode=mode,
                    ansible=ansible_cfg,
                    listen_port=request.running_instance.listen_port,
                )
            )

        if request.artifact_mode in {
            WorkspaceArtifactsMode.IAC_ONLY,
            WorkspaceArtifactsMode.BOTH,
        } and request.iac_engine != IaCEngine.ANSIBLE:
            files.extend(
                write_local_runtime_iac(
                    workspace_dir,
                    name=request.name,
                    engine=request.iac_engine,
                    runtime_mode=mode,
                )
            )
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
            "runtime_mode": request.runtime_mode.value,
            "running_instance": request.running_instance.model_dump(mode="json"),
            "artifact_mode": request.artifact_mode.value,
            "kubernetes_packaging": request.kubernetes_packaging.value,
            "kubernetes_options": request.kubernetes_options.model_dump(mode="json"),
            "cost_optimization": request.cost_optimization.model_dump(mode="json"),
            "container_scaffold": request.container_scaffold.model_dump(mode="json"),
            "dependencies": request.dependencies.model_dump(mode="json"),
            "ansible": request.ansible.model_dump(mode="json"),
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
        root = workspace_dir.resolve()
        files: dict[str, str] = {}
        for path in sorted(workspace_dir.rglob("*")):
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved != root and root not in resolved.parents:
                logger.warning(
                    "iac_bundle_file_skipped_escape",
                    workspace_ref=workspace_ref,
                    path=str(path),
                )
                continue
            relative = path.relative_to(workspace_dir)
            if is_denied_workspace_path(relative):
                continue
            try:
                files[str(relative).replace("\\", "/")] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                logger.warning(
                    "iac_bundle_file_skipped_binary",
                    workspace_ref=workspace_ref,
                    path=str(relative),
                )
                continue
        return files

    def _write_launchpad_script(
        self, workspace_dir: Path, request: ProvisioningWizardRequest
    ) -> list[str]:
        from app.services.launchpad_script import write_launchpad_script

        return write_launchpad_script(workspace_dir, request)

    def _write_terraform(
        self, workspace_dir: Path, request: ProvisioningWizardRequest
    ) -> list[str]:
        return write_terraform_bundle(workspace_dir, request.name, request.cloud)

    def _write_pulumi(
        self, workspace_dir: Path, request: ProvisioningWizardRequest
    ) -> list[str]:
        """Write Pulumi TypeScript program under ``infra/pulumi/`` (matches apply path)."""
        cloud = request.cloud
        written: list[str] = []
        pulumi_dir = workspace_dir / "infra" / "pulumi"
        pulumi_dir.mkdir(parents=True, exist_ok=True)

        def _write(rel: str, content: str) -> None:
            path = pulumi_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(f"infra/pulumi/{rel}")

        project_yaml = (
            "name: " + request.name + "\n"
            "runtime: nodejs\n"
            "description: Launchpad ephemeral environment for " + request.name + "\n"
        )
        _write("Pulumi.yaml", project_yaml)

        stack_yaml = (
            "config:\n"
            "  environmentName: " + request.name + "\n"
            "  ttlExpiration: unset\n"
        )
        _write("Pulumi.dev.yaml", stack_yaml)

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
        _write("package.json", package_json + "\n")

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
        _write("tsconfig.json", tsconfig_json + "\n")
        _write("index.ts", _pulumi_index(request.name, cloud))
        _write(".gitignore", "node_modules/\nbin/\n*.tsbuildinfo\n")

        return written

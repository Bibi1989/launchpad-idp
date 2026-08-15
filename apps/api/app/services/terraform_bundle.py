"""Modular Terraform bundle writer under ``infra/terraform/``.

Renders a root stack that instantiates child modules:

- ``modules/vpc`` - network definitions
- ``modules/cluster`` - GKE / EKS / AKS / Cloud Run / compute
- ``modules/secrets`` - cloud secret manager / Key Vault / native K8s secrets
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.schemas.cloud import (
    AwsCloudConfig,
    AwsResources,
    AzureCloudConfig,
    AzureResources,
    CacheEngine,
    CloudConfig,
    CloudflareResources,
    CosmosApiKind,
    GcpCloudConfig,
    GcpResources,
    NetworkTopology,
    SecretBackend,
    SqlDatabaseEngine,
)


def _gcp_cloud_sql_database_version(engine: SqlDatabaseEngine) -> str:
    if engine == SqlDatabaseEngine.MYSQL:
        return "MYSQL_8_0"
    if engine == SqlDatabaseEngine.MARIADB:
        # Cloud SQL has no MariaDB product; closest wire-compatible path is MySQL.
        return "MYSQL_8_0"
    return "POSTGRES_15"


def _aws_rds_engine(engine: SqlDatabaseEngine) -> str:
    return engine.value


def _azure_cosmos_kind(api: CosmosApiKind) -> str:
    if api == CosmosApiKind.MONGODB:
        return "MongoDB"
    return "GlobalDocumentDB"

TF_ROOT = Path("infra") / "terraform"

# DNS-1123 / RFC 1035: lowercase alnum + hyphens; must start with a letter.
_DNS1123_RE = re.compile(r"[^a-z0-9]+")
_MULTI_HYPHEN_RE = re.compile(r"-{2,}")


def sanitize_dns1123_name(
    value: str,
    *,
    max_len: int = 63,
    prefix: str = "",
) -> str:
    """Sanitize a string for GCP/AWS/K8s DNS-1123 / RFC 1035 resource names."""
    slug = _MULTI_HYPHEN_RE.sub("-", _DNS1123_RE.sub("-", value.strip().lower())).strip("-")
    if not slug:
        slug = "env"
    if prefix:
        candidate = f"{prefix.rstrip('-')}-{slug}"
    elif not slug[0].isalpha():
        candidate = f"lp-{slug}"
    else:
        candidate = slug
    candidate = candidate[:max_len].rstrip("-")
    if not candidate or not candidate[0].isalpha():
        candidate = f"lp-{candidate}"[:max_len].rstrip("-")
    return candidate or "lp-env"


def _naming_locals_hcl() -> str:
    """Shared locals: RFC 1035 names, unique buckets, per-env CIDRs."""
    return """\
locals {
  # Lowercase + non-alnum → hyphen; collapse consecutive hyphens; trim edges.
  _env_raw       = lower(var.environment_id)
  _env_hyphen    = replace(local._env_raw, "/[^a-z0-9]+/", "-")
  _env_collapsed = replace(local._env_hyphen, "/-+/", "-")
  _env_trimmed   = trimsuffix(trimprefix(local._env_collapsed, "-"), "-")
  env_slug       = local._env_trimmed == "" ? "env" : local._env_trimmed
  env_hash       = substr(md5(var.environment_id), 0, 8)

  # GKE cluster / node pool: must start with a letter, end alnum, max 40.
  gke_cluster_name   = trimsuffix(substr(format("gke-%s", local.env_slug), 0, 40), "-")
  gke_node_pool_name = trimsuffix(substr(format("np-%s", local.env_slug), 0, 40), "-")

  # Generic DNS-1123 resource names (leave headroom for common suffixes).
  name_40 = trimsuffix(substr(format("lp-%s", local.env_slug), 0, 40), "-")
  name_55 = trimsuffix(substr(format("lp-%s", local.env_slug), 0, 55), "-")
  name_63 = trimsuffix(substr(format("lp-%s", local.env_slug), 0, 63), "-")

  # Globally unique object-storage names (lowercase, no underscores).
  gcs_bucket_name = trimsuffix(substr(format("lp-%s-%s", local.env_slug, local.env_hash), 0, 63), "-")
  s3_bucket_name  = trimsuffix(substr(format("lp-%s-%s", local.env_slug, local.env_hash), 0, 63), "-")

  # Per-environment RFC1918 third octet (16-239) avoids CIDR collisions across parallel stacks.
  cidr_octet          = 16 + (parseint(substr(md5(var.environment_id), 0, 2), 16) % 224)
  gcp_subnet_cidr     = format("10.%d.0.0/20", local.cidr_octet)
  gcp_public_cidr     = format("10.%d.16.0/20", local.cidr_octet)
  gcp_private_cidr    = format("10.%d.32.0/20", local.cidr_octet)
  aws_vpc_cidr        = format("10.%d.0.0/16", local.cidr_octet)
  aws_public_cidr     = format("10.%d.1.0/24", local.cidr_octet)
  aws_private_cidr    = format("10.%d.2.0/24", local.cidr_octet)
  azure_vnet_cidr     = format("10.%d.0.0/16", local.cidr_octet)
  azure_subnet_cidr   = format("10.%d.1.0/24", local.cidr_octet)
  azure_public_cidr   = format("10.%d.2.0/24", local.cidr_octet)
  azure_private_cidr  = format("10.%d.3.0/24", local.cidr_octet)
}
"""

_GOVERNANCE_VARS = """\
variable "environment_id" {
  description = "Stable identifier for the ephemeral environment"
  type        = string
}

variable "owner" {
  description = "Governance owner tag"
  type        = string
  default     = "launchpad"
}

variable "created_by" {
  description = "Governance creator tag"
  type        = string
  default     = "launchpad-control-plane"
}

variable "ttl_expiration" {
  description = "RFC3339 timestamp after which this environment should be reaped"
  type        = string
  default     = "unset"
}
"""


def _governance_tags_hcl(
    key: str = "tags",
    indent: str = "  ",
    extra: dict[str, str] | None = None,
    *,
    gcp: bool = False,
) -> str:
    """Emit governance tags/labels.

    AWS/Azure tags keep PascalCase keys (EnvironmentId, …).
    GCP labels/resource_labels require lowercase keys and a restricted value charset.
    """
    inner = indent + "  "
    lines = [indent + key + " = {"]
    if extra:
        for extra_key, expression in extra.items():
            out_key = extra_key.lower().replace(" ", "_") if gcp else extra_key
            lines.append(inner + out_key + " = " + expression)
    if gcp:
        # GCP label keys: [a-z][a-z0-9_-]* ; values: [a-z0-9_-]* (no ':').
        lines.append(inner + "environment_id = var.environment_id")
        lines.append(inner + "owner          = var.owner")
        lines.append(inner + "created_by     = var.created_by")
        lines.append(
            inner
            + 'ttl_expiration = replace(lower(var.ttl_expiration), "/[^a-z0-9_-]+/", "-")'
        )
    else:
        lines.append(inner + "EnvironmentId  = var.environment_id")
        lines.append(inner + "Owner          = var.owner")
        lines.append(inner + "CreatedBy      = var.created_by")
        lines.append(inner + "TTL_Expiration = var.ttl_expiration")
    lines.append(indent + "}")
    return "\n".join(lines)


def _join_blocks(blocks: list[str], empty_comment: str) -> str:
    if not blocks:
        return empty_comment + "\n"
    return "\n\n".join(blocks) + "\n"


def _governance_variable_pass_through() -> str:
    return """\
  environment_id = var.environment_id
  owner          = var.owner
  created_by     = var.created_by
  ttl_expiration = var.ttl_expiration
"""


def _module_governance_variables() -> str:
    return _GOVERNANCE_VARS


# --------------------------------------------------------------------------- #
# providers.tf
# --------------------------------------------------------------------------- #


def _providers_tf(cloud: CloudConfig) -> str:
    if isinstance(cloud, GcpCloudConfig):
        providers = """\
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.32"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
"""
        if cloud.resources.secret_backend == SecretBackend.NATIVE_K8S:
            if cloud.resources.gke:
                # Target the GKE cluster - not local ~/.kube/config (avoids kind secret clashes).
                providers += """
data "google_client_config" "default" {}

provider "kubernetes" {
  host                   = "https://${module.cluster.gke_cluster_endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(module.cluster.gke_cluster_ca_certificate)
}
"""
            else:
                providers += """
provider "kubernetes" {
  config_path = "~/.kube/config"
}
"""
        return providers

    if isinstance(cloud, AwsCloudConfig):
        return """\
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "aws" {
  region = var.region
}
"""

    if isinstance(cloud, AzureCloudConfig):
        return """\
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.116"
    }
  }
}

provider "azurerm" {
  features {}
}
"""

    return """\
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.44"
    }
  }
}

provider "cloudflare" {
}
"""


# --------------------------------------------------------------------------- #
# GCP API enablement (google_project_service)
# --------------------------------------------------------------------------- #


def gcp_required_apis(r: GcpResources) -> list[str]:
    """APIs required for the selected GCP resource set (stable order, unique)."""
    apis: list[str] = [
        # Bootstrap: Terraform google_project_service needs Resource Manager + Service Usage.
        "cloudresourcemanager.googleapis.com",
        "serviceusage.googleapis.com",
        # IAM is required for GKE node service accounts and most project resources.
        "iam.googleapis.com",
    ]
    if r.vpc or r.subnets or r.gke or getattr(r, "compute_instance", False):
        apis.append("compute.googleapis.com")
    if r.gke:
        apis.append("container.googleapis.com")
    if r.artifact_registry:
        apis.append("artifactregistry.googleapis.com")
    if r.cloud_run:
        apis.append("run.googleapis.com")
    if r.cloud_functions:
        apis.extend(
            [
                "cloudfunctions.googleapis.com",
                "cloudbuild.googleapis.com",
                "artifactregistry.googleapis.com",
            ]
        )
    if r.cloud_sql:
        apis.append("sqladmin.googleapis.com")
    if r.cloud_storage:
        apis.append("storage.googleapis.com")
    if r.pubsub:
        apis.append("pubsub.googleapis.com")
    if r.memorystore:
        if r.memorystore_engine == CacheEngine.MEMCACHED:
            apis.append("memcache.googleapis.com")
        else:
            apis.append("redis.googleapis.com")
    if r.bigquery:
        apis.append("bigquery.googleapis.com")
    if r.secret_backend == SecretBackend.SECRET_MANAGER:
        apis.append("secretmanager.googleapis.com")
    # Preserve order while uniquing.
    seen: set[str] = set()
    ordered: list[str] = []
    for api in apis:
        if api not in seen:
            seen.add(api)
            ordered.append(api)
    return ordered


# Back-compat alias for internal call sites / tests.
_gcp_required_apis = gcp_required_apis


def _apis_tf(cloud: CloudConfig) -> str | None:
    """Root-level google_project_service resources for GCP; None for other clouds."""
    if not isinstance(cloud, GcpCloudConfig):
        return None
    apis = gcp_required_apis(cloud.resources)
    lines = [
        "# Enable required Google APIs before creating project resources.",
        "# disable_on_destroy=false keeps APIs enabled after terraform destroy.",
        'resource "google_project_service" "apis" {',
        "  for_each = toset([",
    ]
    for api in apis:
        lines.append(f'    "{api}",')
    lines += [
        "  ])",
        "",
        "  project            = var.project_id",
        "  service            = each.value",
        "  disable_on_destroy = false",
        "}",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Root variables / tfvars
# --------------------------------------------------------------------------- #


def _root_variables(cloud: CloudConfig) -> str:
    lines = [_GOVERNANCE_VARS.rstrip()]

    if isinstance(cloud, GcpCloudConfig):
        r = cloud.resources
        lines += [
            "",
            'variable "project_id" {',
            '  description = "GCP project ID"',
            "  type        = string",
            "  default     = " + json.dumps(r.project_id),
            "}",
            "",
            'variable "region" {',
            '  description = "GCP region"',
            "  type        = string",
            "  default     = " + json.dumps(r.region),
            "}",
            "",
            'variable "machine_type" {',
            '  description = "GCE machine type for compute_instance"',
            "  type        = string",
            "  default     = " + json.dumps(r.machine_type),
            "}",
            "",
            'variable "app_listen_port" {',
            '  description = "Application listen port (VM / Cloud Run)"',
            "  type        = number",
            "  default     = 8080",
            "}",
            "",
            'variable "app_image" {',
            '  description = "Container image for Cloud Run (optional)"',
            "  type        = string",
            '  default     = ""',
            "}",
            "",
            'variable "ssh_public_key" {',
            '  description = "SSH public key for VM bootstrap (optional)"',
            "  type        = string",
            '  default     = ""',
            "}",
        ]
    elif isinstance(cloud, AwsCloudConfig):
        r = cloud.resources
        lines += [
            "",
            'variable "region" {',
            '  description = "AWS region"',
            "  type        = string",
            "  default     = " + json.dumps(r.region),
            "}",
            "",
            'variable "app_listen_port" {',
            '  description = "Application listen port (EC2 / App Runner)"',
            "  type        = number",
            "  default     = 8080",
            "}",
            "",
            'variable "ssh_public_key" {',
            '  description = "SSH public key for EC2 (optional)"',
            "  type        = string",
            '  default     = ""',
            "}",
        ]
    elif isinstance(cloud, AzureCloudConfig):
        r = cloud.resources
        lines += [
            "",
            'variable "location" {',
            '  description = "Azure location"',
            "  type        = string",
            "  default     = " + json.dumps(r.location),
            "}",
            "",
            'variable "resource_group_name" {',
            '  description = "Azure resource group name"',
            "  type        = string",
            "  default     = " + json.dumps(r.resource_group),
            "}",
        ]
    else:
        r = cloud.resources
        lines += [
            "",
            'variable "cloudflare_account_id" {',
            '  description = "Cloudflare account ID"',
            "  type        = string",
            "  default     = " + json.dumps(r.account_id),
            "}",
        ]
        if r.zone_name:
            lines += [
                "",
                'variable "cloudflare_zone_name" {',
                '  description = "Cloudflare DNS zone name"',
                "  type        = string",
                "  default     = " + json.dumps(r.zone_name),
                "}",
            ]

    return "\n".join(lines) + "\n"


def _root_tfvars(name: str) -> str:
    return (
        "environment_id = "
        + json.dumps(name)
        + "\n"
        'owner          = "launchpad"\n'
        'created_by     = "launchpad-control-plane"\n'
        'ttl_expiration = "unset"\n'
    )


# --------------------------------------------------------------------------- #
# VPC module
# --------------------------------------------------------------------------- #


def _vpc_module_variables(cloud: CloudConfig) -> str:
    lines = [_module_governance_variables().rstrip()]
    if isinstance(cloud, GcpCloudConfig):
        lines += [
            "",
            'variable "project_id" {',
            "  type = string",
            "}",
            "",
            'variable "region" {',
            "  type = string",
            "}",
        ]
    elif isinstance(cloud, AwsCloudConfig):
        lines += [
            "",
            'variable "region" {',
            "  type = string",
            "}",
        ]
    elif isinstance(cloud, AzureCloudConfig):
        lines += [
            "",
            'variable "location" {',
            "  type = string",
            "}",
            "",
            'variable "resource_group_name" {',
            "  type = string",
            "}",
        ]
    return "\n".join(lines) + "\n"


def _vpc_module_main(cloud: CloudConfig) -> str:
    if isinstance(cloud, GcpCloudConfig):
        return _vpc_gcp(cloud.resources)
    if isinstance(cloud, AwsCloudConfig):
        return _vpc_aws(cloud.resources)
    if isinstance(cloud, AzureCloudConfig):
        return _vpc_azure(cloud.resources)
    return "# Cloudflare has no VPC module resources.\n"


def _vpc_gcp(r: GcpResources) -> str:
    blocks: list[str] = [_naming_locals_hcl().rstrip()]
    if r.vpc:
        blocks.append(
            "\n".join(
                [
                    'resource "google_compute_network" "vpc" {',
                    "  name                    = \"${local.name_55}-vpc\"",
                    "  project                 = var.project_id",
                    "  auto_create_subnetworks = false",
                    "}",
                ]
            )
        )
    if r.subnets:
        network_ref = "google_compute_network.vpc.id" if r.vpc else '"default"'
        if r.network_topology == NetworkTopology.STANDARD and r.vpc:
            blocks.append(
                "\n".join(
                    [
                        'resource "google_compute_subnetwork" "public" {',
                        "  name          = \"${local.name_55}-public\"",
                        "  project       = var.project_id",
                        "  region        = var.region",
                        "  network       = " + network_ref,
                        "  ip_cidr_range = local.gcp_public_cidr",
                        "}",
                        "",
                        'resource "google_compute_subnetwork" "private" {',
                        "  name                     = \"${local.name_55}-private\"",
                        "  project                  = var.project_id",
                        "  region                   = var.region",
                        "  network                  = " + network_ref,
                        "  ip_cidr_range            = local.gcp_private_cidr",
                        "  private_ip_google_access = true",
                        "}",
                        "",
                        'resource "google_compute_router" "nat" {',
                        "  name    = \"${local.name_55}-router\"",
                        "  project = var.project_id",
                        "  region  = var.region",
                        "  network = " + network_ref,
                        "}",
                        "",
                        'resource "google_compute_router_nat" "nat" {',
                        "  name                               = \"${local.name_55}-nat\"",
                        "  project                            = var.project_id",
                        "  router                             = google_compute_router.nat.name",
                        "  region                             = var.region",
                        "  nat_ip_allocate_option             = \"AUTO_ONLY\"",
                        "  source_subnetwork_ip_ranges_to_nat = \"LIST_OF_SUBNETWORKS\"",
                        "  subnetwork {",
                        "    name                    = google_compute_subnetwork.private.id",
                        "    source_ip_ranges_to_nat = [\"ALL_IP_RANGES\"]",
                        "  }",
                        "}",
                    ]
                )
            )
        else:
            blocks.append(
                "\n".join(
                    [
                        'resource "google_compute_subnetwork" "subnet" {',
                        "  name          = \"${local.name_55}-subnet\"",
                        "  project       = var.project_id",
                        "  region        = var.region",
                        "  network       = " + network_ref,
                        "  ip_cidr_range = local.gcp_subnet_cidr",
                        "}",
                    ]
                )
            )
    if len(blocks) == 1:
        return _join_blocks([], "# No VPC resources selected.")
    return _join_blocks(blocks, "# No VPC resources selected.")


def _vpc_aws(r: AwsResources) -> str:
    blocks: list[str] = [_naming_locals_hcl().rstrip()]
    if r.vpc:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_vpc" "main" {',
                    "  cidr_block           = local.aws_vpc_cidr",
                    "  enable_dns_hostnames = true",
                    "  enable_dns_support   = true",
                    "",
                    _governance_tags_hcl(
                        "tags", extra={"Name": '"${local.name_55}-vpc"'}
                    ),
                    "}",
                ]
            )
        )
    if r.subnets and r.vpc:
        if r.network_topology == NetworkTopology.STANDARD:
            blocks.append(
                "\n".join(
                    [
                        'resource "aws_subnet" "public" {',
                        "  vpc_id                  = aws_vpc.main.id",
                        "  cidr_block              = local.aws_public_cidr",
                        '  availability_zone       = "${var.region}a"',
                        "  map_public_ip_on_launch = true",
                        "",
                        _governance_tags_hcl(
                            "tags", extra={"Name": '"${local.name_55}-public"'}
                        ),
                        "}",
                        "",
                        'resource "aws_subnet" "private" {',
                        "  vpc_id            = aws_vpc.main.id",
                        "  cidr_block        = local.aws_private_cidr",
                        '  availability_zone = "${var.region}a"',
                        "",
                        _governance_tags_hcl(
                            "tags", extra={"Name": '"${local.name_55}-private"'}
                        ),
                        "}",
                        "",
                        'resource "aws_internet_gateway" "igw" {',
                        "  vpc_id = aws_vpc.main.id",
                        "",
                        _governance_tags_hcl(
                            "tags", extra={"Name": '"${local.name_55}-igw"'}
                        ),
                        "}",
                        "",
                        'resource "aws_eip" "nat" {',
                        '  domain = "vpc"',
                        "",
                        _governance_tags_hcl(
                            "tags", extra={"Name": '"${local.name_55}-nat-eip"'}
                        ),
                        "}",
                        "",
                        'resource "aws_nat_gateway" "nat" {',
                        "  allocation_id = aws_eip.nat.id",
                        "  subnet_id     = aws_subnet.public.id",
                        "",
                        _governance_tags_hcl(
                            "tags", extra={"Name": '"${local.name_55}-nat"'}
                        ),
                        "  depends_on = [aws_internet_gateway.igw]",
                        "}",
                        "",
                        'resource "aws_route_table" "public" {',
                        "  vpc_id = aws_vpc.main.id",
                        "  route {",
                        '    cidr_block = "0.0.0.0/0"',
                        "    gateway_id = aws_internet_gateway.igw.id",
                        "  }",
                        "",
                        _governance_tags_hcl(
                            "tags", extra={"Name": '"${local.name_55}-public-rt"'}
                        ),
                        "}",
                        "",
                        'resource "aws_route_table" "private" {',
                        "  vpc_id = aws_vpc.main.id",
                        "  route {",
                        '    cidr_block     = "0.0.0.0/0"',
                        "    nat_gateway_id = aws_nat_gateway.nat.id",
                        "  }",
                        "",
                        _governance_tags_hcl(
                            "tags", extra={"Name": '"${local.name_55}-private-rt"'}
                        ),
                        "}",
                        "",
                        'resource "aws_route_table_association" "public" {',
                        "  subnet_id      = aws_subnet.public.id",
                        "  route_table_id = aws_route_table.public.id",
                        "}",
                        "",
                        'resource "aws_route_table_association" "private" {',
                        "  subnet_id      = aws_subnet.private.id",
                        "  route_table_id = aws_route_table.private.id",
                        "}",
                    ]
                )
            )
        else:
            blocks.append(
                "\n".join(
                    [
                        'resource "aws_subnet" "public" {',
                        "  vpc_id                  = aws_vpc.main.id",
                        "  cidr_block              = local.aws_public_cidr",
                        '  availability_zone       = "${var.region}a"',
                        "  map_public_ip_on_launch = true",
                        "",
                        _governance_tags_hcl(
                            "tags", extra={"Name": '"${local.name_55}-subnet"'}
                        ),
                        "}",
                        "",
                        'resource "aws_internet_gateway" "igw" {',
                        "  vpc_id = aws_vpc.main.id",
                        "",
                        _governance_tags_hcl(
                            "tags", extra={"Name": '"${local.name_55}-igw"'}
                        ),
                        "}",
                        "",
                        'resource "aws_route_table" "public" {',
                        "  vpc_id = aws_vpc.main.id",
                        "  route {",
                        '    cidr_block = "0.0.0.0/0"',
                        "    gateway_id = aws_internet_gateway.igw.id",
                        "  }",
                        "",
                        _governance_tags_hcl(
                            "tags", extra={"Name": '"${local.name_55}-rt"'}
                        ),
                        "}",
                        "",
                        'resource "aws_route_table_association" "public" {',
                        "  subnet_id      = aws_subnet.public.id",
                        "  route_table_id = aws_route_table.public.id",
                        "}",
                    ]
                )
            )
    if len(blocks) == 1:
        return _join_blocks([], "# No VPC resources selected.")
    return _join_blocks(blocks, "# No VPC resources selected.")


def _vpc_azure(r: AzureResources) -> str:
    blocks: list[str] = [_naming_locals_hcl().rstrip()]
    if r.vnet:
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_virtual_network" "vnet" {',
                    "  name                = \"${local.name_55}-vnet\"",
                    "  resource_group_name = var.resource_group_name",
                    "  location            = var.location",
                    "  address_space       = [local.azure_vnet_cidr]",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.subnets and r.vnet:
        if r.network_topology == NetworkTopology.STANDARD:
            blocks.append(
                "\n".join(
                    [
                        'resource "azurerm_subnet" "public" {',
                        "  name                 = \"${local.name_55}-public\"",
                        "  resource_group_name  = var.resource_group_name",
                        "  virtual_network_name = azurerm_virtual_network.vnet.name",
                        "  address_prefixes     = [local.azure_public_cidr]",
                        "}",
                        "",
                        'resource "azurerm_subnet" "private" {',
                        "  name                 = \"${local.name_55}-private\"",
                        "  resource_group_name  = var.resource_group_name",
                        "  virtual_network_name = azurerm_virtual_network.vnet.name",
                        "  address_prefixes     = [local.azure_private_cidr]",
                        "}",
                        "",
                        'resource "azurerm_public_ip" "nat" {',
                        "  name                = \"${local.name_55}-nat-pip\"",
                        "  resource_group_name = var.resource_group_name",
                        "  location            = var.location",
                        '  allocation_method   = "Static"',
                        '  sku                 = "Standard"',
                        "",
                        _governance_tags_hcl("tags"),
                        "}",
                        "",
                        'resource "azurerm_nat_gateway" "nat" {',
                        "  name                = \"${local.name_55}-nat\"",
                        "  resource_group_name = var.resource_group_name",
                        "  location            = var.location",
                        '  sku_name            = "Standard"',
                        "",
                        _governance_tags_hcl("tags"),
                        "}",
                        "",
                        'resource "azurerm_nat_gateway_public_ip_association" "nat" {',
                        "  nat_gateway_id       = azurerm_nat_gateway.nat.id",
                        "  public_ip_address_id = azurerm_public_ip.nat.id",
                        "}",
                        "",
                        'resource "azurerm_subnet_nat_gateway_association" "private" {',
                        "  subnet_id      = azurerm_subnet.private.id",
                        "  nat_gateway_id = azurerm_nat_gateway.nat.id",
                        "}",
                    ]
                )
            )
        else:
            blocks.append(
                "\n".join(
                    [
                        'resource "azurerm_subnet" "primary" {',
                        "  name                 = \"${local.name_55}-subnet\"",
                        "  resource_group_name  = var.resource_group_name",
                        "  virtual_network_name = azurerm_virtual_network.vnet.name",
                        "  address_prefixes     = [local.azure_subnet_cidr]",
                        "}",
                    ]
                )
            )
    if len(blocks) == 1:
        return _join_blocks([], "# No VPC resources selected.")
    return _join_blocks(blocks, "# No VPC resources selected.")


def _vpc_module_outputs(cloud: CloudConfig) -> str:
    if isinstance(cloud, GcpCloudConfig):
        r = cloud.resources
        lines: list[str] = []
        if r.vpc:
            lines += [
                'output "vpc_id" {',
                "  value = google_compute_network.vpc.id",
                "}",
                "",
                'output "network_id" {',
                "  value = google_compute_network.vpc.id",
                "}",
            ]
        else:
            lines += [
                'output "vpc_id" {',
                '  value = null',
                "}",
                "",
                'output "network_id" {',
                '  value = "default"',
                "}",
            ]
        if r.subnets:
            if r.network_topology == NetworkTopology.STANDARD and r.vpc:
                lines += [
                    "",
                    'output "subnet_id" {',
                    "  value = google_compute_subnetwork.private.id",
                    "}",
                    "",
                    'output "public_subnet_id" {',
                    "  value = google_compute_subnetwork.public.id",
                    "}",
                    "",
                    'output "private_subnet_id" {',
                    "  value = google_compute_subnetwork.private.id",
                    "}",
                ]
            else:
                lines += [
                    "",
                    'output "subnet_id" {',
                    "  value = google_compute_subnetwork.subnet.id",
                    "}",
                    "",
                    'output "public_subnet_id" {',
                    "  value = google_compute_subnetwork.subnet.id",
                    "}",
                    "",
                    'output "private_subnet_id" {',
                    "  value = google_compute_subnetwork.subnet.id",
                    "}",
                ]
        else:
            lines += [
                "",
                'output "subnet_id" {',
                "  value = null",
                "}",
                "",
                'output "public_subnet_id" {',
                "  value = null",
                "}",
                "",
                'output "private_subnet_id" {',
                "  value = null",
                "}",
            ]
        return "\n".join(lines) + "\n"

    if isinstance(cloud, AwsCloudConfig):
        r = cloud.resources
        lines = []
        if r.vpc:
            lines += [
                'output "vpc_id" {',
                "  value = aws_vpc.main.id",
                "}",
            ]
        else:
            lines += ['output "vpc_id" {', "  value = null", "}"]
        if r.subnets and r.vpc:
            if r.network_topology == NetworkTopology.STANDARD:
                lines += [
                    "",
                    'output "public_subnet_id" {',
                    "  value = aws_subnet.public.id",
                    "}",
                    "",
                    'output "private_subnet_id" {',
                    "  value = aws_subnet.private.id",
                    "}",
                    "",
                    'output "subnet_ids" {',
                    "  value = [aws_subnet.public.id, aws_subnet.private.id]",
                    "}",
                ]
            else:
                lines += [
                    "",
                    'output "public_subnet_id" {',
                    "  value = aws_subnet.public.id",
                    "}",
                    "",
                    'output "private_subnet_id" {',
                    "  value = aws_subnet.public.id",
                    "}",
                    "",
                    'output "subnet_ids" {',
                    "  value = [aws_subnet.public.id]",
                    "}",
                ]
        else:
            lines += [
                "",
                'output "public_subnet_id" {',
                "  value = null",
                "}",
                "",
                'output "private_subnet_id" {',
                "  value = null",
                "}",
                "",
                'output "subnet_ids" {',
                "  value = []",
                "}",
            ]
        return "\n".join(lines) + "\n"

    if isinstance(cloud, AzureCloudConfig):
        r = cloud.resources
        lines = []
        if r.vnet:
            lines += [
                'output "vnet_id" {',
                "  value = azurerm_virtual_network.vnet.id",
                "}",
                "",
                'output "vnet_name" {',
                "  value = azurerm_virtual_network.vnet.name",
                "}",
            ]
        else:
            lines += [
                'output "vnet_id" {',
                "  value = null",
                "}",
                "",
                'output "vnet_name" {',
                "  value = null",
                "}",
            ]
        if r.subnets and r.vnet:
            if r.network_topology == NetworkTopology.STANDARD:
                lines += [
                    "",
                    'output "subnet_id" {',
                    "  value = azurerm_subnet.private.id",
                    "}",
                    "",
                    'output "public_subnet_id" {',
                    "  value = azurerm_subnet.public.id",
                    "}",
                    "",
                    'output "private_subnet_id" {',
                    "  value = azurerm_subnet.private.id",
                    "}",
                ]
            else:
                lines += [
                    "",
                    'output "subnet_id" {',
                    "  value = azurerm_subnet.primary.id",
                    "}",
                    "",
                    'output "public_subnet_id" {',
                    "  value = azurerm_subnet.primary.id",
                    "}",
                    "",
                    'output "private_subnet_id" {',
                    "  value = azurerm_subnet.primary.id",
                    "}",
                ]
        else:
            lines += [
                "",
                'output "subnet_id" {',
                "  value = null",
                "}",
                "",
                'output "public_subnet_id" {',
                "  value = null",
                "}",
                "",
                'output "private_subnet_id" {',
                "  value = null",
                "}",
            ]
        return "\n".join(lines) + "\n"

    return (
        'output "vpc_id" {\n'
        "  value = null\n"
        "}\n"
    )


# --------------------------------------------------------------------------- #
# Cluster module
# --------------------------------------------------------------------------- #


def _cluster_module_variables(cloud: CloudConfig) -> str:
    lines = [_module_governance_variables().rstrip()]
    if isinstance(cloud, GcpCloudConfig):
        lines += [
            "",
            'variable "project_id" {',
            "  type = string",
            "}",
            "",
            'variable "region" {',
            "  type = string",
            "}",
            "",
            'variable "network" {',
            "  type    = string",
            '  default = "default"',
            "}",
            "",
            'variable "subnetwork" {',
            "  type    = string",
            "  default = null",
            "}",
            "",
            'variable "machine_type" {',
            "  type    = string",
            '  default = "e2-medium"',
            "}",
            "",
            'variable "app_listen_port" {',
            "  type    = number",
            "  default = 8080",
            "}",
            "",
            'variable "app_image" {',
            "  type    = string",
            '  default = ""',
            "}",
            "",
            'variable "ssh_public_key" {',
            "  type    = string",
            '  default = ""',
            "}",
        ]
    elif isinstance(cloud, AwsCloudConfig):
        lines += [
            "",
            'variable "region" {',
            "  type = string",
            "}",
            "",
            'variable "subnet_ids" {',
            "  type    = list(string)",
            "  default = []",
            "}",
            "",
            'variable "public_subnet_id" {',
            "  type    = string",
            "  default = null",
            "}",
            "",
            'variable "app_listen_port" {',
            "  type    = number",
            "  default = 8080",
            "}",
            "",
            'variable "ssh_public_key" {',
            "  type    = string",
            '  default = ""',
            "}",
        ]
    elif isinstance(cloud, AzureCloudConfig):
        lines += [
            "",
            'variable "location" {',
            "  type = string",
            "}",
            "",
            'variable "resource_group_name" {',
            "  type = string",
            "}",
            "",
            'variable "subnet_id" {',
            "  type    = string",
            "  default = null",
            "}",
        ]
    return "\n".join(lines) + "\n"


def _cluster_module_main(cloud: CloudConfig) -> str:
    if isinstance(cloud, GcpCloudConfig):
        return _cluster_gcp(cloud.resources)
    if isinstance(cloud, AwsCloudConfig):
        return _cluster_aws(cloud.resources)
    if isinstance(cloud, AzureCloudConfig):
        return _cluster_azure(cloud.resources)
    return "# Cloudflare has no cluster module resources.\n"


def _cluster_gcp(r: GcpResources) -> str:
    blocks: list[str] = [_naming_locals_hcl().rstrip()]
    if r.gke:
        blocks.append(
            "\n".join(
                [
                    'resource "google_container_cluster" "gke" {',
                    "  name                     = local.gke_cluster_name",
                    "  project                  = var.project_id",
                    "  location                 = var.region",
                    "  remove_default_node_pool = true",
                    "  initial_node_count       = 1",
                    "  network                  = var.network",
                    "  subnetwork               = var.subnetwork",
                    "  # Ephemeral / preview stacks must be destroyable without a second apply.",
                    "  deletion_protection      = false",
                    "",
                    _governance_tags_hcl("resource_labels", gcp=True),
                    "}",
                    "",
                    'resource "google_container_node_pool" "gke_primary" {',
                    "  name       = local.gke_node_pool_name",
                    "  project    = var.project_id",
                    "  location   = var.region",
                    "  cluster    = google_container_cluster.gke.name",
                    "  node_count = 2",
                    "",
                    "  depends_on = [google_container_cluster.gke]",
                    "",
                    '  provisioner "local-exec" {',
                    '    when    = destroy',
                    '    command = "sleep 30"',
                    '  }',
                    "",
                    "  node_config {",
                    f'    machine_type = "{r.machine_type}"',
                    '    oauth_scopes = [',
                    '      "https://www.googleapis.com/auth/cloud-platform",',
                    '    ]',
                    "",
                    _governance_tags_hcl("labels", "    ", gcp=True),
                    "  }",
                    "}",
                ]
            )
        )
    if r.cloud_run:
        blocks.append(
            "\n".join(
                [
                    'resource "google_cloud_run_v2_service" "app" {',
                    "  name     = \"${local.name_55}-run\"",
                    "  project  = var.project_id",
                    "  location = var.region",
                    "",
                    "  template {",
                    "    containers {",
                    '      image = var.app_image != "" ? var.app_image : "us-docker.pkg.dev/cloudrun/container/hello"',
                    "      ports {",
                    '        container_port = var.app_listen_port',
                    "      }",
                    "    }",
                    "  }",
                    "",
                    _governance_tags_hcl("labels", gcp=True),
                    "}",
                ]
            )
        )
    if getattr(r, "compute_instance", False):
        blocks.append(
            "\n".join(
                [
                    'data "google_compute_image" "ubuntu" {',
                    '  family  = "ubuntu-2204-lts"',
                    '  project = "ubuntu-os-cloud"',
                    "}",
                    "",
                    'resource "google_compute_firewall" "launchpad_vm" {',
                    '  name    = "${local.name_55}-vm-fw"',
                    "  project = var.project_id",
                    "  network = var.network",
                    "",
                    "  allow {",
                    '    protocol = "tcp"',
                    "    ports    = [\"22\", tostring(var.app_listen_port), \"80\", \"443\"]",
                    "  }",
                    "",
                    '  source_ranges = ["0.0.0.0/0"]',
                    '  target_tags   = ["launchpad-vm"]',
                    "}",
                    "",
                    'resource "google_compute_instance" "app" {',
                    '  name         = "${local.name_55}-vm"',
                    "  project      = var.project_id",
                    "  machine_type = var.machine_type",
                    '  zone         = "${var.region}-a"',
                    "",
                    '  tags = ["launchpad-vm"]',
                    "",
                    "  boot_disk {",
                    "    initialize_params {",
                    "      image = data.google_compute_image.ubuntu.self_link",
                    "      size  = 20",
                    "    }",
                    "  }",
                    "",
                    "  network_interface {",
                    "    network    = var.network",
                    "    subnetwork = var.subnetwork",
                    "    access_config {}",
                    "  }",
                    "",
                    "  metadata = merge(",
                    '    { enable-oslogin = "FALSE" },',
                    '    var.ssh_public_key != "" ? { ssh-keys = "ubuntu:${var.ssh_public_key}" } : {}',
                    "  )",
                    "",
                    "  metadata_startup_script = <<-EOT",
                    "    #!/bin/bash",
                    "    set -euo pipefail",
                    "    export DEBIAN_FRONTEND=noninteractive",
                    "    apt-get update -y",
                    "    apt-get install -y curl ca-certificates gnupg git",
                    '    if [ -n "${var.ssh_public_key}" ]; then',
                    "      install -d -m 700 -o ubuntu -g ubuntu /home/ubuntu/.ssh",
                    '      grep -qxF "${var.ssh_public_key}" /home/ubuntu/.ssh/authorized_keys 2>/dev/null \\',
                    '        || printf "%s\\n" "${var.ssh_public_key}" >> /home/ubuntu/.ssh/authorized_keys',
                    "      chown -R ubuntu:ubuntu /home/ubuntu/.ssh",
                    "      chmod 600 /home/ubuntu/.ssh/authorized_keys",
                    "    fi",
                    "  EOT",
                    "",
                    _governance_tags_hcl("labels", gcp=True),
                    "}",
                ]
            )
        )
    if len(blocks) == 1:
        return _join_blocks([], "# No cluster resources selected.")
    return _join_blocks(blocks, "# No cluster resources selected.")


def _cluster_aws(r: AwsResources) -> str:
    blocks: list[str] = [_naming_locals_hcl().rstrip()]
    if r.ec2:
        blocks.append(
            "\n".join(
                [
                    'data "aws_ami" "ubuntu" {',
                    "  most_recent = true",
                    '  owners      = ["099720109477"]',
                    "",
                    "  filter {",
                    '    name   = "name"',
                    '    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]',
                    "  }",
                    "}",
                    "",
                    'data "aws_vpc" "default" {',
                    "  count   = var.public_subnet_id == null ? 1 : 0",
                    "  default = true",
                    "}",
                    "",
                    'data "aws_subnets" "default_public" {',
                    "  count = var.public_subnet_id == null ? 1 : 0",
                    "  filter {",
                    '    name   = "vpc-id"',
                    "    values = [data.aws_vpc.default[0].id]",
                    "  }",
                    "}",
                    "",
                    'data "aws_subnet" "selected" {',
                    "  id = var.public_subnet_id != null ? var.public_subnet_id : data.aws_subnets.default_public[0].ids[0]",
                    "}",
                    "",
                    'resource "aws_security_group" "launchpad_vm" {',
                    '  name        = "${local.name_55}-vm-sg"',
                    '  description = "Launchpad VM ingress"',
                    "  vpc_id      = data.aws_subnet.selected.vpc_id",
                    "",
                    "  ingress {",
                    "    from_port   = 22",
                    "    to_port     = 22",
                    '    protocol    = "tcp"',
                    '    cidr_blocks = ["0.0.0.0/0"]',
                    "  }",
                    "",
                    "  ingress {",
                    "    from_port   = var.app_listen_port",
                    "    to_port     = var.app_listen_port",
                    '    protocol    = "tcp"',
                    '    cidr_blocks = ["0.0.0.0/0"]',
                    "  }",
                    "",
                    "  ingress {",
                    "    from_port   = 80",
                    "    to_port     = 80",
                    '    protocol    = "tcp"',
                    '    cidr_blocks = ["0.0.0.0/0"]',
                    "  }",
                    "",
                    "  egress {",
                    "    from_port   = 0",
                    "    to_port     = 0",
                    '    protocol    = "-1"',
                    '    cidr_blocks = ["0.0.0.0/0"]',
                    "  }",
                    "",
                    _governance_tags_hcl(
                        "tags", extra={"Name": '"${local.name_55}-vm-sg"'}
                    ),
                    "}",
                    "",
                    'resource "aws_key_pair" "launchpad" {',
                    '  count      = var.ssh_public_key != "" ? 1 : 0',
                    '  key_name   = "${local.name_55}-key"',
                    "  public_key = var.ssh_public_key",
                    "}",
                    "",
                    'resource "aws_instance" "app" {',
                    "  ami                         = data.aws_ami.ubuntu.id",
                    f'  instance_type               = "{r.instance_type}"',
                    "  subnet_id                   = data.aws_subnet.selected.id",
                    "  vpc_security_group_ids      = [aws_security_group.launchpad_vm.id]",
                    "  associate_public_ip_address = true",
                    "  key_name                    = length(aws_key_pair.launchpad) > 0 ? aws_key_pair.launchpad[0].key_name : null",
                    "",
                    "  user_data = <<-EOT",
                    "    #!/bin/bash",
                    "    set -euo pipefail",
                    "    export DEBIAN_FRONTEND=noninteractive",
                    "    apt-get update -y",
                    "    apt-get install -y curl ca-certificates gnupg git",
                    "  EOT",
                    "",
                    _governance_tags_hcl(
                        "tags", extra={"Name": '"${local.name_55}-ec2"'}
                    ),
                    "}",
                ]
            )
        )
    if r.eks:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_iam_role" "eks_cluster" {',
                    '  name = "${local.name_55}-eks-role"',
                    "",
                    "  assume_role_policy = jsonencode({",
                    '    Version = "2012-10-17"',
                    "    Statement = [{",
                    '      Action    = "sts:AssumeRole"',
                    '      Effect    = "Allow"',
                    "      Principal = {",
                    '        Service = "eks.amazonaws.com"',
                    "      }",
                    "    }]",
                    "  })",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                    "",
                    'resource "aws_eks_cluster" "main" {',
                    '  name     = "${local.name_55}-eks"',
                    "  role_arn = aws_iam_role.eks_cluster.arn",
                    "",
                    "  vpc_config {",
                    "    subnet_ids = var.subnet_ids",
                    "  }",
                    "",
                    "  depends_on = [aws_iam_role.eks_cluster]",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.app_runner:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_apprunner_service" "app" {',
                    '  service_name = "${local.name_55}-runner"',
                    "",
                    "  source_configuration {",
                    "    auto_deployments_enabled = false",
                    "",
                    "    image_repository {",
                    '      image_identifier      = "public.ecr.aws/aws-containers/hello-app-runner:latest"',
                    '      image_repository_type = "ECR_PUBLIC"',
                    "",
                    "      image_configuration {",
                    '        port = "8080"',
                    "      }",
                    "    }",
                    "  }",
                    "",
                    "  instance_configuration {",
                    '    cpu    = "256"',
                    '    memory = "512"',
                    "  }",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if len(blocks) == 1:
        return _join_blocks([], "# No cluster resources selected.")
    return _join_blocks(blocks, "# No cluster resources selected.")


def _cluster_azure(r: AzureResources) -> str:
    blocks: list[str] = [_naming_locals_hcl().rstrip()]
    if r.aks:
        node_pool = [
            "  default_node_pool {",
            '    name       = "default"',
            "    node_count = 2",
            f'    vm_size    = "{r.vm_size}"',
        ]
        if r.subnets and r.vnet:
            node_pool.append("    vnet_subnet_id = var.subnet_id")
        node_pool.append("  }")
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_kubernetes_cluster" "aks" {',
                    '  name                = "${local.name_55}-aks"',
                    "  resource_group_name = var.resource_group_name",
                    "  location            = var.location",
                    "  dns_prefix          = local.name_40",
                    "",
                    *node_pool,
                    "",
                    "  identity {",
                    '    type = "SystemAssigned"',
                    "  }",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.container_apps:
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_container_app_environment" "main" {',
                    '  name                = "${local.name_55}-cae"',
                    "  resource_group_name = var.resource_group_name",
                    "  location            = var.location",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                    "",
                    'resource "azurerm_container_app" "app" {',
                    '  name                         = "${local.name_55}-app"',
                    "  container_app_environment_id = azurerm_container_app_environment.main.id",
                    "  resource_group_name          = var.resource_group_name",
                    '  revision_mode                = "Single"',
                    "",
                    "  template {",
                    "    container {",
                    '      name   = "app"',
                    '      image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"',
                    "      cpu    = 0.5",
                    '      memory = "1Gi"',
                    "    }",
                    "  }",
                    "",
                    "  depends_on = [azurerm_container_app_environment.main]",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if len(blocks) == 1:
        return _join_blocks([], "# No cluster resources selected.")
    return _join_blocks(blocks, "# No cluster resources selected.")


def _cluster_module_outputs(cloud: CloudConfig) -> str:
    if isinstance(cloud, GcpCloudConfig):
        r = cloud.resources
        lines: list[str] = []
        if r.gke:
            lines += [
                'output "gke_cluster_endpoint" {',
                "  value     = google_container_cluster.gke.endpoint",
                "  sensitive = true",
                "}",
                "",
                'output "gke_cluster_ca_certificate" {',
                "  value     = google_container_cluster.gke.master_auth[0].cluster_ca_certificate",
                "  sensitive = true",
                "}",
                "",
                'output "gke_cluster_name" {',
                "  value = google_container_cluster.gke.name",
                "}",
            ]
        if r.cloud_run:
            if lines:
                lines.append("")
            lines += [
                'output "cloud_run_url" {',
                "  value = google_cloud_run_v2_service.app.uri",
                "}",
                "",
                'output "preview_url" {',
                "  value = google_cloud_run_v2_service.app.uri",
                "}",
            ]
        if getattr(r, "compute_instance", False):
            if lines:
                lines.append("")
            lines += [
                'output "compute_instance_id" {',
                "  value = google_compute_instance.app.id",
                "}",
                "",
                'output "public_ip" {',
                "  value = google_compute_instance.app.network_interface[0].access_config[0].nat_ip",
                "}",
                "",
                'output "preview_url" {',
                '  value = format("http://%s:%s", google_compute_instance.app.network_interface[0].access_config[0].nat_ip, var.app_listen_port)',
                "}",
            ]
        if not lines:
            lines = ["# No cluster outputs."]
        return "\n".join(lines) + "\n"

    if isinstance(cloud, AwsCloudConfig):
        r = cloud.resources
        lines = []
        if r.eks:
            lines += [
                'output "eks_cluster_endpoint" {',
                "  value     = aws_eks_cluster.main.endpoint",
                "  sensitive = true",
                "}",
                "",
                'output "eks_cluster_name" {',
                "  value = aws_eks_cluster.main.name",
                "}",
            ]
        if r.ec2:
            if lines:
                lines.append("")
            lines += [
                'output "ec2_instance_id" {',
                "  value = aws_instance.app.id",
                "}",
                "",
                'output "public_ip" {',
                "  value = aws_instance.app.public_ip",
                "}",
                "",
                'output "preview_url" {',
                '  value = format("http://%s:%s", aws_instance.app.public_ip, var.app_listen_port)',
                "}",
            ]
        if r.app_runner:
            if lines:
                lines.append("")
            lines += [
                'output "app_runner_service_url" {',
                "  value = aws_apprunner_service.app.service_url",
                "}",
                "",
                'output "preview_url" {',
                '  value = format("https://%s", aws_apprunner_service.app.service_url)',
                "}",
            ]
        if not lines:
            lines = ["# No cluster outputs."]
        return "\n".join(lines) + "\n"

    if isinstance(cloud, AzureCloudConfig):
        r = cloud.resources
        lines = []
        if r.aks:
            lines += [
                'output "aks_cluster_name" {',
                "  value = azurerm_kubernetes_cluster.aks.name",
                "}",
            ]
        if r.container_apps:
            if lines:
                lines.append("")
            lines += [
                'output "container_app_name" {',
                "  value = azurerm_container_app.app.name",
                "}",
            ]
        if not lines:
            lines = ["# No cluster outputs."]
        return "\n".join(lines) + "\n"

    return "# No cluster outputs.\n"


# --------------------------------------------------------------------------- #
# Secrets module
# --------------------------------------------------------------------------- #


def _secrets_module_variables(cloud: CloudConfig) -> str:
    lines = [_module_governance_variables().rstrip()]
    if isinstance(cloud, GcpCloudConfig):
        lines += [
            "",
            'variable "project_id" {',
            "  type = string",
            "}",
        ]
    elif isinstance(cloud, AzureCloudConfig):
        lines += [
            "",
            'variable "location" {',
            "  type = string",
            "}",
            "",
            'variable "resource_group_name" {',
            "  type = string",
            "}",
        ]
    return "\n".join(lines) + "\n"


def _secrets_module_main(cloud: CloudConfig) -> str:
    if isinstance(cloud, GcpCloudConfig):
        r = cloud.resources
        naming = _naming_locals_hcl().rstrip()
        if r.secret_backend == SecretBackend.SECRET_MANAGER:
            return (
                naming
                + "\n\n"
                + "\n".join(
                    [
                        'resource "google_secret_manager_secret" "app_secrets" {',
                        "  project   = var.project_id",
                        '  secret_id = "${local.name_55}-secrets"',
                        "",
                        "  replication {",
                        "    auto {}",
                        "  }",
                        "",
                        _governance_tags_hcl("labels", gcp=True),
                        "}",
                    ]
                )
                + "\n"
            )
        return (
            naming
            + "\n\n"
            + "\n".join(
                [
                    'resource "kubernetes_secret" "app_secrets" {',
                    "  metadata {",
                    '    name      = "${local.name_55}-secrets"',
                    '    namespace = "default"',
                    "",
                    _governance_tags_hcl("labels", "    "),
                    "  }",
                    "",
                    '  type = "Opaque"',
                    "}",
                ]
            )
            + "\n"
        )

    if isinstance(cloud, AwsCloudConfig):
        if not cloud.resources.secrets_manager:
            return "# No secrets resources selected.\n"
        return (
            _naming_locals_hcl().rstrip()
            + "\n\n"
            + "\n".join(
                [
                    'resource "aws_secretsmanager_secret" "app_secrets" {',
                    '  name = "${local.name_55}-secrets"',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
            + "\n"
        )

    if isinstance(cloud, AzureCloudConfig):
        if not cloud.resources.key_vault:
            return "# No secrets resources selected.\n"
        return (
            _naming_locals_hcl().rstrip()
            + "\n\n"
            + "\n".join(
                [
                    'data "azurerm_client_config" "current" {}',
                    "",
                    'resource "azurerm_key_vault" "main" {',
                    # Key Vault names: 3-24 chars, alphanumeric only.
                    '  name                = substr(replace(local.name_63, "-", ""), 0, 24)',
                    "  resource_group_name = var.resource_group_name",
                    "  location            = var.location",
                    "  tenant_id           = data.azurerm_client_config.current.tenant_id",
                    '  sku_name            = "standard"',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
            + "\n"
        )

    return "# Cloudflare has no secrets module resources.\n"


def _secrets_module_outputs(cloud: CloudConfig) -> str:
    if isinstance(cloud, GcpCloudConfig):
        if cloud.resources.secret_backend == SecretBackend.SECRET_MANAGER:
            return (
                'output "secret_id" {\n'
                "  value = google_secret_manager_secret.app_secrets.id\n"
                "}\n"
            )
        return (
            'output "secret_name" {\n'
            "  value = kubernetes_secret.app_secrets.metadata[0].name\n"
            "}\n"
        )
    if isinstance(cloud, AwsCloudConfig) and cloud.resources.secrets_manager:
        return (
            'output "secrets_manager_arn" {\n'
            "  value = aws_secretsmanager_secret.app_secrets.arn\n"
            "}\n"
        )
    if isinstance(cloud, AzureCloudConfig) and cloud.resources.key_vault:
        return (
            'output "key_vault_uri" {\n'
            "  value = azurerm_key_vault.main.vault_uri\n"
            "}\n"
        )
    return "# No secrets outputs.\n"


# --------------------------------------------------------------------------- #
# Root main.tf / outputs.tf / extras
# --------------------------------------------------------------------------- #


def _root_main(name: str, cloud: CloudConfig) -> str:
    header = [
        "# Generated by Launchpad IaC Generator - do not hand-edit; regenerate via the wizard.",
        "# Environment: " + name,
        "# Modular layout: modules/{vpc,cluster,secrets}",
        "",
    ]
    body: list[str] = []

    if isinstance(cloud, AzureCloudConfig):
        body.append(
            "\n".join(
                [
                    'resource "azurerm_resource_group" "main" {',
                    "  name     = var.resource_group_name",
                    "  location = var.location",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )

    body.append(_module_vpc_block(cloud))
    body.append(_module_cluster_block(cloud))
    body.append(_module_secrets_block(cloud))

    extras = _root_extra_resources(cloud)
    if extras.strip():
        body.append(extras.rstrip())

    return "\n".join(header) + "\n" + "\n\n".join(body) + "\n"


def _module_vpc_block(cloud: CloudConfig) -> str:
    lines = [
        'module "vpc" {',
        '  source = "./modules/vpc"',
        "",
        _governance_variable_pass_through().rstrip(),
    ]
    if isinstance(cloud, GcpCloudConfig):
        lines += [
            "  project_id = var.project_id",
            "  region     = var.region",
            "",
            "  depends_on = [google_project_service.apis]",
        ]
    elif isinstance(cloud, AwsCloudConfig):
        lines += ["  region = var.region"]
    elif isinstance(cloud, AzureCloudConfig):
        lines += [
            "  location            = azurerm_resource_group.main.location",
            "  resource_group_name = azurerm_resource_group.main.name",
        ]
    lines.append("}")
    return "\n".join(lines)


def _module_cluster_block(cloud: CloudConfig) -> str:
    lines = [
        'module "cluster" {',
        '  source = "./modules/cluster"',
        "",
        _governance_variable_pass_through().rstrip(),
    ]
    if isinstance(cloud, GcpCloudConfig):
        lines += [
            "  project_id      = var.project_id",
            "  region          = var.region",
            "  network         = module.vpc.network_id",
            "  subnetwork      = module.vpc.subnet_id",
            "  machine_type    = var.machine_type",
            "  app_listen_port = var.app_listen_port",
            "  app_image       = var.app_image",
            "  ssh_public_key  = var.ssh_public_key",
            "",
            # APIs before cluster create; VPC before destroy of network-bound cluster.
            "  depends_on = [google_project_service.apis, module.vpc]",
        ]
    elif isinstance(cloud, AwsCloudConfig):
        lines += [
            "  region           = var.region",
            "  subnet_ids       = module.vpc.subnet_ids",
            "  public_subnet_id = module.vpc.public_subnet_id",
            "  app_listen_port  = var.app_listen_port",
            "  ssh_public_key   = var.ssh_public_key",
            "",
            "  depends_on = [module.vpc]",
        ]
    elif isinstance(cloud, AzureCloudConfig):
        lines += [
            "  location            = azurerm_resource_group.main.location",
            "  resource_group_name = azurerm_resource_group.main.name",
            "  subnet_id           = module.vpc.subnet_id",
            "",
            "  depends_on = [module.vpc]",
        ]
    lines.append("}")
    return "\n".join(lines)


def _module_secrets_block(cloud: CloudConfig) -> str:
    lines = [
        'module "secrets" {',
        '  source = "./modules/secrets"',
        "",
        _governance_variable_pass_through().rstrip(),
    ]
    if isinstance(cloud, GcpCloudConfig):
        lines += ["  project_id = var.project_id"]
        deps = ["google_project_service.apis"]
        if (
            cloud.resources.gke
            and cloud.resources.secret_backend == SecretBackend.NATIVE_K8S
        ):
            # Wait for GKE so the kubernetes provider (wired to the cluster) can create the secret.
            deps.append("module.cluster")
        lines += ["", f"  depends_on = [{', '.join(deps)}]"]
    elif isinstance(cloud, AzureCloudConfig):
        lines += [
            "  location            = azurerm_resource_group.main.location",
            "  resource_group_name = azurerm_resource_group.main.name",
        ]
    lines.append("}")
    return "\n".join(lines)


def _root_extra_resources(cloud: CloudConfig) -> str:
    if isinstance(cloud, GcpCloudConfig):
        return _extras_gcp(cloud.resources)
    if isinstance(cloud, AwsCloudConfig):
        return _extras_aws(cloud.resources)
    if isinstance(cloud, AzureCloudConfig):
        return _extras_azure(cloud.resources)
    return _extras_cloudflare(cloud.resources)


def _extras_gcp(r: GcpResources) -> str:
    blocks: list[str] = [_naming_locals_hcl().rstrip()]
    if r.artifact_registry:
        blocks.append(
            "\n".join(
                [
                    'resource "google_artifact_registry_repository" "ar" {',
                    "  project       = var.project_id",
                    "  location      = var.region",
                    "  repository_id = local.name_63",
                    '  format        = "DOCKER"',
                    "",
                    "  depends_on = [google_project_service.apis]",
                    "",
                    _governance_tags_hcl("labels", gcp=True),
                    "}",
                ]
            )
        )
    if r.cloud_functions:
        blocks.append(
            "\n".join(
                [
                    'resource "google_cloudfunctions2_function" "fn" {',
                    '  name     = "${local.name_55}-fn"',
                    "  project  = var.project_id",
                    "  location = var.region",
                    "",
                    "  build_config {",
                    '    runtime     = "nodejs20"',
                    '    entry_point = "handler"',
                    "",
                    "    source {",
                    "      storage_source {",
                    '        bucket = "${local.gcs_bucket_name}-fn"',
                    '        object = "function-source.zip"',
                    "      }",
                    "    }",
                    "  }",
                    "",
                    "  service_config {",
                    "    max_instance_count = 3",
                    '    available_memory   = "256M"',
                    "  }",
                    "",
                    "  depends_on = [google_project_service.apis]",
                    "",
                    _governance_tags_hcl("labels", gcp=True),
                    "}",
                ]
            )
        )

    if r.cloud_sql:
        db_version = _gcp_cloud_sql_database_version(r.cloud_sql_engine)
        blocks.append(
            "\n".join(
                [
                    'resource "google_sql_database_instance" "primary" {',
                    '  name             = "${local.name_55}-sql"',
                    "  project          = var.project_id",
                    "  region           = var.region",
                    f'  database_version = "{db_version}"',
                    "",
                    "  settings {",
                    '    tier = "db-f1-micro"',
                    "  }",
                    "",
                    "  deletion_protection = false",
                    "",
                    "  depends_on = [google_project_service.apis]",
                    "",
                    _governance_tags_hcl("labels", gcp=True),
                    "}",
                ]
            )
        )
    if r.cloud_storage:
        blocks.append(
            "\n".join(
                [
                    'resource "google_storage_bucket" "data" {',
                    "  name     = local.gcs_bucket_name",
                    "  project  = var.project_id",
                    "  location = var.region",
                    "",
                    "  uniform_bucket_level_access = true",
                    "  # Ephemeral preview buckets must tear down without leftover objects.",
                    "  force_destroy               = true",
                    "",
                    "  depends_on = [google_project_service.apis]",
                    "",
                    _governance_tags_hcl("labels", gcp=True),
                    "}",
                ]
            )
        )
    if r.pubsub:
        blocks.append(
            "\n".join(
                [
                    'resource "google_pubsub_topic" "events" {',
                    '  name    = "${local.name_55}-events"',
                    "  project = var.project_id",
                    "",
                    "  depends_on = [google_project_service.apis]",
                    "",
                    _governance_tags_hcl("labels", gcp=True),
                    "}",
                ]
            )
        )
    if r.memorystore:
        if r.memorystore_engine == CacheEngine.MEMCACHED:
            blocks.append(
                "\n".join(
                    [
                        'resource "google_memcache_instance" "cache" {',
                        '  name           = "${local.name_55}-memcache"',
                        "  project        = var.project_id",
                        "  region         = var.region",
                        "  node_count     = 1",
                        "",
                        "  node_config {",
                        "    cpu_count      = 1",
                        "    memory_size_mb = 1024",
                        "  }",
                        "",
                        "  depends_on = [google_project_service.apis]",
                        "",
                        _governance_tags_hcl("labels", gcp=True),
                        "}",
                    ]
                )
            )
        else:
            blocks.append(
                "\n".join(
                    [
                        'resource "google_redis_instance" "cache" {',
                        '  name           = "${local.name_55}-redis"',
                        "  project        = var.project_id",
                        "  region         = var.region",
                        '  tier           = "BASIC"',
                        "  memory_size_gb = 1",
                        '  redis_version  = "REDIS_7_0"',
                        "",
                        "  depends_on = [google_project_service.apis]",
                        "",
                        _governance_tags_hcl("labels", gcp=True),
                        "}",
                    ]
                )
            )
    if r.bigquery:
        blocks.append(
            "\n".join(
                [
                    'resource "google_bigquery_dataset" "analytics" {',
                    '  dataset_id = replace(local.name_63, "-", "_")',
                    "  project    = var.project_id",
                    "  location   = var.region",
                    "",
                    "  depends_on = [google_project_service.apis]",
                    "",
                    _governance_tags_hcl("labels", gcp=True),
                    "}",
                ]
            )
        )

    if len(blocks) == 1:
        return ""
    return _join_blocks(blocks, "") if blocks else ""


def _extras_aws(r: AwsResources) -> str:
    blocks: list[str] = [_naming_locals_hcl().rstrip()]
    if r.s3:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_s3_bucket" "data" {',
                    "  bucket        = local.s3_bucket_name",
                    "  force_destroy = true",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                    "",
                    'resource "aws_s3_bucket_versioning" "data" {',
                    "  bucket = aws_s3_bucket.data.id",
                    "",
                    "  versioning_configuration {",
                    '    status = "Enabled"',
                    "  }",
                    "}",
                ]
            )
        )
    if r.rds:
        rds_engine = _aws_rds_engine(r.rds_engine)
        blocks.append(
            "\n".join(
                [
                    'resource "aws_db_instance" "primary" {',
                    '  identifier          = "${local.name_55}-db"',
                    f'  engine              = "{rds_engine}"',
                    '  instance_class      = "db.t3.micro"',
                    "  allocated_storage   = 20",
                    '  username            = "launchpad"',
                    '  password            = "change-me-in-prod"',
                    "  skip_final_snapshot = true",
                    "  deletion_protection = false",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.ecr:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_ecr_repository" "app" {',
                    "  name = local.name_63",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.elasticache:
        cache_engine = r.elasticache_engine.value
        blocks.append(
            "\n".join(
                [
                    'resource "aws_elasticache_cluster" "cache" {',
                    "  cluster_id      = local.name_40",
                    f'  engine          = "{cache_engine}"',
                    '  node_type       = "cache.t3.micro"',
                    "  num_cache_nodes = 1",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.lambda_fn:
        lambda_runtime = r.lambda_runtime.value
        lambda_handler = (
            "index.handler"
            if lambda_runtime.startswith("nodejs")
            else "index.handler"
            if lambda_runtime.startswith("python")
            else "bootstrap"
        )
        blocks.append(
            "\n".join(
                [
                    'resource "aws_iam_role" "lambda" {',
                    '  name = "${local.name_55}-lambda"',
                    "  assume_role_policy = jsonencode({",
                    '    Version = "2012-10-17"',
                    "    Statement = [{ Action = \"sts:AssumeRole\", Effect = \"Allow\", Principal = { Service = \"lambda.amazonaws.com\" } }]",
                    "  })",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                    "",
                    'resource "aws_lambda_function" "app" {',
                    "  function_name = local.name_63",
                    "  role          = aws_iam_role.lambda.arn",
                    f'  handler       = "{lambda_handler}"',
                    f'  runtime       = "{lambda_runtime}"',
                    '  filename      = "placeholder.zip"',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.dynamodb:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_dynamodb_table" "app" {',
                    "  name         = local.name_63",
                    '  billing_mode = "PAY_PER_REQUEST"',
                    '  hash_key     = "pk"',
                    "",
                    "  attribute {",
                    '    name = "pk"',
                    '    type = "S"',
                    "  }",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.sqs:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_sqs_queue" "events" {',
                    '  name = "${local.name_55}-events"',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.alb:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_lb" "app" {',
                    # ALB names max 32 chars.
                    "  name               = local.name_40",
                    "  internal           = false",
                    '  load_balancer_type = "application"',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if len(blocks) == 1:
        return ""
    return _join_blocks(blocks, "") if blocks else ""


def _extras_azure(r: AzureResources) -> str:
    blocks: list[str] = [_naming_locals_hcl().rstrip()]
    if r.acr:
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_container_registry" "acr" {',
                    '  name                = substr(replace(local.name_63, "-", ""), 0, 50)',
                    "  resource_group_name = var.resource_group",
                    "  location            = var.location",
                    '  sku                 = "Basic"',
                    "  admin_enabled       = false",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.storage_account:
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_storage_account" "data" {',
                    '  name                     = substr(replace(local.name_63, "-", ""), 0, 24)',
                    "  resource_group_name      = var.resource_group",
                    "  location                 = var.location",
                    '  account_tier             = "Standard"',
                    '  account_replication_type = "LRS"',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.cosmos_db:
        cosmos_kind = _azure_cosmos_kind(r.cosmos_api)
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_cosmosdb_account" "app" {',
                    "  name                = local.name_63",
                    "  resource_group_name = var.resource_group",
                    "  location            = var.location",
                    '  offer_type          = "Standard"',
                    f'  kind                = "{cosmos_kind}"',
                    "",
                    "  consistency_policy { consistency_level = \"Session\" }",
                    "  geo_location { location = var.location; failover_priority = 0 }",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.redis_cache:
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_redis_cache" "cache" {',
                    "  name                = local.name_63",
                    "  resource_group_name = var.resource_group",
                    "  location            = var.location",
                    "  capacity            = 0",
                    '  family              = "C"',
                    '  sku_name            = "Basic"',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.app_service:
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_service_plan" "app" {',
                    '  name                = "${local.name_55}-plan"',
                    "  resource_group_name = var.resource_group",
                    "  location            = var.location",
                    '  os_type             = "Linux"',
                    '  sku_name            = "B1"',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                    "",
                    'resource "azurerm_linux_web_app" "app" {',
                    "  name                = local.name_63",
                    "  resource_group_name = var.resource_group",
                    "  location            = var.location",
                    "  service_plan_id     = azurerm_service_plan.app.id",
                    "  site_config {}",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.log_analytics:
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_log_analytics_workspace" "logs" {',
                    '  name                = "${local.name_55}-logs"',
                    "  resource_group_name = var.resource_group",
                    "  location            = var.location",
                    '  sku                 = "PerGB2018"',
                    "  retention_in_days   = 30",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if len(blocks) == 1:
        return ""
    return _join_blocks(blocks, "") if blocks else ""


def _extras_cloudflare(r: CloudflareResources) -> str:
    blocks: list[str] = [_naming_locals_hcl().rstrip()]
    if r.workers:
        blocks.append(
            "\n".join(
                [
                    'resource "cloudflare_workers_script" "app" {',
                    "  account_id = var.cloudflare_account_id",
                    "  name       = local.name_63",
                    "  content    = <<-EOT",
                    "    export default {",
                    "      async fetch() {",
                    '        return new Response("ok");',
                    "      },",
                    "    };",
                    "  EOT",
                    "}",
                ]
            )
        )
    if r.r2:
        blocks.append(
            "\n".join(
                [
                    'resource "cloudflare_r2_bucket" "data" {',
                    "  account_id = var.cloudflare_account_id",
                    '  name       = "${local.name_55}-data"',
                    '  location   = "WNAM"',
                    "}",
                ]
            )
        )
    if r.dns_records:
        blocks.append(
            "\n".join(
                [
                    'data "cloudflare_zone" "primary" {',
                    "  account_id = var.cloudflare_account_id",
                    "  name       = var.cloudflare_zone_name",
                    "}",
                    "",
                    'resource "cloudflare_record" "environment" {',
                    "  zone_id = data.cloudflare_zone.primary.id",
                    "  name    = local.name_63",
                    '  type    = "CNAME"',
                    '  content = "${local.name_63}.workers.dev"',
                    "  proxied = true",
                    (
                        '  comment = "EnvironmentId=${var.environment_id} Owner=launchpad '
                        'CreatedBy=launchpad-control-plane"'
                    ),
                    "}",
                ]
            )
        )
    if r.pages:
        blocks.append(
            "\n".join(
                [
                    'resource "cloudflare_pages_project" "app" {',
                    "  account_id        = var.cloudflare_account_id",
                    "  name              = local.name_63",
                    '  production_branch = "main"',
                    "}",
                ]
            )
        )
    if r.kv:
        blocks.append(
            "\n".join(
                [
                    'resource "cloudflare_workers_kv_namespace" "store" {',
                    "  account_id = var.cloudflare_account_id",
                    "  title      = local.name_63",
                    "}",
                ]
            )
        )
    if r.d1:
        blocks.append(
            "\n".join(
                [
                    'resource "cloudflare_d1_database" "db" {',
                    "  account_id = var.cloudflare_account_id",
                    "  name       = local.name_63",
                    "}",
                ]
            )
        )
    if r.tunnels:
        blocks.append(
            "\n".join(
                [
                    'resource "cloudflare_zero_trust_tunnel_cloudflared" "app" {',
                    "  account_id = var.cloudflare_account_id",
                    "  name       = local.name_63",
                    '  secret     = base64encode("change-me")',
                    "}",
                ]
            )
        )
    if r.queues:
        blocks.append(
            "\n".join(
                [
                    'resource "cloudflare_queue" "events" {',
                    "  account_id = var.cloudflare_account_id",
                    '  name       = "${local.name_55}-events"',
                    "}",
                ]
            )
        )
    if len(blocks) == 1:
        return "# No Cloudflare resources selected.\n"
    return _join_blocks(blocks, "# No Cloudflare resources selected.")


def _root_outputs(cloud: CloudConfig) -> str:
    lines: list[str] = []

    if isinstance(cloud, GcpCloudConfig):
        r = cloud.resources
        if r.vpc:
            lines += [
                'output "vpc_id" {',
                "  value = module.vpc.vpc_id",
                "}",
                "",
            ]
        if r.subnets:
            lines += [
                'output "subnet_id" {',
                "  value = module.vpc.subnet_id",
                "}",
                "",
            ]
        if r.gke:
            lines += [
                'output "gke_cluster_endpoint" {',
                "  value     = module.cluster.gke_cluster_endpoint",
                "  sensitive = true",
                "}",
                "",
            ]
        if r.cloud_run:
            lines += [
                'output "cloud_run_url" {',
                "  value = module.cluster.cloud_run_url",
                "}",
                "",
                'output "preview_url" {',
                "  value = module.cluster.preview_url",
                "}",
                "",
            ]
        if getattr(r, "compute_instance", False):
            lines += [
                'output "compute_instance_id" {',
                "  value = module.cluster.compute_instance_id",
                "}",
                "",
                'output "public_ip" {',
                "  value = module.cluster.public_ip",
                "}",
                "",
                'output "preview_url" {',
                "  value = module.cluster.preview_url",
                "}",
                "",
            ]
        if r.artifact_registry:
            lines += [
                'output "artifact_registry_repository" {',
                "  value = google_artifact_registry_repository.ar.name",
                "}",
                "",
            ]
        if r.cloud_functions:
            lines += [
                'output "cloud_function_name" {',
                "  value = google_cloudfunctions2_function.fn.name",
                "}",
                "",
            ]
        if r.cloud_sql:
            if r.cloud_sql_engine == SqlDatabaseEngine.POSTGRES:
                lines += [
                    'output "managed_postgres_host" {',
                    "  value = google_sql_database_instance.primary.private_ip_address",
                    "}",
                    "",
                    'output "managed_postgres_connection_url" {',
                    '  value     = format("postgresql://launchpad:change-me@%s:5432/app", google_sql_database_instance.primary.private_ip_address)',
                    "  sensitive = true",
                    "}",
                    "",
                ]
            else:
                lines += [
                    'output "managed_mysql_host" {',
                    "  value = google_sql_database_instance.primary.private_ip_address",
                    "}",
                    "",
                    'output "managed_mysql_connection_url" {',
                    '  value     = format("mysql://launchpad:change-me@%s:3306/app", google_sql_database_instance.primary.private_ip_address)',
                    "  sensitive = true",
                    "}",
                    "",
                ]
        if r.memorystore:
            if r.memorystore_engine == CacheEngine.MEMCACHED:
                lines += [
                    'output "managed_memcached_host" {',
                    "  value = google_memcache_instance.cache.discovery_endpoint",
                    "}",
                    "",
                ]
            else:
                lines += [
                    'output "managed_redis_host" {',
                    "  value = google_redis_instance.cache.host",
                    "}",
                    "",
                    'output "managed_redis_connection_url" {',
                    '  value     = format("redis://%s:6379/0", google_redis_instance.cache.host)',
                    "  sensitive = true",
                    "}",
                    "",
                ]
        if r.secret_backend == SecretBackend.SECRET_MANAGER:
            lines += [
                'output "secret_id" {',
                "  value = module.secrets.secret_id",
                "}",
                "",
            ]
    elif isinstance(cloud, AwsCloudConfig):
        r = cloud.resources
        if r.vpc:
            lines += ['output "vpc_id" {', "  value = module.vpc.vpc_id", "}", ""]
        if r.s3:
            lines += [
                'output "s3_bucket_name" {',
                "  value = aws_s3_bucket.data.bucket",
                "}",
                "",
            ]
        if r.eks:
            lines += [
                'output "eks_cluster_endpoint" {',
                "  value     = module.cluster.eks_cluster_endpoint",
                "  sensitive = true",
                "}",
                "",
            ]
        if r.ec2:
            lines += [
                'output "ec2_instance_id" {',
                "  value = module.cluster.ec2_instance_id",
                "}",
                "",
                'output "public_ip" {',
                "  value = module.cluster.public_ip",
                "}",
                "",
                'output "preview_url" {',
                "  value = module.cluster.preview_url",
                "}",
                "",
            ]
        if r.app_runner:
            lines += [
                'output "app_runner_service_url" {',
                "  value = module.cluster.app_runner_service_url",
                "}",
                "",
                'output "preview_url" {',
                "  value = module.cluster.preview_url",
                "}",
                "",
            ]
        if r.secrets_manager:
            lines += [
                'output "secrets_manager_arn" {',
                "  value = module.secrets.secrets_manager_arn",
                "}",
                "",
            ]
        if r.rds:
            if r.rds_engine == SqlDatabaseEngine.POSTGRES:
                lines += [
                    'output "managed_postgres_host" {',
                    "  value = aws_db_instance.primary.address",
                    "}",
                    "",
                    'output "managed_postgres_connection_url" {',
                    '  value     = format("postgresql://launchpad:change-me@%s:5432/app", aws_db_instance.primary.address)',
                    "  sensitive = true",
                    "}",
                    "",
                ]
            elif r.rds_engine == SqlDatabaseEngine.MARIADB:
                lines += [
                    'output "managed_mariadb_host" {',
                    "  value = aws_db_instance.primary.address",
                    "}",
                    "",
                    'output "managed_mariadb_connection_url" {',
                    '  value     = format("mysql://launchpad:change-me@%s:3306/app", aws_db_instance.primary.address)',
                    "  sensitive = true",
                    "}",
                    "",
                ]
            else:
                lines += [
                    'output "managed_mysql_host" {',
                    "  value = aws_db_instance.primary.address",
                    "}",
                    "",
                    'output "managed_mysql_connection_url" {',
                    '  value     = format("mysql://launchpad:change-me@%s:3306/app", aws_db_instance.primary.address)',
                    "  sensitive = true",
                    "}",
                    "",
                ]
        if r.elasticache:
            if r.elasticache_engine == CacheEngine.MEMCACHED:
                lines += [
                    'output "managed_memcached_host" {',
                    "  value = aws_elasticache_cluster.cache.cache_nodes[0].address",
                    "}",
                    "",
                ]
            else:
                lines += [
                    'output "managed_redis_host" {',
                    "  value = aws_elasticache_cluster.cache.cache_nodes[0].address",
                    "}",
                    "",
                    'output "managed_redis_connection_url" {',
                    '  value     = format("redis://%s:6379/0", aws_elasticache_cluster.cache.cache_nodes[0].address)',
                    "  sensitive = true",
                    "}",
                    "",
                ]
    elif isinstance(cloud, AzureCloudConfig):
        r = cloud.resources
        lines += [
            'output "resource_group_name" {',
            "  value = azurerm_resource_group.main.name",
            "}",
            "",
        ]
        if r.vnet:
            lines += [
                'output "vnet_id" {',
                "  value = module.vpc.vnet_id",
                "}",
                "",
            ]
        if r.aks:
            lines += [
                'output "aks_cluster_name" {',
                "  value = module.cluster.aks_cluster_name",
                "}",
                "",
            ]
        if r.key_vault:
            lines += [
                'output "key_vault_uri" {',
                "  value = module.secrets.key_vault_uri",
                "}",
                "",
            ]
        if r.cosmos_db:
            if r.cosmos_api == CosmosApiKind.MONGODB:
                lines += [
                    'output "managed_mongodb_host" {',
                    "  value = azurerm_cosmosdb_account.app.name",
                    "}",
                    "",
                    'output "managed_mongodb_connection_url" {',
                    '  value     = format("mongodb://launchpad:change-me@%s:10255/app", azurerm_cosmosdb_account.app.name)',
                    "  sensitive = true",
                    "}",
                    "",
                ]
            else:
                lines += [
                    'output "managed_cosmos_sql_host" {',
                    "  value = azurerm_cosmosdb_account.app.name",
                    "}",
                    "",
                    'output "managed_cosmos_sql_endpoint" {',
                    "  value     = azurerm_cosmosdb_account.app.endpoint",
                    "  sensitive = true",
                    "}",
                    "",
                ]
        if r.redis_cache:
            lines += [
                'output "managed_redis_host" {',
                "  value = azurerm_redis_cache.cache.hostname",
                "}",
                "",
                'output "managed_redis_connection_url" {',
                '  value     = format("redis://%s:6380/0", azurerm_redis_cache.cache.hostname)',
                "  sensitive = true",
                "}",
                "",
            ]
    else:
        r = cloud.resources
        if r.r2:
            lines += [
                'output "r2_bucket_name" {',
                "  value = cloudflare_r2_bucket.data.name",
                "}",
                "",
            ]
        if r.workers:
            lines += [
                'output "worker_script_name" {',
                "  value = cloudflare_workers_script.app.name",
                "}",
                "",
            ]

    if not lines:
        lines = ["# No outputs declared for the selected resource set."]
    return "\n".join(lines).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #


def write_terraform_bundle(workspace_dir: Path, name: str, cloud: CloudConfig) -> list[str]:
    """Writes a modular Terraform tree under ``infra/terraform/`` and returns relative paths."""
    modules = {
        "vpc": (
            _vpc_module_main(cloud),
            _vpc_module_variables(cloud),
            _vpc_module_outputs(cloud),
        ),
        "cluster": (
            _cluster_module_main(cloud),
            _cluster_module_variables(cloud),
            _cluster_module_outputs(cloud),
        ),
        "secrets": (
            _secrets_module_main(cloud),
            _secrets_module_variables(cloud),
            _secrets_module_outputs(cloud),
        ),
    }

    written: list[str] = []

    def _write(relative: Path, content: str) -> None:
        path = workspace_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(relative).replace("\\", "/"))

    _write(TF_ROOT / "providers.tf", _providers_tf(cloud))
    _write(TF_ROOT / "variables.tf", _root_variables(cloud))
    _write(TF_ROOT / "terraform.tfvars", _root_tfvars(name))
    apis = _apis_tf(cloud)
    if apis is not None:
        _write(TF_ROOT / "apis.tf", apis)
    _write(TF_ROOT / "main.tf", _root_main(name, cloud))
    _write(TF_ROOT / "outputs.tf", _root_outputs(cloud))
    _write(
        TF_ROOT / ".gitignore",
        "*.tfstate\n*.tfstate.*\n.terraform/\n.terraform.lock.hcl\ncrash.log\n",
    )

    for module_name, (main, variables, outputs) in modules.items():
        module_dir = TF_ROOT / "modules" / module_name
        _write(module_dir / "main.tf", main)
        _write(module_dir / "variables.tf", variables)
        _write(module_dir / "outputs.tf", outputs)

    return written

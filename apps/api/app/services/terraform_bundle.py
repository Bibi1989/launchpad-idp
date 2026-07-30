"""Modular Terraform bundle writer under ``infra/terraform/``.

Renders a root stack that instantiates child modules:

- ``modules/vpc`` — network definitions
- ``modules/cluster`` — GKE / EKS / AKS / Cloud Run / compute
- ``modules/secrets`` — cloud secret manager / Key Vault / native K8s secrets
"""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas.cloud import (
    AwsCloudConfig,
    AwsResources,
    AzureCloudConfig,
    AzureResources,
    CloudConfig,
    CloudflareResources,
    GcpCloudConfig,
    GcpResources,
    SecretBackend,
)

TF_ROOT = Path("infra") / "terraform"

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
) -> str:
    inner = indent + "  "
    lines = [indent + key + " = {"]
    if extra:
        for extra_key, expression in extra.items():
            lines.append(inner + extra_key + " = " + expression)
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
    blocks: list[str] = []
    if r.vpc:
        blocks.append(
            "\n".join(
                [
                    'resource "google_compute_network" "vpc" {',
                    '  name                    = "lp-${var.environment_id}-vpc"',
                    "  project                 = var.project_id",
                    "  auto_create_subnetworks = false",
                    "",
                    _governance_tags_hcl("labels"),
                    "}",
                ]
            )
        )
    if r.subnets:
        network_ref = "google_compute_network.vpc.id" if r.vpc else '"default"'
        blocks.append(
            "\n".join(
                [
                    'resource "google_compute_subnetwork" "subnet" {',
                    '  name          = "lp-${var.environment_id}-subnet"',
                    "  project       = var.project_id",
                    "  region        = var.region",
                    "  network       = " + network_ref,
                    '  ip_cidr_range = "10.10.0.0/20"',
                    "",
                    _governance_tags_hcl("labels"),
                    "}",
                ]
            )
        )
    return _join_blocks(blocks, "# No VPC resources selected.")


def _vpc_aws(r: AwsResources) -> str:
    blocks: list[str] = []
    if r.vpc:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_vpc" "main" {',
                    '  cidr_block           = "10.20.0.0/16"',
                    "  enable_dns_hostnames = true",
                    "  enable_dns_support   = true",
                    "",
                    _governance_tags_hcl(
                        "tags", extra={"Name": '"lp-${var.environment_id}-vpc"'}
                    ),
                    "}",
                ]
            )
        )
    if r.subnets and r.vpc:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_subnet" "public" {',
                    "  vpc_id                  = aws_vpc.main.id",
                    '  cidr_block              = "10.20.1.0/24"',
                    '  availability_zone       = "${var.region}a"',
                    "  map_public_ip_on_launch = true",
                    "",
                    _governance_tags_hcl(
                        "tags", extra={"Name": '"lp-${var.environment_id}-public"'}
                    ),
                    "}",
                    "",
                    'resource "aws_subnet" "private" {',
                    "  vpc_id            = aws_vpc.main.id",
                    '  cidr_block        = "10.20.2.0/24"',
                    '  availability_zone = "${var.region}a"',
                    "",
                    _governance_tags_hcl(
                        "tags", extra={"Name": '"lp-${var.environment_id}-private"'}
                    ),
                    "}",
                ]
            )
        )
    return _join_blocks(blocks, "# No VPC resources selected.")


def _vpc_azure(r: AzureResources) -> str:
    blocks: list[str] = []
    if r.vnet:
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_virtual_network" "vnet" {',
                    '  name                = "lp-${var.environment_id}-vnet"',
                    "  resource_group_name = var.resource_group_name",
                    "  location            = var.location",
                    '  address_space       = ["10.30.0.0/16"]',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.subnets and r.vnet:
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_subnet" "primary" {',
                    '  name                 = "lp-${var.environment_id}-subnet"',
                    "  resource_group_name  = var.resource_group_name",
                    "  virtual_network_name = azurerm_virtual_network.vnet.name",
                    '  address_prefixes     = ["10.30.1.0/24"]',
                    "}",
                ]
            )
        )
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
            lines += [
                "",
                'output "subnet_id" {',
                "  value = google_compute_subnetwork.subnet.id",
                "}",
            ]
        else:
            lines += ["", 'output "subnet_id" {', "  value = null", "}"]
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
            lines += [
                "",
                'output "subnet_id" {',
                "  value = azurerm_subnet.primary.id",
                "}",
            ]
        else:
            lines += ["", 'output "subnet_id" {', "  value = null", "}"]
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


def _cluster_module_main(cloud: CloudConfig) -> str:
    if isinstance(cloud, GcpCloudConfig):
        return _cluster_gcp(cloud.resources)
    if isinstance(cloud, AwsCloudConfig):
        return _cluster_aws(cloud.resources)
    if isinstance(cloud, AzureCloudConfig):
        return _cluster_azure(cloud.resources)
    return "# Cloudflare has no cluster module resources.\n"


def _cluster_gcp(r: GcpResources) -> str:
    blocks: list[str] = []
    if r.gke:
        blocks.append(
            "\n".join(
                [
                    'resource "google_container_cluster" "gke" {',
                    '  name                     = "lp-${var.environment_id}-gke"',
                    "  project                  = var.project_id",
                    "  location                 = var.region",
                    "  remove_default_node_pool = true",
                    "  initial_node_count       = 1",
                    "  network                  = var.network",
                    "",
                    _governance_tags_hcl("resource_labels"),
                    "}",
                    "",
                    'resource "google_container_node_pool" "gke_primary" {',
                    '  name       = "lp-${var.environment_id}-primary"',
                    "  project    = var.project_id",
                    "  location   = var.region",
                    "  cluster    = google_container_cluster.gke.name",
                    "  node_count = 2",
                    "",
                    "  node_config {",
                    '    machine_type = "e2-standard-4"',
                    "",
                    _governance_tags_hcl("labels", "    "),
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
                    '  name     = "lp-${var.environment_id}-run"',
                    "  project  = var.project_id",
                    "  location = var.region",
                    "",
                    "  template {",
                    "    containers {",
                    '      image = "us-docker.pkg.dev/cloudrun/container/hello"',
                    "    }",
                    "  }",
                    "",
                    _governance_tags_hcl("labels"),
                    "}",
                ]
            )
        )
    return _join_blocks(blocks, "# No cluster resources selected.")


def _cluster_aws(r: AwsResources) -> str:
    blocks: list[str] = []
    if r.ec2:
        blocks.append(
            "\n".join(
                [
                    'data "aws_ami" "amazon_linux" {',
                    "  most_recent = true",
                    '  owners      = ["amazon"]',
                    "",
                    "  filter {",
                    '    name   = "name"',
                    '    values = ["al2023-ami-*-x86_64"]',
                    "  }",
                    "}",
                    "",
                    'resource "aws_instance" "app" {',
                    "  ami           = data.aws_ami.amazon_linux.id",
                    '  instance_type = "t3.medium"',
                    "  subnet_id     = var.public_subnet_id",
                    "",
                    _governance_tags_hcl(
                        "tags", extra={"Name": '"lp-${var.environment_id}-ec2"'}
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
                    '  name = "lp-${var.environment_id}-eks-role"',
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
                    '  name     = "lp-${var.environment_id}-eks"',
                    "  role_arn = aws_iam_role.eks_cluster.arn",
                    "",
                    "  vpc_config {",
                    "    subnet_ids = var.subnet_ids",
                    "  }",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    return _join_blocks(blocks, "# No cluster resources selected.")


def _cluster_azure(r: AzureResources) -> str:
    blocks: list[str] = []
    if r.aks:
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_kubernetes_cluster" "aks" {',
                    '  name                = "lp-${var.environment_id}-aks"',
                    "  resource_group_name = var.resource_group_name",
                    "  location            = var.location",
                    '  dns_prefix          = "lp-${var.environment_id}"',
                    "",
                    "  default_node_pool {",
                    '    name       = "default"',
                    "    node_count = 2",
                    '    vm_size    = "Standard_D2_v2"',
                    "  }",
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
                    '  name                = "lp-${var.environment_id}-cae"',
                    "  resource_group_name = var.resource_group_name",
                    "  location            = var.location",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                    "",
                    'resource "azurerm_container_app" "app" {',
                    '  name                         = "lp-${var.environment_id}-app"',
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
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
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
        if r.secret_backend == SecretBackend.SECRET_MANAGER:
            return (
                "\n".join(
                    [
                        'resource "google_secret_manager_secret" "app_secrets" {',
                        "  project   = var.project_id",
                        '  secret_id = "lp-${var.environment_id}-secrets"',
                        "",
                        "  replication {",
                        "    auto {}",
                        "  }",
                        "",
                        _governance_tags_hcl("labels"),
                        "}",
                    ]
                )
                + "\n"
            )
        return (
            "\n".join(
                [
                    'resource "kubernetes_secret" "app_secrets" {',
                    "  metadata {",
                    '    name      = "lp-${var.environment_id}-secrets"',
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
            "\n".join(
                [
                    'resource "aws_secretsmanager_secret" "app_secrets" {',
                    '  name = "lp-${var.environment_id}-secrets"',
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
            "\n".join(
                [
                    'data "azurerm_client_config" "current" {}',
                    "",
                    'resource "azurerm_key_vault" "main" {',
                    '  name                = "lp-${var.environment_id}-kv"',
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
        "# Generated by Launchpad IaC Generator — do not hand-edit; regenerate via the wizard.",
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
            "  project_id = var.project_id",
            "  region     = var.region",
            "  network    = module.vpc.network_id",
        ]
    elif isinstance(cloud, AwsCloudConfig):
        lines += [
            "  region           = var.region",
            "  subnet_ids       = module.vpc.subnet_ids",
            "  public_subnet_id = module.vpc.public_subnet_id",
        ]
    elif isinstance(cloud, AzureCloudConfig):
        lines += [
            "  location            = azurerm_resource_group.main.location",
            "  resource_group_name = azurerm_resource_group.main.name",
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
    blocks: list[str] = []
    if r.artifact_registry:
        blocks.append(
            "\n".join(
                [
                    'resource "google_artifact_registry_repository" "ar" {',
                    "  project       = var.project_id",
                    "  location      = var.region",
                    '  repository_id = "lp-${var.environment_id}"',
                    '  format        = "DOCKER"',
                    "",
                    _governance_tags_hcl("labels"),
                    "}",
                ]
            )
        )
    if r.cloud_functions:
        blocks.append(
            "\n".join(
                [
                    'resource "google_cloudfunctions2_function" "fn" {',
                    '  name     = "lp-${var.environment_id}-fn"',
                    "  project  = var.project_id",
                    "  location = var.region",
                    "",
                    "  build_config {",
                    '    runtime     = "nodejs20"',
                    '    entry_point = "handler"',
                    "",
                    "    source {",
                    "      storage_source {",
                    '        bucket = "lp-${var.environment_id}-fn-source"',
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
                    _governance_tags_hcl("labels"),
                    "}",
                ]
            )
        )

    if r.cloud_sql:
        blocks.append(
            "\n".join(
                [
                    'resource "google_sql_database_instance" "primary" {',
                    '  name             = "lp-${var.environment_id}-sql"',
                    "  project          = var.project_id",
                    "  region           = var.region",
                    '  database_version = "POSTGRES_15"',
                    "",
                    "  settings {",
                    '    tier = "db-f1-micro"',
                    "  }",
                    "",
                    "  deletion_protection = false",
                    _governance_tags_hcl("labels"),
                    "}",
                ]
            )
        )
    if r.cloud_storage:
        blocks.append(
            "\n".join(
                [
                    'resource "google_storage_bucket" "data" {',
                    '  name     = "lp-${var.environment_id}-data"',
                    "  project  = var.project_id",
                    "  location = var.region",
                    "",
                    "  uniform_bucket_level_access = true",
                    "",
                    _governance_tags_hcl("labels"),
                    "}",
                ]
            )
        )
    if r.pubsub:
        blocks.append(
            "\n".join(
                [
                    'resource "google_pubsub_topic" "events" {',
                    '  name    = "lp-${var.environment_id}-events"',
                    "  project = var.project_id",
                    "",
                    _governance_tags_hcl("labels"),
                    "}",
                ]
            )
        )
    if r.memorystore:
        blocks.append(
            "\n".join(
                [
                    'resource "google_redis_instance" "cache" {',
                    '  name           = "lp-${var.environment_id}-redis"',
                    "  project        = var.project_id",
                    "  region         = var.region",
                    '  tier           = "BASIC"',
                    "  memory_size_gb = 1",
                    '  redis_version  = "REDIS_7_0"',
                    "",
                    _governance_tags_hcl("labels"),
                    "}",
                ]
            )
        )
    if r.bigquery:
        blocks.append(
            "\n".join(
                [
                    'resource "google_bigquery_dataset" "analytics" {',
                    '  dataset_id = "lp_${replace(var.environment_id, "-", "_")}"',
                    "  project    = var.project_id",
                    "  location   = var.region",
                    "",
                    _governance_tags_hcl("labels"),
                    "}",
                ]
            )
        )

    return _join_blocks(blocks, "") if blocks else ""


def _extras_aws(r: AwsResources) -> str:
    blocks: list[str] = []
    if r.s3:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_s3_bucket" "data" {',
                    '  bucket = "lp-${var.environment_id}-data"',
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
        blocks.append(
            "\n".join(
                [
                    'resource "aws_db_instance" "primary" {',
                    '  identifier          = "lp-${var.environment_id}-db"',
                    '  engine              = "postgres"',
                    '  instance_class      = "db.t3.micro"',
                    "  allocated_storage   = 20",
                    '  username            = "launchpad"',
                    '  password            = "change-me-in-prod"',
                    "  skip_final_snapshot = true",
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
                    '  name = "lp-${var.environment_id}"',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.elasticache:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_elasticache_cluster" "redis" {',
                    '  cluster_id      = "lp-${var.environment_id}"',
                    '  engine          = "redis"',
                    '  node_type       = "cache.t3.micro"',
                    "  num_cache_nodes = 1",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    if r.lambda_fn:
        blocks.append(
            "\n".join(
                [
                    'resource "aws_iam_role" "lambda" {',
                    '  name = "lp-${var.environment_id}-lambda"',
                    "  assume_role_policy = jsonencode({",
                    '    Version = "2012-10-17"',
                    "    Statement = [{ Action = \"sts:AssumeRole\", Effect = \"Allow\", Principal = { Service = \"lambda.amazonaws.com\" } }]",
                    "  })",
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                    "",
                    'resource "aws_lambda_function" "app" {',
                    '  function_name = "lp-${var.environment_id}"',
                    "  role          = aws_iam_role.lambda.arn",
                    '  handler       = "index.handler"',
                    '  runtime       = "nodejs20.x"',
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
                    '  name         = "lp-${var.environment_id}"',
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
                    '  name = "lp-${var.environment_id}-events"',
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
                    '  name               = "lp-${var.environment_id}"',
                    "  internal           = false",
                    '  load_balancer_type = "application"',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                ]
            )
        )
    return _join_blocks(blocks, "") if blocks else ""


def _extras_azure(r: AzureResources) -> str:
    blocks: list[str] = []
    if r.acr:
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_container_registry" "acr" {',
                    '  name                = replace("lp${var.environment_id}", "-", "")',
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
                    '  name                     = substr(replace("lp${var.environment_id}", "-", ""), 0, 24)',
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
        blocks.append(
            "\n".join(
                [
                    'resource "azurerm_cosmosdb_account" "app" {',
                    '  name                = "lp-${var.environment_id}"',
                    "  resource_group_name = var.resource_group",
                    "  location            = var.location",
                    '  offer_type          = "Standard"',
                    '  kind                = "GlobalDocumentDB"',
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
                    '  name                = "lp-${var.environment_id}"',
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
                    '  name                = "lp-${var.environment_id}-plan"',
                    "  resource_group_name = var.resource_group",
                    "  location            = var.location",
                    '  os_type             = "Linux"',
                    '  sku_name            = "B1"',
                    "",
                    _governance_tags_hcl("tags"),
                    "}",
                    "",
                    'resource "azurerm_linux_web_app" "app" {',
                    '  name                = "lp-${var.environment_id}"',
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
                    '  name                = "lp-${var.environment_id}-logs"',
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
    return _join_blocks(blocks, "") if blocks else ""


def _extras_cloudflare(r: CloudflareResources) -> str:
    blocks: list[str] = []
    if r.workers:
        blocks.append(
            "\n".join(
                [
                    'resource "cloudflare_workers_script" "app" {',
                    "  account_id = var.cloudflare_account_id",
                    '  name       = "lp-${var.environment_id}"',
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
                    '  name       = "lp-${var.environment_id}-data"',
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
                    '  name    = "lp-${var.environment_id}"',
                    '  type    = "CNAME"',
                    '  content = "lp-${var.environment_id}.workers.dev"',
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
                    '  name              = "lp-${var.environment_id}"',
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
                    '  title      = "lp-${var.environment_id}"',
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
                    '  name       = "lp-${var.environment_id}"',
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
                    '  name       = "lp-${var.environment_id}"',
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
                    '  name       = "lp-${var.environment_id}-events"',
                    "}",
                ]
            )
        )
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
        if r.secrets_manager:
            lines += [
                'output "secrets_manager_arn" {',
                "  value = module.secrets.secrets_manager_arn",
                "}",
                "",
            ]
        if r.rds:
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
            lines += [
                'output "managed_redis_host" {',
                "  value = aws_elasticache_cluster.redis.cache_nodes[0].address",
                "}",
                "",
                'output "managed_redis_connection_url" {',
                '  value     = format("redis://%s:6379/0", aws_elasticache_cluster.redis.cache_nodes[0].address)',
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

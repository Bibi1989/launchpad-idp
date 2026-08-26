"""Scaffold provisioning artifacts for the selected tool into a workspace.

Given a provisioning tool (scripting / terraform / opentofu / pulumi / cloud-native) and a
:class:`ProvisionSpec`, produce the concrete files to drop into the workspace directory.
"Scripting" is the default and uses the built-in cloud-init generator; the IaC tools emit
starter bundles that boot the app container on the chosen cloud.

Every file boots the same container the rest of Launchpad builds, so switching tools does
not change what runs, only how it is provisioned.
"""

from __future__ import annotations

from pydantic import BaseModel

from .base import ProvisionSpec
from .cloud_init import render_cloud_init

# Clouds that expose a real Linux VM these scaffolds can target.
_VM_CLOUDS = {"aws", "gcp", "azure", "hetzner", "digitalocean",
              "aws-legacy", "gcp-legacy", "azure-legacy"}

_TERRAFORM_PROVIDER = {
    "aws": 'provider "aws" {\n  region = var.region\n}',
    "gcp": 'provider "google" {\n  project = var.project_id\n  region  = var.region\n}',
    "azure": 'provider "azurerm" {\n  features {}\n}',
    "hetzner": 'provider "hcloud" {\n  token = var.hcloud_token\n}',
    "digitalocean": 'provider "digitalocean" {\n  token = var.do_token\n}',
}

_TERRAFORM_REQUIRED = {
    "aws": '    aws = {\n      source  = "hashicorp/aws"\n      version = "~> 5.0"\n    }',
    "gcp": '    google = {\n      source  = "hashicorp/google"\n      version = "~> 5.0"\n    }',
    "azure": '    azurerm = {\n      source  = "hashicorp/azurerm"\n      version = "~> 3.0"\n    }',
    "hetzner": '    hcloud = {\n      source  = "hetznercloud/hcloud"\n      version = "~> 1.45"\n    }',
    "digitalocean": '    digitalocean = {\n      source  = "digitalocean/digitalocean"\n      version = "~> 2.0"\n    }',
}

# A minimal compute instance per cloud that runs cloud-init user-data.
_TERRAFORM_INSTANCE = {
    "aws": (
        'resource "aws_instance" "app" {\n'
        '  ami           = var.ami_id\n'
        '  instance_type = var.instance_type\n'
        '  user_data     = file("${path.module}/cloud-init.yaml")\n'
        '  tags = { Name = var.name }\n'
        '}\n\n'
        'output "public_ip" {\n  value = aws_instance.app.public_ip\n}'
    ),
    "gcp": (
        'resource "google_compute_instance" "app" {\n'
        '  name         = var.name\n'
        '  machine_type = var.machine_type\n'
        '  zone         = "${var.region}-a"\n'
        '  boot_disk { initialize_params { image = "ubuntu-os-cloud/ubuntu-2204-lts" } }\n'
        '  network_interface { network = "default"\n    access_config {} }\n'
        '  metadata = { user-data = file("${path.module}/cloud-init.yaml") }\n'
        '}\n\n'
        'output "public_ip" {\n'
        '  value = google_compute_instance.app.network_interface[0].access_config[0].nat_ip\n}'
    ),
    "hetzner": (
        'resource "hcloud_server" "app" {\n'
        '  name        = var.name\n'
        '  server_type = var.server_type\n'
        '  image       = "docker-ce"\n'
        '  location    = var.region\n'
        '  user_data   = file("${path.module}/cloud-init.yaml")\n'
        '}\n\n'
        'output "public_ip" {\n  value = hcloud_server.app.ipv4_address\n}'
    ),
    "digitalocean": (
        'resource "digitalocean_droplet" "app" {\n'
        '  name      = var.name\n'
        '  size      = var.droplet_size\n'
        '  image     = "docker-20-04"\n'
        '  region    = var.region\n'
        '  user_data = file("${path.module}/cloud-init.yaml")\n'
        '}\n\n'
        'output "public_ip" {\n  value = digitalocean_droplet.app.ipv4_address\n}'
    ),
    "azure": (
        '# Azure Linux VM omitted for brevity: use azurerm_linux_virtual_machine with\n'
        '# custom_data = filebase64("${path.module}/cloud-init.yaml"). See README.'
    ),
}


class ScaffoldFile(BaseModel):
    path: str
    content: str


def _base_id(provider_id: str) -> str:
    return provider_id.removesuffix("-legacy")


def _cloud_init(spec: ProvisionSpec) -> str:
    return render_cloud_init(
        image=spec.image or "REPLACE_WITH_IMAGE:latest",
        app_port=spec.app_port,
        env_vars=spec.env_vars,
        ssh_authorized_keys=[spec.ssh_public_key] if spec.ssh_public_key else (),
    )


def _scripting_files(provider_id: str, spec: ProvisionSpec) -> list[ScaffoldFile]:
    readme = (
        "# Provisioning: scripting (cloud-init)\n\n"
        "`cloud-init.yaml` is the default provisioning method. Pass it as the instance\n"
        "user-data when creating a VM on your cloud; it installs Docker, injects env vars,\n"
        "and runs the app container as a systemd unit.\n"
    )
    return [
        ScaffoldFile(path="provision/cloud-init.yaml", content=_cloud_init(spec)),
        ScaffoldFile(path="provision/README.md", content=readme),
    ]


def _terraform_files(provider_id: str, spec: ProvisionSpec, *, dir_name: str) -> list[ScaffoldFile]:
    cloud = _base_id(provider_id)
    if cloud not in _TERRAFORM_PROVIDER:
        return [ScaffoldFile(
            path=f"infra/{dir_name}/README.md",
            content=f"# {dir_name}\n\nTerraform starter is not available for '{cloud}'. "
                    "Use the scripting (cloud-init) tool instead.\n",
        )]
    main_tf = (
        "terraform {\n  required_providers {\n"
        f"{_TERRAFORM_REQUIRED[cloud]}\n  }}\n}}\n\n"
        f"{_TERRAFORM_PROVIDER[cloud]}\n\n"
        f"{_TERRAFORM_INSTANCE[cloud]}\n"
    )
    variables_tf = (
        'variable "name" {\n  type    = string\n'
        f'  default = "{(spec.name or "launchpad-app")}"\n}}\n\n'
        'variable "region" {\n  type    = string\n'
        f'  default = "{spec.region or ""}"\n}}\n\n'
        'variable "project_id" {\n  type    = string\n'
        '  default = ""\n}}\n\n'
        'variable "app_listen_port" {\n  type    = number\n'
        '  default = 8080\n}}\n\n'
        'variable "app_image" {\n  type    = string\n'
        '  default = ""\n}}\n\n'
        'variable "ssh_public_key" {\n  type    = string\n'
        '  default = ""\n}}\n'
    )
    readme = (
        f"# Provisioning: {dir_name}\n\n"
        f"Starter {dir_name} for **{cloud}** that boots the app container on a VM via\n"
        "`cloud-init.yaml`. Fill in credentials/variables, then:\n\n"
        f"```bash\ncd infra/{dir_name}\n{dir_name} init\n{dir_name} apply\n```\n"
    )
    return [
        ScaffoldFile(path=f"infra/{dir_name}/main.tf", content=main_tf),
        ScaffoldFile(path=f"infra/{dir_name}/variables.tf", content=variables_tf),
        ScaffoldFile(path=f"infra/{dir_name}/cloud-init.yaml", content=_cloud_init(spec)),
        ScaffoldFile(path=f"infra/{dir_name}/README.md", content=readme),
    ]


def _pulumi_files(provider_id: str, spec: ProvisionSpec) -> list[ScaffoldFile]:
    cloud = _base_id(provider_id)
    pkg_map = {"aws": "@pulumi/aws", "gcp": "@pulumi/gcp", "azure": "@pulumi/azure-native",
               "digitalocean": "@pulumi/digitalocean", "hetzner": "@pulumi/hcloud"}
    pkg = pkg_map.get(cloud, "@pulumi/pulumi")
    index_ts = (
        "import * as pulumi from '@pulumi/pulumi';\n"
        f"// import * as cloud from '{pkg}';\n"
        "import * as fs from 'fs';\n\n"
        "const userData = fs.readFileSync('cloud-init.yaml', 'utf8');\n"
        f"// Create a VM on {cloud} that boots the app container via `userData`.\n"
        "// See the provider docs for the exact resource name and inputs.\n"
        "export const note = 'Pulumi starter - wire the VM resource for your cloud.';\n"
    )
    pulumi_yaml = f"name: launchpad-provision\nruntime: nodejs\ndescription: Provision app on {cloud}\n"
    package_json = (
        '{\n  "name": "launchpad-provision",\n  "devDependencies": { "@types/node": "^20" },\n'
        f'  "dependencies": {{ "@pulumi/pulumi": "^3.0.0", "{pkg}": "^6.0.0" }}\n}}\n'
    )
    return [
        ScaffoldFile(path="infra/pulumi/index.ts", content=index_ts),
        ScaffoldFile(path="infra/pulumi/Pulumi.yaml", content=pulumi_yaml),
        ScaffoldFile(path="infra/pulumi/package.json", content=package_json),
        ScaffoldFile(path="infra/pulumi/cloud-init.yaml", content=_cloud_init(spec)),
    ]


def _aws_native_files(spec: ProvisionSpec) -> list[ScaffoldFile]:
    tpl = (
        "AWSTemplateFormatVersion: '2010-09-09'\n"
        "Description: Launchpad app on EC2 (cloud-init user-data)\n"
        "Parameters:\n  InstanceType: { Type: String, Default: t3.small }\n"
        "Resources:\n  AppInstance:\n    Type: AWS::EC2::Instance\n"
        "    Properties:\n      InstanceType: !Ref InstanceType\n"
        "      ImageId: ami-0ubuntu  # replace with a valid Ubuntu AMI\n"
        "      UserData:\n        Fn::Base64: !Sub |\n"
        + "".join(f"          {line}\n" for line in _cloud_init(spec).splitlines())
    )
    return [ScaffoldFile(path="infra/cloudformation/stack.yaml", content=tpl)]


def _azure_native_files(spec: ProvisionSpec) -> list[ScaffoldFile]:
    bicep = (
        "// Azure Bicep starter - Linux VM booting the app container via cloud-init.\n"
        "param location string = resourceGroup().location\n"
        "param vmName string = 'launchpad-app'\n"
        "// Set customData to base64(loadTextContent('cloud-init.yaml')) on the VM's\n"
        "// osProfile. See README for the full VM/NIC/vnet resources.\n"
    )
    return [
        ScaffoldFile(path="infra/bicep/main.bicep", content=bicep),
        ScaffoldFile(path="infra/bicep/cloud-init.yaml", content=_cloud_init(spec)),
    ]


def _gcp_native_files(spec: ProvisionSpec) -> list[ScaffoldFile]:
    script = (
        "#!/usr/bin/env bash\n# GCP native provisioning via gcloud.\nset -euo pipefail\n"
        f"ZONE=\"${{ZONE:-{(spec.region or 'us-central1')}-a}}\"\n"
        "gcloud compute instances create launchpad-app \\\n"
        "  --zone \"$ZONE\" \\\n"
        "  --image-family ubuntu-2204-lts --image-project ubuntu-os-cloud \\\n"
        "  --metadata-from-file user-data=cloud-init.yaml\n"
    )
    return [
        ScaffoldFile(path="infra/gcp/deploy.sh", content=script),
        ScaffoldFile(path="infra/gcp/cloud-init.yaml", content=_cloud_init(spec)),
    ]


def render_provisioning_files(
    tool: str,
    provider_id: str,
    spec: ProvisionSpec,
) -> list[ScaffoldFile]:
    """Return the files to scaffold for the chosen provisioning tool."""
    tool = (tool or "scripting").strip().lower()
    if tool == "scripting":
        return _scripting_files(provider_id, spec)
    if tool in ("terraform", "opentofu"):
        return _terraform_files(provider_id, spec, dir_name=tool)
    if tool == "pulumi":
        return _pulumi_files(provider_id, spec)
    if tool == "aws-native":
        return _aws_native_files(spec)
    if tool == "azure-native":
        return _azure_native_files(spec)
    if tool == "gcp-native":
        return _gcp_native_files(spec)
    # Unknown tool -> fall back to the default scripting method.
    return _scripting_files(provider_id, spec)


__all__ = ["ScaffoldFile", "render_provisioning_files"]

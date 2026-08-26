"""Provisioning + configuration tool catalog.

Additive metadata describing which infrastructure-provisioning tools (Terraform, Pulumi,
OpenTofu, cloud-native provisioners) and configuration-management tools (Ansible,
cloud-init) are available, and which clouds each one supports. The frontend consumes this
to let a user pick a tool and to restrict cloud-specific tools (AWS/Azure native) to their
own cloud.

This is pure static metadata - it does not run anything. The existing IaC generation
(terraform_bundle / iac_generator / ansible_runner) remains the execution path; this just
describes the menu.
"""

from __future__ import annotations

from pydantic import BaseModel

# Sentinel meaning "works with every cloud provider in the registry".
ALL_CLOUDS = "*"


class ProvisioningTool(BaseModel):
    id: str
    label: str
    # 'iac' = infrastructure provisioning; 'config' = machine/app configuration.
    category: str
    description: str
    # Provider ids this tool supports, or [ALL_CLOUDS] for cloud-agnostic tools.
    supported_clouds: list[str]
    docs_url: str | None = None
    # Whether Launchpad currently has an execution path wired for this tool.
    implemented: bool = True
    # The default provisioning method (exactly one tool sets this true).
    default: bool = False


_PROVISIONING_TOOLS: list[ProvisioningTool] = [
    # --- Infrastructure provisioning (IaC) ---
    ProvisioningTool(
        id="scripting",
        label="LaunchProvision",
        category="iac",
        description="Default. Provisions the selected cloud service (cluster, registry, "
                    "VPC, secrets) via infra/launchProvision.sh. Works on any cloud.",
        supported_clouds=[ALL_CLOUDS],
        docs_url="https://cloudinit.readthedocs.io/",
        default=True,
    ),
    ProvisioningTool(
        id="terraform",
        label="Terraform",
        category="iac",
        description="Cloud-agnostic IaC. Works with every supported cloud.",
        supported_clouds=[ALL_CLOUDS],
        docs_url="https://developer.hashicorp.com/terraform",
    ),
    ProvisioningTool(
        id="opentofu",
        label="OpenTofu",
        category="iac",
        description="Open-source Terraform fork. Cloud-agnostic.",
        supported_clouds=[ALL_CLOUDS],
        docs_url="https://opentofu.org/",
    ),
    ProvisioningTool(
        id="pulumi",
        label="Pulumi",
        category="iac",
        description="IaC in general-purpose languages. Cloud-agnostic.",
        supported_clouds=[ALL_CLOUDS],
        docs_url="https://www.pulumi.com/docs/",
    ),
    ProvisioningTool(
        id="aws-native",
        label="AWS Native (CloudFormation)",
        category="iac",
        description="AWS-only provisioning via CloudFormation / native SDK. Restricted to AWS.",
        supported_clouds=["aws", "aws-legacy"],
        docs_url="https://docs.aws.amazon.com/cloudformation/",
    ),
    ProvisioningTool(
        id="azure-native",
        label="Azure Native (ARM / Bicep)",
        category="iac",
        description="Azure-only provisioning via ARM templates / native SDK. Restricted to Azure.",
        supported_clouds=["azure", "azure-legacy"],
        docs_url="https://learn.microsoft.com/azure/azure-resource-manager/",
    ),
    ProvisioningTool(
        id="gcp-native",
        label="GCP Native (Deployment Manager)",
        category="iac",
        description="GCP-only provisioning via native SDK / Deployment Manager. Restricted to GCP.",
        supported_clouds=["gcp", "gcp-legacy"],
        docs_url="https://cloud.google.com/deployment-manager/docs",
    ),
    # --- Configuration management ---
    ProvisioningTool(
        id="cloud-init",
        label="LaunchConfig",
        category="config",
        description="Default. First-boot / post-create configuration (Docker, env, systemd). "
                    "Built in. Replace with Ansible or a registered Puppet/Chef plugin.",
        supported_clouds=[ALL_CLOUDS],
        docs_url="https://cloudinit.readthedocs.io/",
        default=True,
    ),
    ProvisioningTool(
        id="ansible",
        label="Ansible",
        category="config",
        description="Optional. Agentless VM/app configuration. Register a plugin or enable this tool.",
        supported_clouds=[ALL_CLOUDS],
        docs_url="https://docs.ansible.com/",
    ),
    ProvisioningTool(
        id="puppet",
        label="Puppet",
        category="config",
        description="Optional. Register a Puppet config plugin to configure VMs after provision.",
        supported_clouds=[ALL_CLOUDS],
        docs_url="https://www.puppet.com/docs",
        implemented=False,
    ),
    ProvisioningTool(
        id="chef",
        label="Chef",
        category="config",
        description="Optional. Register a Chef config plugin to configure VMs after provision.",
        supported_clouds=[ALL_CLOUDS],
        docs_url="https://docs.chef.io/",
        implemented=False,
    ),
]


def _supports(tool: ProvisioningTool, provider_id: str) -> bool:
    return ALL_CLOUDS in tool.supported_clouds or provider_id in tool.supported_clouds


def build_tools_catalog() -> list[dict]:
    """Return every provisioning + configuration tool as UI-facing metadata."""
    return [t.model_dump() for t in _PROVISIONING_TOOLS]


def tools_for_cloud(provider_id: str) -> list[dict]:
    """Return only the tools compatible with a given cloud provider id."""
    return [t.model_dump() for t in _PROVISIONING_TOOLS if _supports(t, provider_id)]


__all__ = ["ALL_CLOUDS", "ProvisioningTool", "build_tools_catalog", "tools_for_cloud"]

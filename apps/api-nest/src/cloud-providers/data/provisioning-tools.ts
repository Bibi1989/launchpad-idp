import type { ProvisioningTool } from '../cloud-providers.types';

// Sentinel meaning "works with every cloud" (mirrors ALL_CLOUDS in tools.py).
export const ALL_CLOUDS = '*';

/**
 * Provisioning + configuration tools, ported from app/providers/tools.py.
 * LaunchProvision is the default provisioner; LaunchConfig is the default VM config path.
 */
export const PROVISIONING_TOOLS: ProvisioningTool[] = [
  {
    id: 'scripting',
    label: 'LaunchProvision',
    category: 'iac',
    description:
      'Default. Provisions the selected cloud service (cluster, registry, VPC, secrets) ' +
      'via infra/launchProvision.sh. Works on any cloud.',
    supported_clouds: [ALL_CLOUDS],
    docs_url: 'https://cloudinit.readthedocs.io/',
    implemented: true,
    default: true,
  },
  tool('terraform', 'Terraform', 'iac', 'Cloud-agnostic IaC. Works with every supported cloud.', [ALL_CLOUDS], 'https://developer.hashicorp.com/terraform'),
  tool('opentofu', 'OpenTofu', 'iac', 'Open-source Terraform fork. Cloud-agnostic.', [ALL_CLOUDS], 'https://opentofu.org/'),
  tool('pulumi', 'Pulumi', 'iac', 'IaC in general-purpose languages. Cloud-agnostic.', [ALL_CLOUDS], 'https://www.pulumi.com/docs/'),
  tool('aws-native', 'AWS Native (CloudFormation)', 'iac', 'AWS-only provisioning via CloudFormation / native SDK. Restricted to AWS.', ['aws', 'aws-legacy'], 'https://docs.aws.amazon.com/cloudformation/'),
  tool('azure-native', 'Azure Native (ARM / Bicep)', 'iac', 'Azure-only provisioning via ARM templates / native SDK. Restricted to Azure.', ['azure', 'azure-legacy'], 'https://learn.microsoft.com/azure/azure-resource-manager/'),
  tool('gcp-native', 'GCP Native (Deployment Manager)', 'iac', 'GCP-only provisioning via native SDK / Deployment Manager. Restricted to GCP.', ['gcp', 'gcp-legacy'], 'https://cloud.google.com/deployment-manager/docs'),
  {
    id: 'cloud-init',
    label: 'LaunchConfig',
    category: 'config',
    description:
      'Default. First-boot / post-create configuration (Docker, env, systemd). ' +
      'Built in. Replace with Ansible or a registered Puppet/Chef plugin.',
    supported_clouds: [ALL_CLOUDS],
    docs_url: 'https://cloudinit.readthedocs.io/',
    implemented: true,
    default: true,
  },
  tool('ansible', 'Ansible', 'config', 'Optional. Agentless VM/app configuration. Register a plugin or enable this tool.', [ALL_CLOUDS], 'https://docs.ansible.com/'),
  {
    id: 'puppet',
    label: 'Puppet',
    category: 'config',
    description: 'Optional. Register a Puppet config plugin to configure VMs after provision.',
    supported_clouds: [ALL_CLOUDS],
    docs_url: 'https://www.puppet.com/docs',
    implemented: false,
    default: false,
  },
  {
    id: 'chef',
    label: 'Chef',
    category: 'config',
    description: 'Optional. Register a Chef config plugin to configure VMs after provision.',
    supported_clouds: [ALL_CLOUDS],
    docs_url: 'https://docs.chef.io/',
    implemented: false,
    default: false,
  },
];

export function toolsForCloud(providerId: string): ProvisioningTool[] {
  return PROVISIONING_TOOLS.filter(
    (t) => t.supported_clouds.includes(ALL_CLOUDS) || t.supported_clouds.includes(providerId),
  );
}

function tool(
  id: string,
  label: string,
  category: 'iac' | 'config',
  description: string,
  supportedClouds: string[],
  docsUrl: string,
): ProvisioningTool {
  return {
    id,
    label,
    category,
    description,
    supported_clouds: supportedClouds,
    docs_url: docsUrl,
    implemented: true,
    default: false,
  };
}

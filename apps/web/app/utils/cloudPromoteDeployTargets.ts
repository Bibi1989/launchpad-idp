import type { CloudProvider, WorkspaceRuntimeMode } from '~/types/provisioning'

export type CloudPromoteNetworkMode = 'existing' | 'create' | 'default'
export type CloudPromoteSecurityGroupMode = 'auto' | 'existing'

export type CloudPromoteDeployTarget = {
  id: string
  category: 'compute' | 'registry' | 'network' | 'security' | 'platform'
  title: string
  detail?: string
}

export type CloudPromoteDeployPlanInput = {
  provider: CloudProvider
  runtimeMode: WorkspaceRuntimeMode
  region: string
  networkMode: CloudPromoteNetworkMode
  createSubnets?: boolean
  existingVpcId?: string | null
  existingVpcLabel?: string | null
  securityGroupMode?: CloudPromoteSecurityGroupMode
  existingSecurityGroupId?: string | null
  existingSecurityGroupLabel?: string | null
  processStrategy?: string | null
}

function isServerless(runtimeMode: WorkspaceRuntimeMode): boolean {
  return runtimeMode === 'docker_compose'
}

export function resolveCloudPromoteDeployTargets(
  input: CloudPromoteDeployPlanInput,
): CloudPromoteDeployTarget[] {
  const region = (input.region || '').trim()
  const serverless = isServerless(input.runtimeMode)
  const targets: CloudPromoteDeployTarget[] = []

  if (input.provider === 'gcp') {
    targets.push({
      id: 'compute',
      category: 'compute',
      title: serverless ? 'Cloud Run' : 'Compute Engine (VM)',
      detail: region ? `Region: ${region}` : undefined,
    })
    targets.push({
      id: 'registry',
      category: 'registry',
      title: 'Artifact Registry',
      detail: 'Build and push container images',
    })
  } else if (input.provider === 'aws') {
    targets.push({
      id: 'compute',
      category: 'compute',
      title: serverless ? 'App Runner' : 'EC2',
      detail: region ? `Region: ${region}` : undefined,
    })
    targets.push({
      id: 'registry',
      category: 'registry',
      title: 'ECR',
      detail: 'Build and push container images',
    })
  } else if (input.provider === 'azure') {
    targets.push({
      id: 'compute',
      category: 'compute',
      title: serverless ? 'Container Apps' : 'Virtual Machine',
      detail: region ? `Location: ${region}` : undefined,
    })
    targets.push({
      id: 'registry',
      category: 'registry',
      title: 'ACR',
      detail: 'Build and push container images',
    })
  } else if (input.provider === 'cloudflare') {
    targets.push({
      id: 'workers',
      category: 'platform',
      title: 'Workers',
    })
    targets.push({
      id: 'pages',
      category: 'platform',
      title: 'Pages',
    })
    return targets
  }

  if (input.provider === 'gcp' || input.provider === 'aws' || input.provider === 'azure') {
    const networkLabel = input.provider === 'azure' ? 'VNet' : 'VPC'
    if (input.networkMode === 'existing' && input.existingVpcId) {
      targets.push({
        id: 'network',
        category: 'network',
        title: `Existing ${networkLabel}`,
        detail: input.existingVpcLabel || input.existingVpcId,
      })
    } else if (input.networkMode === 'create') {
      targets.push({
        id: 'network',
        category: 'network',
        title: `New ${networkLabel}`,
        detail: input.createSubnets ? 'With subnets' : 'VPC/VNet only',
      })
    } else {
      targets.push({
        id: 'network',
        category: 'network',
        title: `Default ${networkLabel}`,
        detail: 'Provider default network',
      })
    }
  }

  if (input.provider === 'aws' && !serverless) {
    if (input.securityGroupMode === 'existing' && input.existingSecurityGroupId) {
      targets.push({
        id: 'security-group',
        category: 'security',
        title: 'Security group',
        detail: input.existingSecurityGroupLabel || input.existingSecurityGroupId,
      })
    } else {
      targets.push({
        id: 'security-group',
        category: 'security',
        title: 'Security group',
        detail: 'Launchpad-managed (SSH + app port)',
      })
    }
  }

  const strategy = (input.processStrategy || 'docker').trim().toLowerCase()
  if (!serverless && strategy && strategy !== 'docker') {
    targets.push({
      id: 'runtime',
      category: 'platform',
      title: 'Process runtime',
      detail: strategy === 'pm2' ? 'PM2 on VM' : strategy === 'systemd' ? 'systemd on VM' : strategy,
    })
  }

  return targets
}

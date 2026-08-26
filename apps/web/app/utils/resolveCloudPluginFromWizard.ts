import type { CloudPluginSelection } from '~/types/cloudPluginSelection'
import type { WorkspaceWizardConfig } from '~/types/provisioning'
import { emptyCloudPluginSelection } from '~/types/cloudPluginSelection'

function str(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed || null
}

function resourcesOf(config: WorkspaceWizardConfig): Record<string, unknown> {
  const cloud = config.cloud as { resources?: Record<string, unknown> }
  return cloud?.resources ?? {}
}

/** Restore launch/promote plugin selection from wizard snapshot (explicit or inferred). */
export function resolveCloudPluginFromWizard(
  config: WorkspaceWizardConfig,
): CloudPluginSelection {
  const stored = config.cloud_plugin
  if (stored?.provider) {
    return {
      provider: stored.provider,
      service: stored.service ?? null,
      region: stored.region ?? null,
      tier: stored.tier ?? null,
    }
  }

  const provider = config.cloud.provider
  if (provider === 'local') {
    return emptyCloudPluginSelection()
  }
  const resources = resourcesOf(config)
  const region =
    str(resources.region) || str(resources.location) || str(resources.zone) || null
  const tier =
    str(resources.machine_type) || str(resources.instance_type) || str(resources.size) || null

  if (provider === 'gcp') {
    if (resources.compute_instance) {
      const docker =
        config.running_instance?.process_strategy === 'docker'
        || config.running_instance?.kind === 'serverless'
      return {
        provider: docker ? 'gcp-gce-docker' : 'gcp-gce',
        service: docker ? 'gce-docker' : 'gce',
        region,
        tier,
      }
    }
    if (resources.gke) {
      return { provider: 'gcp-gke', service: 'gke', region, tier }
    }
    if (resources.cloud_run) {
      return { provider: 'gcp-cloud-run', service: 'cloud-run', region, tier }
    }
  }

  if (provider === 'aws') {
    if (resources.ec2) {
      const docker = config.running_instance?.process_strategy === 'docker'
      return {
        provider: docker ? 'aws-ec2-docker' : 'aws-ec2',
        service: docker ? 'ec2-docker' : 'ec2',
        region,
        tier,
      }
    }
    if (resources.eks) {
      return { provider: 'aws-eks', service: 'eks', region, tier }
    }
    if (resources.app_runner) {
      return { provider: 'aws-ecs-fargate', service: 'ecs-fargate', region, tier }
    }
  }

  if (provider === 'azure') {
    if (resources.aks) {
      return { provider: 'azure-aks', service: 'aks', region, tier }
    }
    if (resources.container_apps) {
      return { provider: 'azure-container-apps', service: 'container-apps', region, tier }
    }
  }

  if (provider === 'cloudflare') {
    if (resources.workers) {
      return { provider: 'cloudflare-workers', service: 'workers', region, tier }
    }
    if (resources.pages) {
      return { provider: 'cloudflare-pages', service: 'pages', region, tier }
    }
    if (resources.tunnels) {
      return { provider: 'cloudflare-tunnel', service: 'tunnel', region, tier }
    }
  }

  return emptyCloudPluginSelection()
}

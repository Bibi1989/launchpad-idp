import { describe, expect, it } from 'vitest'
import { applyPluginServiceDefaults } from '../app/utils/applyPluginServiceDefaults'
import type { CloudPluginSelection } from '../app/types/cloudPluginSelection'

function emptyForm() {
  return {
    provider: 'gcp' as const,
    runtime_mode: 'kubernetes' as const,
    gcp: {
      vpc: true,
      subnets: true,
      gke: false,
      artifact_registry: false,
      cloud_run: false,
      compute_instance: false,
    },
    aws: {
      vpc: true,
      subnets: true,
      eks: false,
      ecr: false,
      ec2: false,
      app_runner: false,
    },
    azure: {
      vnet: true,
      subnets: true,
      aks: false,
      acr: false,
      container_apps: false,
    },
    cloudflare: {
      workers: false,
      pages: false,
      tunnels: false,
    },
    kubernetes_options: { image_source: 'build_registry' as const },
  }
}

function plugin(provider: string, service: string): CloudPluginSelection {
  return { provider, service, region: null, tier: null }
}

describe('applyPluginServiceDefaults', () => {
  it('enables GKE plus Artifact Registry and VPC', () => {
    const form = emptyForm()
    applyPluginServiceDefaults(form, plugin('gcp-gke', 'gke'))
    expect(form.gcp.gke).toBe(true)
    expect(form.gcp.vpc).toBe(true)
    expect(form.gcp.artifact_registry).toBe(true)
    expect(form.runtime_mode).toBe('kubernetes')
  })

  it('skips Artifact Registry when the image hub is external', () => {
    const form = emptyForm()
    applyPluginServiceDefaults(form, plugin('gcp-gke', 'gke'), 'external')
    expect(form.gcp.gke).toBe(true)
    expect(form.gcp.artifact_registry).toBe(false)
  })

  it('enables GCE compute_instance for VM plugins', () => {
    const form = emptyForm()
    applyPluginServiceDefaults(form, plugin('gcp-gce', 'gce'))
    expect(form.gcp.compute_instance).toBe(true)
    expect(form.gcp.gke).toBe(false)
    expect(form.runtime_mode).toBe('running_instance')
  })

  it('enables EKS plus ECR', () => {
    const form = emptyForm()
    form.provider = 'aws'
    applyPluginServiceDefaults(form, plugin('aws-eks', 'eks'))
    expect(form.aws.eks).toBe(true)
    expect(form.aws.ecr).toBe(true)
    expect(form.runtime_mode).toBe('kubernetes')
  })
})

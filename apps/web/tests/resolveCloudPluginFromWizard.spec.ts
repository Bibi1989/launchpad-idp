import { describe, expect, it } from 'vitest'
import { resolveCloudPluginFromWizard } from '../app/utils/resolveCloudPluginFromWizard'
import type { WorkspaceWizardConfig } from '../app/types/provisioning'
import { defaultKubernetesWorkloadOptions } from '../app/utils/cloudValidation'

function baseConfig(overrides: Partial<WorkspaceWizardConfig> = {}): WorkspaceWizardConfig {
  return {
    name: 'ws',
    iac_engine: 'terraform',
    cloud: { provider: 'gcp', resources: {} },
    run_init: true,
    runtime_mode: 'running_instance',
    running_instance: {
      kind: 'vm',
      process_strategy: 'docker',
      host: null,
      service_name: null,
      ssh_user: 'ubuntu',
      ssh_port: 22,
      listen_port: 8080,
      code_source: 'github',
      preview_url_override: null,
      region: null,
      ssh_key_path: null,
    },
    artifact_mode: 'iac_only',
    kubernetes_packaging: 'none',
    kubernetes_options: defaultKubernetesWorkloadOptions(),
    cost_optimization: {
      spotScheduling: { enabled: false, schedule: 'weeknights' },
      hpa: { enabled: false },
      vpa: { enabled: false },
      resources: { enabled: false },
      idleShutdown: { enabled: false, schedule: 'weeknights' },
    },
    container_scaffold: {
      enabled: false,
      generate_dockerfile: false,
      generate_docker_compose: false,
      stack: 'monolith',
      frameworks: [],
      app_name: 'app',
      listen_port: 8080,
      services: [],
    },
    dependencies: {
      postgres: { enabled: false, placement: 'in_cluster' },
      mysql: { enabled: false, placement: 'in_cluster' },
      mongodb: { enabled: false, placement: 'in_cluster' },
      redis: { enabled: false, placement: 'in_cluster' },
      kafka: { enabled: false, placement: 'in_cluster' },
      rabbitmq: { enabled: false, placement: 'in_cluster' },
    },
    ansible: { enabled: false },
    has_credentials: true,
    ...overrides,
  }
}

describe('resolveCloudPluginFromWizard', () => {
  it('returns stored cloud_plugin when present', () => {
    const plugin = resolveCloudPluginFromWizard(
      baseConfig({
        cloud_plugin: {
          provider: 'gcp-gce',
          service: 'gce',
          region: 'europe-west3',
          tier: 'e2-medium',
        },
      }),
    )
    expect(plugin.provider).toBe('gcp-gce')
    expect(plugin.region).toBe('europe-west3')
  })

  it('infers GCE from compute_instance flags', () => {
    const plugin = resolveCloudPluginFromWizard(
      baseConfig({
        cloud: {
          provider: 'gcp',
          resources: { compute_instance: true, region: 'us-central1' },
        },
      }),
    )
    expect(plugin.provider).toBe('gcp-gce-docker')
    expect(plugin.service).toBe('gce-docker')
    expect(plugin.region).toBe('us-central1')
  })

  it('infers GKE from gke flag', () => {
    const plugin = resolveCloudPluginFromWizard(
      baseConfig({
        runtime_mode: 'kubernetes',
        cloud: {
          provider: 'gcp',
          resources: { gke: true, region: 'us-central1' },
        },
      }),
    )
    expect(plugin.provider).toBe('gcp-gke')
    expect(plugin.service).toBe('gke')
  })
})

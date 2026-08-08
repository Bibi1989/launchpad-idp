import { describe, expect, it } from 'vitest'
import {
  isRuntimeModeAllowed,
  normalizeArtifactsForRuntimeMode,
  runtimeModesForProvider,
} from '~/utils/workspaceRuntimeMode'
import { resolvePreviewDeployPlan } from '~/utils/previewDeployPlan'
import type { WorkspaceWizardConfig } from '~/types/provisioning'
import { defaultContainerScaffold, defaultWorkloadDependencies } from '~/utils/cloudValidation'
import { defaultRunningInstanceConfig } from '~/utils/workspaceRuntimeMode'
import { defaultCostOptimizationConfig } from '~/utils/costOptimization'
import { defaultKubernetesWorkloadOptions } from '~/utils/cloudValidation'

describe('workspaceRuntimeMode', () => {
  it('allows compose only for local', () => {
    expect(isRuntimeModeAllowed('local', 'docker_compose')).toBe(true)
    expect(isRuntimeModeAllowed('gcp', 'docker_compose')).toBe(false)
    expect(runtimeModesForProvider('gcp')).not.toContain('docker_compose')
  })

  it('normalizes compose to packaging none and scaffold on', () => {
    const result = normalizeArtifactsForRuntimeMode({
      provider: 'local',
      runtimeMode: 'docker_compose',
      artifactMode: 'manifest_only',
      kubernetesPackaging: 'raw_manifests',
      containerScaffold: { ...defaultContainerScaffold(), enabled: false },
      runningInstance: defaultRunningInstanceConfig(),
    })
    expect(result.kubernetesPackaging).toBe('none')
    expect(result.artifactMode).toBe('iac_only')
    expect(result.containerScaffold.enabled).toBe(true)
  })
})

describe('resolvePreviewDeployPlan', () => {
  function baseConfig(overrides: Partial<WorkspaceWizardConfig> = {}): WorkspaceWizardConfig {
    return {
      name: 'demo',
      iac_engine: 'terraform',
      cloud: { provider: 'local', resources: { cluster_name: 'launchpad', context: 'kind-launchpad' } },
      run_init: true,
      runtime_mode: 'kubernetes',
      running_instance: defaultRunningInstanceConfig(),
      artifact_mode: 'manifest_only',
      kubernetes_packaging: 'raw_manifests',
      kubernetes_options: defaultKubernetesWorkloadOptions(),
      cost_optimization: defaultCostOptimizationConfig(),
      container_scaffold: defaultContainerScaffold(),
      dependencies: defaultWorkloadDependencies(),
      has_credentials: false,
      ...overrides,
    }
  }

  it('resolves compose with datastore flags', () => {
    const deps = defaultWorkloadDependencies()
    deps.postgres.enabled = true
    deps.redis.enabled = true
    const plan = resolvePreviewDeployPlan(
      baseConfig({
        runtime_mode: 'docker_compose',
        kubernetes_packaging: 'none',
        artifact_mode: 'iac_only',
        dependencies: deps,
      }),
    )
    expect(plan.deploy_mode).toBe('compose')
    expect(plan.skip_local_cluster).toBe(true)
    expect(plan.enable_postgres).toBe(true)
    expect(plan.enable_redis).toBe(true)
  })

  it('resolves kubernetes packaging to manifest', () => {
    const plan = resolvePreviewDeployPlan(baseConfig())
    expect(plan.deploy_mode).toBe('manifest')
  })

  it('resolves attach mode from running_instance', () => {
    const plan = resolvePreviewDeployPlan(
      baseConfig({
        runtime_mode: 'running_instance',
        kubernetes_packaging: 'none',
        artifact_mode: 'iac_only',
        running_instance: {
          kind: 'endpoint',
          endpoint_url: 'https://app.example.com',
          kube_context: null,
        },
      }),
    )
    expect(plan.deploy_mode).toBe('attach')
    expect(plan.skip_local_cluster).toBe(true)
    expect(plan.attach_endpoint_url).toBe('https://app.example.com')
  })
})

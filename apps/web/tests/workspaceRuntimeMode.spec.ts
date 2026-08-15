import { describe, expect, it } from 'vitest'
import {
  isRuntimeModeAllowed,
  normalizeArtifactsForRuntimeMode,
  runtimeModesForProvider,
} from '~/utils/workspaceRuntimeMode'
import { resolvePreviewDeployPlan } from '~/utils/previewDeployPlan'
import type { WorkspaceWizardConfig } from '~/types/provisioning'
import {
  defaultAnsibleConfig,
  defaultContainerScaffold,
  defaultWorkloadDependencies,
} from '~/utils/cloudValidation'
import {
  defaultRunningInstanceConfig,
  validateRunningInstanceFields,
} from '~/utils/workspaceRuntimeMode'
import { defaultCostOptimizationConfig } from '~/utils/costOptimization'
import { defaultKubernetesWorkloadOptions } from '~/utils/cloudValidation'

describe('workspaceRuntimeMode', () => {
  it('allows compose only for local', () => {
    expect(isRuntimeModeAllowed('local', 'docker_compose')).toBe(true)
    expect(isRuntimeModeAllowed('gcp', 'docker_compose')).toBe(false)
    expect(runtimeModesForProvider('gcp')).not.toContain('docker_compose')
  })

  it('vm without host is allowed for local/gcp/aws, required for azure', () => {
    const vm = { ...defaultRunningInstanceConfig(), kind: 'vm' as const, host: '' }
    for (const provider of ['local', 'gcp', 'aws'] as const) {
      expect(
        validateRunningInstanceFields({ provider, mode: 'running_instance', runningInstance: vm }),
      ).toBeNull()
    }
    expect(
      validateRunningInstanceFields({ provider: 'azure', mode: 'running_instance', runningInstance: vm }),
    ).toBe('vm_host')
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
    expect(result.containerScaffold.services).toHaveLength(2)
    expect(result.containerScaffold.services?.map((s) => s.name)).toEqual(['web-ui', 'api-server'])
  })

  it('keeps explicit compose services when already set', () => {
    const result = normalizeArtifactsForRuntimeMode({
      provider: 'local',
      runtimeMode: 'docker_compose',
      artifactMode: 'iac_only',
      kubernetesPackaging: 'none',
      containerScaffold: {
        ...defaultContainerScaffold(),
        enabled: true,
        services: [{ name: 'api', app_kind: 'backend', stack: 'fastapi', listen_port: 8000 }],
      },
      runningInstance: defaultRunningInstanceConfig(),
    })
    expect(result.containerScaffold.services).toHaveLength(1)
    expect(result.containerScaffold.services?.[0]?.name).toBe('api')
  })

  it('preserves link/import scaffold without default apps', () => {
    const result = normalizeArtifactsForRuntimeMode({
      provider: 'gcp',
      runtimeMode: 'running_instance',
      artifactMode: 'iac_only',
      kubernetesPackaging: 'none',
      containerScaffold: {
        ...defaultContainerScaffold(),
        enabled: true,
        services: [],
        frameworks: [],
        generate_dockerfile: false,
        generate_docker_compose: false,
      },
      runningInstance: defaultRunningInstanceConfig(),
    })
    expect(result.containerScaffold.enabled).toBe(true)
    expect(result.containerScaffold.services).toEqual([])
    expect(result.containerScaffold.generate_dockerfile).toBe(false)
    expect(result.containerScaffold.generate_docker_compose).toBe(false)
  })

  it('preserves user host listen_port over scaffold service ports', () => {
    const result = normalizeArtifactsForRuntimeMode({
      provider: 'local',
      runtimeMode: 'running_instance',
      artifactMode: 'iac_only',
      kubernetesPackaging: 'none',
      containerScaffold: {
        ...defaultContainerScaffold(),
        enabled: true,
        generate_docker_compose: false,
        services: [
          {
            name: 'web-ui',
            app_kind: 'frontend',
            stack: 'nextjs',
            listen_port: 3000,
            expose_preview: true,
          },
          {
            name: 'api-server',
            app_kind: 'backend',
            stack: 'node',
            listen_port: 8080,
          },
        ],
      },
      runningInstance: {
        ...defaultRunningInstanceConfig('local'),
        listen_port: 8090,
      },
    })
    expect(result.runningInstance.listen_port).toBe(8090)
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
      ansible: defaultAnsibleConfig(),
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

  it('resolves attach mode from running_instance vm', () => {
    const plan = resolvePreviewDeployPlan(
      baseConfig({
        runtime_mode: 'running_instance',
        kubernetes_packaging: 'none',
        artifact_mode: 'iac_only',
        running_instance: {
          ...defaultRunningInstanceConfig(),
          kind: 'vm',
          host: '203.0.113.10',
          preview_url_override: 'https://app.example.com',
        },
      }),
    )
    expect(plan.deploy_mode).toBe('attach')
    expect(plan.skip_local_cluster).toBe(true)
    expect(plan.attach_kind).toBe('vm')
    expect(plan.attach_host).toBe('203.0.113.10')
  })

  it('resolves attach mode for local_machine', () => {
    const plan = resolvePreviewDeployPlan(
      baseConfig({
        runtime_mode: 'running_instance',
        kubernetes_packaging: 'none',
        artifact_mode: 'iac_only',
        running_instance: defaultRunningInstanceConfig('local'),
      }),
    )
    expect(plan.deploy_mode).toBe('attach')
    expect(plan.attach_kind).toBe('local_machine')
  })
})

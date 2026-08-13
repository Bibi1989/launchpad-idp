import type {
  CloudProvider,
  ContainerScaffoldConfig,
  KubernetesPackaging,
  RunningInstanceConfig,
  RunningInstanceKind,
  WorkspaceArtifactsMode,
  WorkspaceRuntimeMode,
} from '~/types/provisioning'
import { defaultContainerServices } from '~/utils/cloudValidation'

export type RuntimeModeOption = {
  id: WorkspaceRuntimeMode
  allowedFor: (provider: CloudProvider) => boolean
}

export const RUNTIME_MODE_OPTIONS: RuntimeModeOption[] = [
  {
    id: 'kubernetes',
    allowedFor: () => true,
  },
  {
    id: 'docker_compose',
    allowedFor: (provider) => provider === 'local',
  },
  {
    id: 'running_instance',
    allowedFor: () => true,
  },
]

export function defaultRunningInstanceConfig(
  provider: CloudProvider = 'local',
): RunningInstanceConfig {
  return {
    kind: provider === 'local' ? 'local_machine' : 'vm',
    service_name: null,
    region: null,
    host: null,
    ssh_user: 'ubuntu',
    ssh_port: 22,
    ssh_key_path: null,
    listen_port: 8080,
    process_strategy: 'docker',
    code_source: 'ssh',
    reverse_proxy: 'none',
    preview_url_override: null,
    kube_context: null,
    endpoint_url: null,
  }
}

export function defaultRuntimeModeForProvider(provider: CloudProvider): WorkspaceRuntimeMode {
  void provider
  return 'kubernetes'
}

export function runtimeModesForProvider(provider: CloudProvider): WorkspaceRuntimeMode[] {
  return RUNTIME_MODE_OPTIONS.filter((opt) => opt.allowedFor(provider)).map((opt) => opt.id)
}

export function isRuntimeModeAllowed(
  provider: CloudProvider,
  mode: WorkspaceRuntimeMode,
): boolean {
  return runtimeModesForProvider(provider).includes(mode)
}

export function hasServerlessRuntime(
  provider: CloudProvider,
  resources: Record<string, unknown>,
): boolean {
  if (provider === 'gcp') return resources.cloud_run === true
  if (provider === 'aws') return resources.app_runner === true
  if (provider === 'azure') return resources.container_apps === true
  return false
}

export function instanceKindsForProvider(
  provider: CloudProvider,
  resources: Record<string, unknown> = {},
): RunningInstanceKind[] {
  if (provider === 'local') return ['local_machine', 'vm']
  const kinds: RunningInstanceKind[] = ['vm']
  if (hasServerlessRuntime(provider, resources)) {
    kinds.unshift('serverless')
  }
  // Offer serverless in the picker when the provider supports it even before the
  // resource toggle is on (selecting the target enables the flag).
  if (
    !kinds.includes('serverless')
    && (provider === 'gcp' || provider === 'aws' || provider === 'azure')
  ) {
    kinds.unshift('serverless')
  }
  return kinds
}

export function normalizeArtifactsForRuntimeMode(input: {
  provider: CloudProvider
  runtimeMode: WorkspaceRuntimeMode
  artifactMode: WorkspaceArtifactsMode
  kubernetesPackaging: KubernetesPackaging
  containerScaffold: ContainerScaffoldConfig
  runningInstance: RunningInstanceConfig
  resources?: Record<string, unknown>
}): {
  artifactMode: WorkspaceArtifactsMode
  kubernetesPackaging: KubernetesPackaging
  containerScaffold: ContainerScaffoldConfig
  runningInstance: RunningInstanceConfig
} {
  const { provider, runtimeMode } = input
  let runningInstance = { ...input.runningInstance }
  const resources = input.resources ?? {}

  // Coerce legacy kinds from older client state.
  if ((runningInstance.kind as string) === 'kube_context') {
    runningInstance = {
      ...runningInstance,
      kind: provider === 'local' ? 'local_machine' : 'vm',
    }
  }
  if ((runningInstance.kind as string) === 'endpoint') {
    runningInstance = {
      ...runningInstance,
      kind: 'vm',
      preview_url_override:
        runningInstance.preview_url_override || runningInstance.endpoint_url || null,
    }
  }

  if (runtimeMode === 'docker_compose') {
    const existing = input.containerScaffold.services ?? []
    const services = existing.length > 0 ? existing : defaultContainerServices()
    const primary = services[0]
    return {
      artifactMode: 'iac_only',
      kubernetesPackaging: 'none',
      containerScaffold: {
        ...input.containerScaffold,
        enabled: true,
        generate_dockerfile: true,
        generate_docker_compose: true,
        services,
        app_name: primary?.name || input.containerScaffold.app_name || 'web-ui',
        stack: primary?.stack || input.containerScaffold.stack || 'nextjs',
        listen_port: primary?.listen_port || input.containerScaffold.listen_port || 3000,
      },
      runningInstance,
    }
  }

  if (runtimeMode === 'running_instance') {
    if (
      hasServerlessRuntime(provider, resources)
      && runningInstance.kind === 'local_machine'
    ) {
      runningInstance = { ...runningInstance, kind: 'serverless' }
    }
    if (provider === 'local' && runningInstance.kind === 'serverless') {
      runningInstance = { ...runningInstance, kind: 'local_machine' }
    }
    const scaffold = input.containerScaffold.enabled
      ? input.containerScaffold
      : {
          ...input.containerScaffold,
          enabled: true,
          generate_dockerfile: true,
          generate_docker_compose: false,
        }
    // Keep runningInstance.listen_port as the user-chosen host publish port.
    // Container port is resolved from Dockerfile EXPOSE / service scaffold at attach.
    return {
      artifactMode:
        input.artifactMode === 'manifest_only' ? 'iac_only' : input.artifactMode,
      kubernetesPackaging: 'none',
      containerScaffold: scaffold,
      runningInstance,
    }
  }

  if (provider === 'local') {
    return {
      artifactMode: 'manifest_only',
      kubernetesPackaging:
        input.kubernetesPackaging === 'none' ? 'raw_manifests' : input.kubernetesPackaging,
      containerScaffold: input.containerScaffold,
      runningInstance,
    }
  }

  return {
    artifactMode: input.artifactMode,
    kubernetesPackaging: input.kubernetesPackaging,
    containerScaffold: input.containerScaffold,
    runningInstance,
  }
}

export function validateRunningInstanceFields(input: {
  provider: CloudProvider
  mode: WorkspaceRuntimeMode
  runningInstance: RunningInstanceConfig
  resources?: Record<string, unknown>
}): string | null {
  if (input.mode !== 'running_instance') return null
  const kind = input.runningInstance.kind

  if (kind === 'serverless') {
    if (!hasServerlessRuntime(input.provider, input.resources ?? {})) {
      // Allow selecting serverless before the resource flag is set; applyInstanceComputeTarget
      // enables it. Still block providers without a managed container service.
      if (
        input.provider !== 'gcp'
        && input.provider !== 'aws'
        && input.provider !== 'azure'
      ) {
        return 'serverless_unavailable'
      }
    }
    return null
  }

  if (kind === 'vm') {
    const hasHost = Boolean(input.runningInstance.host?.trim())
    const hasOverride = Boolean(
      input.runningInstance.preview_url_override?.trim()
      || input.runningInstance.endpoint_url?.trim(),
    )
    // A host is only required when nothing can supply one automatically:
    // local falls back to a Docker preview; GCP/AWS auto-create the VM. Azure
    // auto-provisioning is not implemented yet, so it still needs a host.
    const canAutocreate = ['local', 'gcp', 'aws'].includes(input.provider)
    if (!hasHost && !hasOverride && !canAutocreate) return 'vm_host'
    return null
  }

  if (kind === 'local_machine' && input.provider !== 'local') {
    return 'local_machine_provider'
  }

  return null
}

export function showsKubernetesRuntimeUi(mode: WorkspaceRuntimeMode): boolean {
  return mode === 'kubernetes'
}

export function requiresContainerScaffold(mode: WorkspaceRuntimeMode): boolean {
  return mode === 'docker_compose' || mode === 'running_instance'
}

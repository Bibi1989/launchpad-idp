import type {
  CloudProvider,
  ContainerScaffoldConfig,
  KubernetesPackaging,
  RunningInstanceConfig,
  RunningInstanceKind,
  WorkspaceArtifactsMode,
  WorkspaceRuntimeMode,
} from '~/types/provisioning'

export type RuntimeModeOption = {
  id: WorkspaceRuntimeMode
  /** When false, option is hidden for this provider. */
  allowedFor: (provider: CloudProvider) => boolean
}

export const RUNTIME_MODE_OPTIONS: RuntimeModeOption[] = [
  {
    id: 'kubernetes',
    allowedFor: () => true,
  },
  {
    id: 'docker_compose',
    // Local-only: never expose a remoted Docker socket.
    allowedFor: (provider) => provider === 'local',
  },
  {
    id: 'running_instance',
    allowedFor: () => true,
  },
]

export function defaultRunningInstanceConfig(): RunningInstanceConfig {
  return {
    kind: 'kube_context',
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
  if (provider === 'azure') return resources.container_apps === true
  return false
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

  if (runtimeMode === 'docker_compose') {
    return {
      artifactMode: 'iac_only',
      kubernetesPackaging: 'none',
      containerScaffold: {
        ...input.containerScaffold,
        enabled: true,
        generate_dockerfile: true,
        generate_docker_compose: true,
      },
      runningInstance,
    }
  }

  if (runtimeMode === 'running_instance') {
    if (
      hasServerlessRuntime(provider, input.resources ?? {})
      && runningInstance.kind === 'kube_context'
    ) {
      runningInstance = { ...runningInstance, kind: 'serverless' }
    }
    return {
      artifactMode:
        input.artifactMode === 'manifest_only' ? 'iac_only' : input.artifactMode,
      kubernetesPackaging: 'none',
      containerScaffold: input.containerScaffold,
      runningInstance,
    }
  }

  // kubernetes
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
  if (hasServerlessRuntime(input.provider, input.resources ?? {})) return null

  const kind: RunningInstanceKind = input.runningInstance.kind
  if (kind === 'endpoint' && !input.runningInstance.endpoint_url?.trim()) {
    return 'endpoint_url'
  }
  if (kind === 'kube_context' && !input.runningInstance.kube_context?.trim()) {
    return 'kube_context'
  }
  if (kind === 'serverless') {
    return 'serverless_unavailable'
  }
  return null
}

/** Whether the provision UI should show K8s packaging / kind cluster fields. */
export function showsKubernetesRuntimeUi(mode: WorkspaceRuntimeMode): boolean {
  return mode === 'kubernetes'
}

/** Whether Compose / Dockerfile scaffold should be forced on. */
export function requiresContainerScaffold(mode: WorkspaceRuntimeMode): boolean {
  return mode === 'docker_compose'
}

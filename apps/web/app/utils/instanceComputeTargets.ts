import type {
  CloudProvider,
  RunningInstanceConfig,
  RunningInstanceKind,
} from '~/types/provisioning'

/**
 * Cloud (or local) services that can host a preview app as a running instance.
 * These are Docker/container or SSH-Docker targets - not Kubernetes clusters.
 */
export type InstanceComputeTarget = {
  /** Stable id for UI selection (unique per provider catalog entry). */
  id: string
  kind: RunningInstanceKind
  /** Resource boolean to enable on the provider resources object (null for local/BYO). */
  resourceKey: string | null
  /** True when the service runs a container image (Docker OCI). */
  runsContainerImage: boolean
  icon: string
}

const LOCAL_TARGETS: InstanceComputeTarget[] = [
  {
    id: 'local_docker',
    kind: 'local_machine',
    resourceKey: null,
    runsContainerImage: true,
    icon: 'dock',
  },
  {
    id: 'local_vm_ssh',
    kind: 'vm',
    resourceKey: null,
    runsContainerImage: true,
    icon: 'dns',
  },
]

const GCP_TARGETS: InstanceComputeTarget[] = [
  {
    id: 'gcp_cloud_run',
    kind: 'serverless',
    resourceKey: 'cloud_run',
    runsContainerImage: true,
    icon: 'rocket_launch',
  },
  {
    id: 'gcp_vm_ssh',
    kind: 'vm',
    resourceKey: null,
    runsContainerImage: true,
    icon: 'dns',
  },
]

const AWS_TARGETS: InstanceComputeTarget[] = [
  {
    id: 'aws_app_runner',
    kind: 'serverless',
    resourceKey: 'app_runner',
    runsContainerImage: true,
    icon: 'rocket_launch',
  },
  {
    id: 'aws_ec2',
    kind: 'vm',
    resourceKey: 'ec2',
    runsContainerImage: true,
    icon: 'dns',
  },
]

const AZURE_TARGETS: InstanceComputeTarget[] = [
  {
    id: 'azure_container_apps',
    kind: 'serverless',
    resourceKey: 'container_apps',
    runsContainerImage: true,
    icon: 'rocket_launch',
  },
  {
    id: 'azure_vm_ssh',
    kind: 'vm',
    resourceKey: null,
    runsContainerImage: true,
    icon: 'dns',
  },
]

/** Cloudflare edge runtimes are not Docker instance hosts; BYO VM only. */
const CLOUDFLARE_TARGETS: InstanceComputeTarget[] = [
  {
    id: 'cloudflare_vm_ssh',
    kind: 'vm',
    resourceKey: null,
    runsContainerImage: true,
    icon: 'dns',
  },
]

export function instanceComputeTargetsForProvider(
  provider: CloudProvider,
): InstanceComputeTarget[] {
  if (provider === 'local') return LOCAL_TARGETS
  if (provider === 'gcp') return GCP_TARGETS
  if (provider === 'aws') return AWS_TARGETS
  if (provider === 'azure') return AZURE_TARGETS
  if (provider === 'cloudflare') return CLOUDFLARE_TARGETS
  return LOCAL_TARGETS
}

/** Resource keys that belong to instance-compute selection for a provider. */
export function instanceComputeResourceKeys(provider: CloudProvider): string[] {
  return instanceComputeTargetsForProvider(provider)
    .map((t) => t.resourceKey)
    .filter((key): key is string => Boolean(key))
}

export function resolveSelectedInstanceComputeTarget(
  provider: CloudProvider,
  runningInstance: RunningInstanceConfig,
  resources: Record<string, unknown> = {},
): InstanceComputeTarget | null {
  const targets = instanceComputeTargetsForProvider(provider)
  const withResource = targets.find(
    (t) => t.resourceKey && resources[t.resourceKey] === true && t.kind === runningInstance.kind,
  )
  if (withResource) return withResource

  const byKind = targets.find((t) => t.kind === runningInstance.kind)
  return byKind ?? targets[0] ?? null
}

/**
 * Select a compute target: set running-instance kind, enable its resource flag,
 * and clear other instance-compute flags for the provider (one winner).
 */
export function applyInstanceComputeTarget(input: {
  provider: CloudProvider
  targetId: string
  runningInstance: RunningInstanceConfig
  resources: Record<string, unknown>
}): RunningInstanceConfig {
  const targets = instanceComputeTargetsForProvider(input.provider)
  const target = targets.find((t) => t.id === input.targetId) ?? targets[0]
  if (!target) return input.runningInstance

  for (const key of instanceComputeResourceKeys(input.provider)) {
    input.resources[key] = key === target.resourceKey
  }
  if (target.resourceKey) {
    input.resources[target.resourceKey] = true
  }

  const regionFromCloud =
    typeof input.resources.region === 'string'
      ? input.resources.region
      : typeof input.resources.location === 'string'
        ? input.resources.location
        : null

  const next: RunningInstanceConfig = {
    ...input.runningInstance,
    kind: target.kind,
  }

  if (target.kind === 'serverless' && regionFromCloud && !next.region?.trim()) {
    next.region = regionFromCloud
  }

  return next
}

export function providerSupportsContainerServerless(provider: CloudProvider): boolean {
  return provider === 'gcp' || provider === 'aws' || provider === 'azure'
}

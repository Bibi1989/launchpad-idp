import type {
  CloudProvider,
  InstanceProcessStrategy,
  RunningInstanceConfig,
  RunningInstanceKind,
} from '~/types/provisioning'

/**
 * Cloud (or local) services that can host a preview app as a running instance.
 * Process strategy (docker / systemd / pm2) shapes how the host runs the app.
 */
export type InstanceComputeTarget = {
  /** Stable id for UI selection (unique per provider catalog entry). */
  id: string
  kind: RunningInstanceKind
  /** Resource boolean to enable on the provider resources object (null for local/BYO). */
  resourceKey: string | null
  /** True when the service runs a container image (Docker OCI) by default. */
  runsContainerImage: boolean
  icon: string
}

export type ComputeTargetDisplay = {
  titleKey: string
  descKey: string
  badgeKey: string
  icon: string
  /** Whether the badge should read as container vs native process. */
  usesContainer: boolean
}

export function resolveProcessStrategy(
  runningInstance: RunningInstanceConfig | null | undefined,
): InstanceProcessStrategy {
  if (runningInstance?.kind === 'serverless') return 'docker'
  return runningInstance?.process_strategy || 'docker'
}

/** i18n keys + icon for a compute card given the selected process strategy. */
export function computeTargetDisplay(
  target: InstanceComputeTarget,
  strategy: InstanceProcessStrategy = 'docker',
): ComputeTargetDisplay {
  if (target.kind === 'serverless') {
    return {
      titleKey: `provision.runtimeMode.attach.targets.${target.id}.title`,
      descKey: `provision.runtimeMode.attach.targets.${target.id}.desc`,
      badgeKey: 'provision.runtimeMode.attach.containerBadge',
      icon: target.icon,
      usesContainer: true,
    }
  }

  const effective: InstanceProcessStrategy =
    target.kind === 'serverless' ? 'docker' : strategy
  const scope = target.kind === 'local_machine' ? 'localByStrategy' : 'vmByStrategy'
  const icon =
    effective === 'pm2' ? 'bolt' : effective === 'systemd' ? 'terminal' : target.icon

  return {
    titleKey: `provision.runtimeMode.attach.${scope}.${effective}.title`,
    descKey: `provision.runtimeMode.attach.${scope}.${effective}.desc`,
    badgeKey: `provision.runtimeMode.attach.${scope}.${effective}.badge`,
    icon,
    usesContainer: effective === 'docker',
  }
}

/** Map instance process strategy → Ansible app_deploy_mode. */
export function ansibleDeployModeFromStrategy(
  strategy: InstanceProcessStrategy,
): 'docker_run' | 'systemd' | 'pm2' {
  if (strategy === 'systemd') return 'systemd'
  if (strategy === 'pm2') return 'pm2'
  return 'docker_run'
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
    resourceKey: 'compute_instance',
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

  if (target.kind === 'serverless') {
    next.process_strategy = 'docker'
    next.reverse_proxy = 'none'
    if (regionFromCloud && !next.region?.trim()) {
      next.region = regionFromCloud
    }
  }

  return next
}

export function providerSupportsContainerServerless(provider: CloudProvider): boolean {
  return provider === 'gcp' || provider === 'aws' || provider === 'azure'
}

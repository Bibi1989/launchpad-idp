import type {
  CostOptimizationConfig,
  CostResourceConfig,
  KubernetesWorkloadOptions,
  ResourceSizingPreset,
} from '~/types/provisioning'

/** CPU/mem request+limit tuples keyed by preset. */
export const RESOURCE_PRESETS: Record<
  Exclude<ResourceSizingPreset, 'custom'>,
  { cpuRequest: string; memoryRequest: string; cpuLimit: string; memoryLimit: string }
> = {
  developer: {
    cpuRequest: '100m',
    memoryRequest: '128Mi',
    cpuLimit: '250m',
    memoryLimit: '256Mi',
  },
  balanced: {
    cpuRequest: '250m',
    memoryRequest: '512Mi',
    cpuLimit: '500m',
    memoryLimit: '1Gi',
  },
  performance: {
    cpuRequest: '1',
    memoryRequest: '2Gi',
    cpuLimit: '2',
    memoryLimit: '4Gi',
  },
}

export function defaultCostOptimizationConfig(): CostOptimizationConfig {
  const lean = RESOURCE_PRESETS.developer
  return {
    spotScheduling: {
      enabled: false,
      placement: 'stateless_nonprod',
      allocationPercent: 80,
      provisioner: 'karpenter',
    },
    hpa: {
      enabled: false,
      minReplicas: 2,
      maxReplicas: 10,
      targetCpuUtilization: 70,
    },
    vpa: {
      enabled: false,
    },
    resources: {
      preset: 'developer',
      ...lean,
    },
    idleShutdown: {
      enabled: false,
      schedule: 'weeknights_weekends',
    },
  }
}

export function applyResourcePreset(
  preset: ResourceSizingPreset,
  current: CostResourceConfig,
): CostResourceConfig {
  if (preset === 'custom') {
    return { ...current, preset: 'custom' }
  }
  return { preset, ...RESOURCE_PRESETS[preset] }
}

/** Sync HPA/VPA object toggles when cost suite enables them. */
export function applyCostOptimizationToWorkloadOptions(
  options: KubernetesWorkloadOptions,
  cost: CostOptimizationConfig,
): KubernetesWorkloadOptions {
  return {
    ...options,
    hpa: options.hpa || cost.hpa.enabled,
    vpa: options.vpa || cost.vpa.enabled,
  }
}

/** API uses snake_case; UI uses camelCase. */
export function costOptimizationToApi(
  cost: CostOptimizationConfig,
): Record<string, unknown> {
  return {
    spot_scheduling: {
      enabled: cost.spotScheduling.enabled,
      placement: cost.spotScheduling.placement,
      allocation_percent: cost.spotScheduling.allocationPercent,
      provisioner: cost.spotScheduling.provisioner,
    },
    hpa: {
      enabled: cost.hpa.enabled,
      min_replicas: cost.hpa.minReplicas,
      max_replicas: cost.hpa.maxReplicas,
      target_cpu_utilization: cost.hpa.targetCpuUtilization,
    },
    vpa: {
      enabled: cost.vpa.enabled,
    },
    resources: {
      preset: cost.resources.preset,
      cpu_request: cost.resources.cpuRequest,
      cpu_limit: cost.resources.cpuLimit,
      memory_request: cost.resources.memoryRequest,
      memory_limit: cost.resources.memoryLimit,
    },
    idle_shutdown: {
      enabled: cost.idleShutdown.enabled,
      schedule: cost.idleShutdown.schedule,
    },
  }
}

export function costOptimizationFromApi(
  raw: Record<string, unknown> | null | undefined,
): CostOptimizationConfig {
  const base = defaultCostOptimizationConfig()
  if (!raw || typeof raw !== 'object') return base

  const spot = (raw.spot_scheduling ?? raw.spotScheduling) as Record<string, unknown> | undefined
  const hpa = raw.hpa as Record<string, unknown> | undefined
  const vpa = raw.vpa as Record<string, unknown> | undefined
  const resources = raw.resources as Record<string, unknown> | undefined
  const idle = (raw.idle_shutdown ?? raw.idleShutdown) as Record<string, unknown> | undefined

  return {
    spotScheduling: {
      enabled: Boolean(spot?.enabled ?? base.spotScheduling.enabled),
      placement:
        (spot?.placement as CostOptimizationConfig['spotScheduling']['placement'])
        ?? base.spotScheduling.placement,
      allocationPercent: Number(
        spot?.allocation_percent ?? spot?.allocationPercent ?? base.spotScheduling.allocationPercent,
      ),
      provisioner:
        (spot?.provisioner as CostOptimizationConfig['spotScheduling']['provisioner'])
        ?? base.spotScheduling.provisioner,
    },
    hpa: {
      enabled: Boolean(hpa?.enabled ?? base.hpa.enabled),
      minReplicas: Number(hpa?.min_replicas ?? hpa?.minReplicas ?? base.hpa.minReplicas),
      maxReplicas: Number(hpa?.max_replicas ?? hpa?.maxReplicas ?? base.hpa.maxReplicas),
      targetCpuUtilization: Number(
        hpa?.target_cpu_utilization ?? hpa?.targetCpuUtilization ?? base.hpa.targetCpuUtilization,
      ),
    },
    vpa: {
      enabled: Boolean(vpa?.enabled ?? base.vpa.enabled),
    },
    resources: {
      preset:
        (resources?.preset as ResourceSizingPreset) ?? base.resources.preset,
      cpuRequest: String(resources?.cpu_request ?? resources?.cpuRequest ?? base.resources.cpuRequest),
      cpuLimit: String(resources?.cpu_limit ?? resources?.cpuLimit ?? base.resources.cpuLimit),
      memoryRequest: String(
        resources?.memory_request ?? resources?.memoryRequest ?? base.resources.memoryRequest,
      ),
      memoryLimit: String(
        resources?.memory_limit ?? resources?.memoryLimit ?? base.resources.memoryLimit,
      ),
    },
    idleShutdown: {
      enabled: Boolean(idle?.enabled ?? base.idleShutdown.enabled),
      schedule:
        (idle?.schedule as CostOptimizationConfig['idleShutdown']['schedule'])
        ?? base.idleShutdown.schedule,
    },
  }
}

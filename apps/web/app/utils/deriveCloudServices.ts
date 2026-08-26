import type {
  CloudProvider,
  RunningInstanceConfig,
  WorkloadDependenciesConfig,
  WorkspaceRuntimeMode,
} from '~/types/provisioning'

/**
 * Smart derivation of cloud-service resource flags from the user's high-level
 * choices (runtime mode, compute target, database placement). This replaces the
 * manual cloud-service toggle step: the wizard collects intent, and this maps it
 * onto the exact resource booleans the backend keys on (has_managed_kubernetes /
 * has_serverless_runtime / has_vm_hint / validate_managed_dependencies).
 *
 * Only the keys returned here are "managed" by derivation. They are applied with
 * their explicit true/false value (see {@link applyDerivedCloudServices}), which
 * turns a service off when the choice that required it is cleared. Every other
 * key on the resources object (object storage, pub/sub, analytics, etc.) is left
 * untouched so the Advanced services panel can still add extras on top.
 */

export interface DeriveCloudServicesInput {
  provider: CloudProvider
  runtimeMode: WorkspaceRuntimeMode
  runningInstance?: RunningInstanceConfig | null
  dependencies?: WorkloadDependenciesConfig | null
  /** True when Launchpad builds an image for this workspace (needs a registry). */
  buildsImages?: boolean
}

type ResourceMap = Record<string, boolean | string>

const CLUSTER_KEY: Partial<Record<CloudProvider, string>> = {
  gcp: 'gke',
  aws: 'eks',
  azure: 'aks',
}

const SERVERLESS_KEY: Partial<Record<CloudProvider, string>> = {
  gcp: 'cloud_run',
  aws: 'app_runner',
  azure: 'container_apps',
}

/** VM compute resource key (Azure/Cloudflare VMs are BYO, so no flag). */
const VM_KEY: Partial<Record<CloudProvider, string>> = {
  gcp: 'compute_instance',
  aws: 'ec2',
}

const REGISTRY_KEY: Partial<Record<CloudProvider, string>> = {
  gcp: 'artifact_registry',
  aws: 'ecr',
  azure: 'acr',
}

const NETWORK_KEYS: Partial<Record<CloudProvider, string[]>> = {
  gcp: ['vpc', 'subnets'],
  aws: ['vpc', 'subnets'],
  azure: ['vnet', 'subnets'],
}

/** The full set of resource keys derivation owns for a provider (all set every run). */
export function derivedCloudServiceKeys(provider: CloudProvider): string[] {
  const keys = new Set<string>()
  for (const map of [CLUSTER_KEY, SERVERLESS_KEY, VM_KEY, REGISTRY_KEY]) {
    const k = map[provider]
    if (k) keys.add(k)
  }
  for (const k of NETWORK_KEYS[provider] ?? []) keys.add(k)
  // Database service flags are conditionally owned (see below).
  for (const k of ['cloud_sql', 'rds', 'cosmos_db', 'memorystore', 'elasticache', 'redis_cache']) {
    keys.add(k)
  }
  return [...keys]
}

function isManaged(dep: { enabled?: boolean; placement?: string } | undefined): boolean {
  return Boolean(dep?.enabled) && dep?.placement === 'managed'
}

/** Pick the single SQL engine for a managed relational service (one engine per instance). */
function managedSqlEngine(deps: WorkloadDependenciesConfig): 'postgres' | 'mysql' | null {
  if (isManaged(deps.postgres)) return 'postgres'
  if (isManaged(deps.mysql)) return 'mysql'
  return null
}

export function deriveCloudServiceResources(input: DeriveCloudServicesInput): ResourceMap {
  const { provider, runtimeMode } = input
  const out: ResourceMap = {}
  if (provider === 'local' || provider === 'cloudflare') {
    // Local has no managed cloud services; Cloudflare uses its own edge catalog
    // (Workers/Pages/D1/KV) that is not runtime-derived.
    return out
  }

  const deps = input.dependencies
  const buildsImages = input.buildsImages ?? true

  // Baseline network + registry so images can be pushed and services reached.
  for (const k of NETWORK_KEYS[provider] ?? []) out[k] = true
  if (REGISTRY_KEY[provider]) out[REGISTRY_KEY[provider] as string] = buildsImages

  // Compute: kubernetes -> managed cluster; running_instance -> serverless/vm.
  const clusterKey = CLUSTER_KEY[provider]
  const serverlessKey = SERVERLESS_KEY[provider]
  const vmKey = VM_KEY[provider]
  if (clusterKey) out[clusterKey] = runtimeMode === 'kubernetes'
  if (runtimeMode === 'running_instance') {
    const kind = input.runningInstance?.kind
    if (serverlessKey) out[serverlessKey] = kind === 'serverless'
    if (vmKey) out[vmKey] = kind === 'vm'
  } else {
    if (serverlessKey) out[serverlessKey] = false
    if (vmKey) out[vmKey] = false
  }

  // Managed databases -> the matching managed cloud service + engine.
  const sqlEngine = deps ? managedSqlEngine(deps) : null
  if (provider === 'gcp') {
    out.cloud_sql = sqlEngine === 'postgres' || sqlEngine === 'mysql'
    if (out.cloud_sql) out.cloud_sql_engine = sqlEngine as string
    out.memorystore = deps ? isManaged(deps.redis) : false
    if (out.memorystore) out.memorystore_engine = 'redis'
  } else if (provider === 'aws') {
    out.rds = Boolean(sqlEngine)
    if (out.rds) out.rds_engine = sqlEngine as string
    out.elasticache = deps ? isManaged(deps.redis) : false
    if (out.elasticache) out.elasticache_engine = 'redis'
  } else if (provider === 'azure') {
    out.cosmos_db = deps ? isManaged(deps.mongodb) : false
    if (out.cosmos_db) out.cosmos_api = 'mongodb'
    out.redis_cache = deps ? isManaged(deps.redis) : false
  }

  return out
}

/**
 * Apply derived resource flags onto a resources object in place. Managed keys are
 * set to their exact derived value; unmanaged keys (advanced overrides) are kept.
 */
export function applyDerivedCloudServices(
  resources: Record<string, unknown>,
  input: DeriveCloudServicesInput,
): void {
  const derived = deriveCloudServiceResources(input)
  for (const [key, value] of Object.entries(derived)) {
    resources[key] = value
  }
}

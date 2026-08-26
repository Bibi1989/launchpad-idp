import type { CloudProviderCatalogEntry } from '~/types/cloudProviders'
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'

export const TYPED_PARENT_CLOUDS = ['gcp', 'aws', 'azure', 'cloudflare'] as const
export type TypedParentCloud = (typeof TYPED_PARENT_CLOUDS)[number]

export const ACCOUNT_PARENT_CLOUDS = [
  ...TYPED_PARENT_CLOUDS,
  'hetzner',
  'digitalocean',
  'linode',
  'railway',
  'render',
] as const

export const TYPED_STATUS_KEY: Record<TypedParentCloud, keyof UserCloudCredentialsStatus> = {
  gcp: 'has_gcp',
  aws: 'has_aws',
  azure: 'has_azure',
  cloudflare: 'has_cloudflare',
}

type CatalogLike = {
  id: string
  parent_cloud?: string | null
}

export function isTypedParentCloud(id: string): id is TypedParentCloud {
  return (TYPED_PARENT_CLOUDS as readonly string[]).includes(id)
}

export function isLegacyCloudId(id: string): boolean {
  return id.endsWith('-legacy')
}

export function parentCloudOf(entryOrId: string | CatalogLike): string {
  const entry = typeof entryOrId === 'string' ? { id: entryOrId } : entryOrId
  const explicit = (entry.parent_cloud || '').trim().toLowerCase()
  if (explicit) return explicit
  const pid = entry.id.replace(/-legacy$/, '').toLowerCase()
  for (const parent of [...ACCOUNT_PARENT_CLOUDS].sort((a, b) => b.length - a.length)) {
    if (pid === parent) return parent
    if (pid.startsWith(`${parent}-`)) return parent
  }
  return pid
}

export function isUmbrellaAccount(entry: CatalogLike, catalog: CloudProviderCatalogEntry[]): boolean {
  if (entry.parent_cloud) return false
  return catalog.some((item) => item.parent_cloud === entry.id)
}

export function isDeployPluginEntry(entry: CloudProviderCatalogEntry, catalog: CloudProviderCatalogEntry[]): boolean {
  if (isLegacyCloudId(entry.id)) return false
  if (isUmbrellaAccount(entry, catalog)) return false
  return true
}

export function pluginIsConnected(
  entry: CatalogLike,
  typedStatus: UserCloudCredentialsStatus | null,
  pluginConnected: Record<string, boolean>,
): boolean {
  const parent = parentCloudOf(entry)
  if (isTypedParentCloud(parent)) {
    return Boolean(typedStatus?.[TYPED_STATUS_KEY[parent]])
  }
  return Boolean(pluginConnected[parent] || pluginConnected[entry.id])
}

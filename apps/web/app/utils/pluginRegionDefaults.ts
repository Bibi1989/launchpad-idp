import type { CloudProviderCatalogEntry } from '~/types/cloudProviders'
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'
import {
  coerceRegionForProvider,
  defaultRegionForProvider,
  isRegionForProvider,
} from '~/utils/cloudRegions'
import { isTypedParentCloud, parentCloudOf } from '~/utils/pluginParentCloud'

export function preferredRegionFromVault(
  provider: string,
  status: UserCloudCredentialsStatus | null | undefined,
): string | null {
  if (!status || !isTypedParentCloud(provider)) return null
  let raw: string | null = null
  if (provider === 'gcp') raw = (status.gcp_region || '').trim() || null
  else if (provider === 'aws') raw = (status.aws_region || '').trim() || null
  else if (provider === 'azure') raw = (status.azure_location || '').trim() || null
  if (!raw) return null
  return isRegionForProvider(provider, raw) ? raw : null
}

/** Default region when a cloud plugin tile/card is selected. */
export function defaultRegionForPluginEntry(
  entry: CloudProviderCatalogEntry,
  status: UserCloudCredentialsStatus | null | undefined,
): string | null {
  const parent = parentCloudOf(entry)
  const preferred = isTypedParentCloud(parent)
    ? preferredRegionFromVault(parent, status)
    : null
  const catalogValues = entry.regions.map((r) => r.value)
  if (preferred && catalogValues.includes(preferred)) {
    return preferred
  }
  if (preferred && isTypedParentCloud(parent)) {
    const coerced = coerceRegionForProvider(parent, preferred, preferred)
    if (catalogValues.includes(coerced)) return coerced
  }
  const first = entry.regions[0]?.value
  if (first) return first
  if (isTypedParentCloud(parent)) return defaultRegionForProvider(parent)
  return null
}

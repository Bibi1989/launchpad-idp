import { describe, expect, it } from 'vitest'
import { defaultRegionForPluginEntry } from '~/utils/pluginRegionDefaults'
import type { CloudProviderCatalogEntry } from '~/types/cloudProviders'
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'

const gkeEntry: CloudProviderCatalogEntry = {
  id: 'gcp-gke',
  label: 'Google GKE',
  parent_cloud: 'gcp',
  regions: [
    { value: 'us-central1', label: 'Iowa' },
    { value: 'europe-west3', label: 'Frankfurt' },
  ],
  tiers: [],
  runtime_targets: ['kubernetes'],
}

describe('defaultRegionForPluginEntry', () => {
  it('uses vault preferred region when listed on the plugin', () => {
    const status: UserCloudCredentialsStatus = {
      has_gcp: true,
      has_aws: false,
      has_azure: false,
      has_cloudflare: false,
      gcp_region: 'europe-west3',
    }
    expect(defaultRegionForPluginEntry(gkeEntry, status)).toBe('europe-west3')
  })

  it('falls back to first catalog region without vault preference', () => {
    expect(defaultRegionForPluginEntry(gkeEntry, null)).toBe('us-central1')
  })
})

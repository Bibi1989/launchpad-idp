import {
  cloudProviderCatalogSchema,
  type CloudProviderCatalog,
  type CloudProviderCatalogEntry,
} from '~/types/cloudProviders'

/**
 * Loads the multi-cloud provider catalog from the backend registry
 * (GET /api/v1/cloud-providers) and exposes it as the single source of truth for
 * which providers exist and what credential fields / regions / tiers each needs.
 *
 * Adding a provider plugin on the backend automatically surfaces it here - no
 * frontend change required.
 */
export function useCloudProviders() {
  const { apiFetch } = useApi()

  const catalog = useState<CloudProviderCatalog>('cloud-provider-catalog', () => [])
  const loading = useState<boolean>('cloud-provider-catalog-loading', () => false)
  const error = useState<string | null>('cloud-provider-catalog-error', () => null)
  const loaded = useState<boolean>('cloud-provider-catalog-loaded', () => false)

  async function load(force = false): Promise<CloudProviderCatalog> {
    if (loaded.value && !force) return catalog.value
    loading.value = true
    error.value = null
    try {
      const raw = await apiFetch<unknown>('/cloud-providers')
      catalog.value = cloudProviderCatalogSchema.parse(raw)
      loaded.value = true
      return catalog.value
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load cloud providers'
      return catalog.value
    } finally {
      loading.value = false
    }
  }

  function getProvider(id: string | null | undefined): CloudProviderCatalogEntry | null {
    if (!id) return null
    return catalog.value.find((p) => p.id === id) ?? null
  }

  /** Providers that can host a container workload (VM / docker host / kubernetes). */
  const vmProviders = computed(() =>
    catalog.value.filter((p) =>
      p.runtime_targets.some((t) => t === 'vm' || t === 'docker_host' || t === 'kubernetes'),
    ),
  )

  /** Providers that run managed platforms (Railway, Cloudflare, ...). */
  const paasProviders = computed(() =>
    catalog.value.filter((p) => p.runtime_targets.includes('paas')),
  )

  return {
    catalog,
    loading,
    error,
    loaded,
    load,
    getProvider,
    vmProviders,
    paasProviders,
  }
}

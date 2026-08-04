import type {
  CatalogService,
  CatalogServiceCreatePayload,
  CatalogServiceUpdatePayload,
  GoldenPathTemplate,
} from '~/types/catalog'

export function useCatalog() {
  const { apiFetch } = useApi()

  async function listTemplates(): Promise<GoldenPathTemplate[]> {
    return apiFetch<GoldenPathTemplate[]>('/catalog/templates')
  }

  async function listServices(): Promise<CatalogService[]> {
    return apiFetch<CatalogService[]>('/catalog/services')
  }

  async function getService(id: string): Promise<CatalogService> {
    return apiFetch<CatalogService>(`/catalog/services/${id}`)
  }

  async function createService(payload: CatalogServiceCreatePayload): Promise<CatalogService> {
    return apiFetch<CatalogService>('/catalog/services', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async function updateService(
    id: string,
    payload: CatalogServiceUpdatePayload,
  ): Promise<CatalogService> {
    return apiFetch<CatalogService>(`/catalog/services/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  }

  async function deleteService(id: string): Promise<void> {
    await apiFetch<void>(`/catalog/services/${id}`, {
      method: 'DELETE',
    })
  }

  return {
    listTemplates,
    listServices,
    getService,
    createService,
    updateService,
    deleteService,
  }
}

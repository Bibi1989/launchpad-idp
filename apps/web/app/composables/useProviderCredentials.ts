import {
  providerCredentialsStatusSchema,
  providerValidateResponseSchema,
  type ProviderCredentialsStatus,
  type ProviderValidateResponse,
} from '~/types/cloudProviders'

/**
 * Save / inspect / validate per-provider credentials for the plugin cloud engine.
 * Backed by the encrypted provider_credentials vault
 * (GET/PUT/DELETE /api/v1/cloud-providers/{id}/credentials, POST .../validate).
 * The status endpoint returns only which fields are set - never the secret values.
 */
export function useProviderCredentials() {
  const { apiFetch } = useApi()

  const saving = useState<boolean>('provider-cred-saving', () => false)
  const validating = useState<boolean>('provider-cred-validating', () => false)
  const error = useState<string | null>('provider-cred-error', () => null)

  async function getStatus(providerId: string): Promise<string[]> {
    const raw = await apiFetch<unknown>(`/cloud-providers/${providerId}/credentials`)
    const parsed = providerCredentialsStatusSchema.parse(raw)
    return parsed[providerId] ?? []
  }

  async function save(
    providerId: string,
    credentials: Record<string, string>,
  ): Promise<ProviderCredentialsStatus> {
    saving.value = true
    error.value = null
    try {
      const raw = await apiFetch<unknown>(`/cloud-providers/${providerId}/credentials`, {
        method: 'PUT',
        body: JSON.stringify({ credentials }),
      })
      return providerCredentialsStatusSchema.parse(raw)
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to save credentials'
      throw err
    } finally {
      saving.value = false
    }
  }

  async function remove(providerId: string): Promise<ProviderCredentialsStatus> {
    const raw = await apiFetch<unknown>(`/cloud-providers/${providerId}/credentials`, {
      method: 'DELETE',
    })
    return providerCredentialsStatusSchema.parse(raw)
  }

  async function validate(
    providerId: string,
    credentials?: Record<string, string>,
  ): Promise<ProviderValidateResponse> {
    validating.value = true
    error.value = null
    try {
      const raw = await apiFetch<unknown>(`/cloud-providers/${providerId}/validate`, {
        method: 'POST',
        body: JSON.stringify({ credentials: credentials ?? null }),
      })
      return providerValidateResponseSchema.parse(raw)
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Validation failed'
      return { valid: false, message: error.value }
    } finally {
      validating.value = false
    }
  }

  return { saving, validating, error, getStatus, save, remove, validate }
}

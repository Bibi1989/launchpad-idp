import type { UserCloudCredentialsStatus } from '~/types/userCredentials'
import type { CloudCredentialsForm } from '~/utils/cloudValidation'
import { emptyCloudCredentials } from '~/utils/cloudValidation'

export function useUserCloudCredentials() {
  const { apiFetch } = useApi()

  async function getStatus(): Promise<UserCloudCredentialsStatus> {
    return apiFetch<UserCloudCredentialsStatus>('/users/me/cloud-credentials')
  }

  async function save(credentials: CloudCredentialsForm, clear?: {
    clear_gcp?: boolean
    clear_aws?: boolean
    clear_azure?: boolean
    clear_cloudflare?: boolean
  }): Promise<UserCloudCredentialsStatus> {
    return apiFetch<UserCloudCredentialsStatus>('/users/me/cloud-credentials', {
      method: 'PUT',
      body: {
        credentials,
        clear_gcp: clear?.clear_gcp ?? false,
        clear_aws: clear?.clear_aws ?? false,
        clear_azure: clear?.clear_azure ?? false,
        clear_cloudflare: clear?.clear_cloudflare ?? false,
      },
    })
  }

  async function clearAll(): Promise<UserCloudCredentialsStatus> {
    return apiFetch<UserCloudCredentialsStatus>('/users/me/cloud-credentials', {
      method: 'DELETE',
    })
  }

  return {
    getStatus,
    save,
    clearAll,
    emptyCloudCredentials,
  }
}

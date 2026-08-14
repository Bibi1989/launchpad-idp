import type { UserCloudCredentialsStatus } from '~/types/userCredentials'
import type { CloudCredentialsForm } from '~/utils/cloudValidation'
import { emptyCloudCredentials } from '~/utils/cloudValidation'

export type CloudOAuthProvider = 'gcp' | 'aws' | 'azure'

export type CloudOAuthCapabilities = {
  gcp: boolean
  aws: boolean
  azure: boolean
  note: string
}

export type CloudOAuthSessionStatus = {
  session_id: string
  provider: CloudOAuthProvider
  status: 'pending' | 'succeeded' | 'failed'
  message?: string | null
  email?: string | null
  label?: string | null
}

export type CloudOAuthStartPayload = {
  provider: CloudOAuthProvider
  aws_start_url?: string
  aws_region?: string
  aws_account_id?: string
  aws_role_name?: string
  azure_tenant_id?: string
  azure_subscription_id?: string
}

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

  async function listNetworks(params: {
    provider: 'gcp' | 'aws' | 'azure' | 'cloudflare'
    region?: string | null
  }): Promise<{
    provider: string
    region?: string | null
    networks: Array<{
      id: string
      name: string
      cidr?: string | null
      is_default?: boolean
      region?: string | null
    }>
  }> {
    const query = new URLSearchParams({ provider: params.provider })
    const region = (params.region || '').trim()
    if (region) query.set('region', region)
    return apiFetch(`/users/me/cloud-credentials/networks?${query.toString()}`)
  }

  async function listSecurityGroups(params: {
    provider: 'aws'
    region?: string | null
    vpc_id?: string | null
  }): Promise<{
    provider: string
    region?: string | null
    vpc_id?: string | null
    security_groups: Array<{
      id: string
      name: string
      vpc_id?: string | null
      description?: string | null
      region?: string | null
    }>
  }> {
    const query = new URLSearchParams({ provider: params.provider })
    const region = (params.region || '').trim()
    const vpcId = (params.vpc_id || '').trim()
    if (region) query.set('region', region)
    if (vpcId) query.set('vpc_id', vpcId)
    return apiFetch(`/users/me/cloud-credentials/security-groups?${query.toString()}`)
  }

  async function oauthCapabilities(): Promise<CloudOAuthCapabilities> {
    return apiFetch<CloudOAuthCapabilities>('/users/me/cloud-credentials/oauth/capabilities')
  }

  async function startOAuth(payload: CloudOAuthStartPayload): Promise<CloudOAuthSessionStatus> {
    return apiFetch<CloudOAuthSessionStatus>('/users/me/cloud-credentials/oauth/start', {
      method: 'POST',
      body: payload,
    })
  }

  async function getOAuthSession(sessionId: string): Promise<CloudOAuthSessionStatus> {
    return apiFetch<CloudOAuthSessionStatus>(
      `/users/me/cloud-credentials/oauth/sessions/${encodeURIComponent(sessionId)}`,
    )
  }

  async function connectWithBrowser(
    payload: CloudOAuthStartPayload,
    options?: { pollMs?: number; timeoutMs?: number },
  ): Promise<CloudOAuthSessionStatus> {
    const pollMs = options?.pollMs ?? 2000
    const timeoutMs = options?.timeoutMs ?? 200_000
    const started = await startOAuth(payload)
    const deadline = Date.now() + timeoutMs
    let current = started
    while (current.status === 'pending' && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, pollMs))
      current = await getOAuthSession(started.session_id)
    }
    return current
  }

  return {
    getStatus,
    save,
    clearAll,
    listNetworks,
    listSecurityGroups,
    emptyCloudCredentials,
    oauthCapabilities,
    startOAuth,
    getOAuthSession,
    connectWithBrowser,
  }
}

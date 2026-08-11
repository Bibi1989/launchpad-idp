import type {
  AuditLogEntry,
  Environment,
  EnvironmentCreatePayload,
  EnvironmentExtendPayload,
  EnvironmentPromotePayload,
  KindClusterStatus,
  KindClusterActionResult,
  PreviewAppTemplate,
  PreviewBuildStatus,
  PreviewLaunchPayload,
} from '~/types/environment'
import type { EnvironmentCreateInput } from '~/utils/validation'

export function useEnvironments() {
  const { apiFetch } = useApi()
  const environments = useState<Environment[]>('environments', () => [])
  const loading = useState<boolean>('environments-loading', () => false)
  const error = useState<string | null>('environments-error', () => null)

  async function refresh() {
    // Re-entrant safe: concurrent callers share one in-flight flag via finally.
    loading.value = true
    error.value = null
    try {
      environments.value = await apiFetch<Environment[]>('/environments')
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load environments'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function create(payload: EnvironmentCreateInput): Promise<Environment> {
    const body: EnvironmentCreatePayload = {
      name: payload.name,
      git_branch: payload.git_branch,
      git_repo_url: payload.git_repo_url,
      workspace_id: payload.workspace_id ?? null,
    }
    if (payload.ttl_minutes != null) {
      body.ttl_minutes = payload.ttl_minutes
    } else if (payload.ttl_hours != null) {
      body.ttl_hours = payload.ttl_hours
    }
    const result = await apiFetch<Environment>('/environments', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    await refresh()
    return result
  }

  async function listPreviewTemplates(): Promise<PreviewAppTemplate[]> {
    return apiFetch<PreviewAppTemplate[]>('/preview/templates')
  }

  async function getKindStatus(): Promise<KindClusterStatus> {
    return apiFetch<KindClusterStatus>('/preview/kind/status')
  }

  async function ensureKindCluster(clusterName?: string | null): Promise<KindClusterActionResult> {
    return apiFetch<KindClusterActionResult>('/preview/kind/up', {
      method: 'POST',
      body: JSON.stringify(clusterName ? { cluster_name: clusterName } : {}),
      timeoutMs: 270_000,
    })
  }

  async function deleteKindCluster(clusterName?: string | null): Promise<KindClusterActionResult> {
    return apiFetch<KindClusterActionResult>('/preview/kind/down', {
      method: 'POST',
      body: JSON.stringify(clusterName ? { cluster_name: clusterName } : {}),
      timeoutMs: 150_000,
    })
  }

  async function getPreviewBuildStatus(): Promise<PreviewBuildStatus> {
    return apiFetch<PreviewBuildStatus>('/preview/build/status')
  }

  async function listAudits(environmentId: string, limit = 50): Promise<AuditLogEntry[]> {
    return apiFetch<AuditLogEntry[]>(
      `/environments/${environmentId}/audits?limit=${limit}`,
    )
  }

  async function scanDrift(id: string): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/drift-scan`, {
      method: 'POST',
    })
    await refresh()
    return environment
  }

  async function launchPreview(payload: PreviewLaunchPayload): Promise<Environment> {
    const result = await apiFetch<Environment>('/preview/launch', {
      method: 'POST',
      body: JSON.stringify(payload),
      // Local first create can still be slow if other sync work runs; keep headroom.
      timeoutMs: 90_000,
    })
    await refresh()
    return result
  }

  async function getById(id: string): Promise<Environment> {
    return apiFetch<Environment>(`/environments/${id}`)
  }

  async function destroy(
    id: string,
    opts: { force?: boolean } = {},
  ): Promise<Environment> {
    const query = opts.force ? '?force=true' : ''
    const environment = await apiFetch<Environment>(`/environments/${id}${query}`, {
      method: 'DELETE',
    })
    await refresh()
    return environment
  }

  async function cancelProvision(id: string): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/cancel-provision`, {
      method: 'POST',
    })
    await refresh()
    return environment
  }

  async function retryProvision(id: string): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/retry`, {
      method: 'POST',
    })
    await refresh()
    return environment
  }

  async function extendTtl(
    id: string,
    payload: EnvironmentExtendPayload = {},
  ): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/extend`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    await refresh()
    return environment
  }

  async function promoteToCloud(
    id: string,
    payload: EnvironmentPromotePayload,
  ): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/promote`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    await refresh()
    return environment
  }

  async function pauseEnvironment(id: string): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/pause`, {
      method: 'POST',
    })
    await refresh()
    return environment
  }

  async function resumeEnvironment(id: string): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/resume`, {
      method: 'POST',
    })
    await refresh()
    return environment
  }

  async function relaunchEnvironment(id: string): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/relaunch`, {
      method: 'POST',
    })
    await refresh()
    return environment
  }

  return {
    environments,
    loading,
    error,
    refresh,
    create,
    listPreviewTemplates,
    getKindStatus,
    ensureKindCluster,
    deleteKindCluster,
    getPreviewBuildStatus,
    listAudits,
    scanDrift,
    launchPreview,
    getById,
    destroy,
    cancelProvision,
    retryProvision,
    extendTtl,
    promoteToCloud,
    pauseEnvironment,
    resumeEnvironment,
    relaunchEnvironment,
  }
}

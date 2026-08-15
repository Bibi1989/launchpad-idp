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
  let refreshInFlight = 0

  async function refresh(opts: { soft?: boolean } = {}) {
    // Soft refresh keeps existing cards visible (destroy/teardown must not
    // blank the whole environments page behind AppSplash).
    refreshInFlight += 1
    if (!opts.soft || environments.value.length === 0) {
      loading.value = true
    }
    error.value = null
    try {
      environments.value = await apiFetch<Environment[]>('/environments')
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load environments'
      throw err
    } finally {
      refreshInFlight -= 1
      if (refreshInFlight <= 0) {
        refreshInFlight = 0
        loading.value = false
      }
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
    await refresh({ soft: true })
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
    await refresh({ soft: true })
    return environment
  }

  async function launchPreview(payload: PreviewLaunchPayload): Promise<Environment> {
    const result = await apiFetch<Environment>('/preview/launch', {
      method: 'POST',
      body: JSON.stringify(payload),
      // Local first create can still be slow if other sync work runs; keep headroom.
      timeoutMs: 90_000,
    })
    await refresh({ soft: true })
    return result
  }

  async function getById(id: string): Promise<Environment> {
    return apiFetch<Environment>(`/environments/${id}`)
  }

  function patchEnvironment(environment: Environment) {
    const idx = environments.value.findIndex((item) => item.id === environment.id)
    if (idx >= 0) {
      environments.value[idx] = environment
    }
  }

  async function destroy(
    id: string,
    opts: { force?: boolean } = {},
  ): Promise<Environment> {
    const query = opts.force ? '?force=true' : ''
    const existing = environments.value.find((item) => item.id === id)
    const previous = existing ? { ...existing } : null
    // Optimistic: drop from live lists immediately while teardown runs in background.
    environments.value = environments.value.filter((item) => item.id !== id)
    try {
      const environment = await apiFetch<Environment>(`/environments/${id}${query}`, {
        method: 'DELETE',
        // Enqueue-only; should be fast. Don't wait forever if the API is busy.
        timeoutMs: 30_000,
      })
      void refresh({ soft: true }).catch(() => undefined)
      return environment
    } catch (err) {
      if (previous) {
        environments.value = [previous, ...environments.value]
      }
      throw err
    }
  }

  async function cancelProvision(id: string): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/cancel-provision`, {
      method: 'POST',
    })
    patchEnvironment(environment)
    void refresh({ soft: true }).catch(() => undefined)
    return environment
  }

  async function retryProvision(id: string): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/retry`, {
      method: 'POST',
    })
    patchEnvironment(environment)
    void refresh({ soft: true }).catch(() => undefined)
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
    patchEnvironment(environment)
    void refresh({ soft: true }).catch(() => undefined)
    return environment
  }

  async function promoteToCloud(
    id: string,
    payload: EnvironmentPromotePayload,
  ): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/promote`, {
      method: 'POST',
      body: JSON.stringify(payload),
      // Promote may do extra work (loading a stored workspace snapshot) to
      // preserve runtime/deploy mode and credentials behavior. Allow enough
      // time for the control-plane to enqueue the preview.
      timeoutMs: 60_000,
    })
    await refresh({ soft: true })
    return environment
  }

  async function pauseEnvironment(id: string): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/pause`, {
      method: 'POST',
    })
    patchEnvironment(environment)
    void refresh({ soft: true }).catch(() => undefined)
    return environment
  }

  async function resumeEnvironment(id: string): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/resume`, {
      method: 'POST',
    })
    patchEnvironment(environment)
    void refresh({ soft: true }).catch(() => undefined)
    return environment
  }

  async function relaunchEnvironment(id: string): Promise<Environment> {
    const environment = await apiFetch<Environment>(`/environments/${id}/relaunch`, {
      method: 'POST',
    })
    patchEnvironment(environment)
    void refresh({ soft: true }).catch(() => undefined)
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

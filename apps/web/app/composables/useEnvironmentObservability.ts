import type {
  EnvironmentHealthPing,
  EnvironmentMetrics,
  EnvironmentObservabilitySummary,
} from '~/types/observability'

export function useEnvironmentObservability() {
  const { apiFetch } = useApi()

  function fetchSummary(limit = 24): Promise<EnvironmentObservabilitySummary> {
    return apiFetch<EnvironmentObservabilitySummary>(
      `/environments/observability/summary?limit=${limit}`,
    )
  }

  function fetchMetrics(environmentId: string): Promise<EnvironmentMetrics> {
    return apiFetch<EnvironmentMetrics>(`/environments/${environmentId}/metrics`)
  }

  function pingHealth(environmentId: string): Promise<EnvironmentHealthPing> {
    return apiFetch<EnvironmentHealthPing>(`/environments/${environmentId}/health-ping`, {
      method: 'POST',
    })
  }

  function shellWsPath(environmentId: string, mode?: 'ssh' | 'kubectl'): string {
    const q = mode ? `?mode=${encodeURIComponent(mode)}` : ''
    return `/api/v1/ws/environments/${environmentId}/shell${q}`
  }

  return {
    fetchSummary,
    fetchMetrics,
    pingHealth,
    shellWsPath,
  }
}

import type {
  K8sClusterContext,
  K8sDescribeMetadata,
  K8sPipelineStage,
  K8sResource,
} from '~/types/k8s'

export function useKubernetesSuite() {
  const { apiFetch } = useApi()

  async function getClusterContext(workspaceId: string): Promise<K8sClusterContext> {
    return apiFetch<K8sClusterContext>(`/workspaces/${workspaceId}/k8s/context`)
  }

  async function getResources(
    workspaceId: string,
    namespace?: string,
  ): Promise<K8sResource[]> {
    const qs = namespace ? `?namespace=${encodeURIComponent(namespace)}` : ''
    return apiFetch<K8sResource[]>(
      `/workspaces/${workspaceId}/k8s/resources${qs}`,
    )
  }

  async function deleteResource(
    workspaceId: string,
    kind: string,
    name: string,
    namespace?: string,
  ): Promise<{ success: boolean; message: string }> {
    return apiFetch<{ success: boolean; message: string }>(
      `/workspaces/${workspaceId}/k8s/resource`,
      {
        method: 'DELETE',
        body: JSON.stringify({ kind, name, namespace }),
      },
    )
  }

  async function describeResource(
    workspaceId: string,
    kind: string,
    name: string,
    namespace?: string,
  ): Promise<K8sDescribeMetadata> {
    const params = new URLSearchParams({ kind, name })
    if (namespace) params.set('namespace', namespace)
    return apiFetch<K8sDescribeMetadata>(
      `/workspaces/${workspaceId}/k8s/describe?${params.toString()}`,
    )
  }

  function getExecWsUrl(
    workspaceId: string,
    podName?: string,
    containerName?: string,
    namespace?: string,
  ): string {
    const config = useRuntimeConfig()
    const tokenState = useState<string | null>('auth-token')
    const token
      = tokenState.value
      || (typeof window !== 'undefined' ? localStorage.getItem('launchpad_access_token') : '')
    const params = new URLSearchParams()
    if (token) params.set('token', token)
    if (podName) params.set('pod', podName)
    if (containerName) params.set('container', containerName)
    if (namespace) params.set('namespace', namespace)
    const path = `/api/v1/ws/k8s/exec/${workspaceId}?${params.toString()}`

    // Prefer direct API WebSocket — Nuxt/Vite proxies often drop WS upgrades.
    const configured = String(config.public.wsBase || '').replace(/\/$/, '')
    if (configured) {
      return `${configured}${path}`
    }

    const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = typeof window !== 'undefined' ? window.location.host : 'localhost:8000'
    return `${protocol}//${host}${path}`
  }

  return {
    getClusterContext,
    getResources,
    deleteResource,
    describeResource,
    getExecWsUrl,
  }
}

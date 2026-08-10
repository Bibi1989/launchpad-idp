import type {
  NodeCommandRequest,
  NodeCommandResult,
  NodeEnrollPayload,
  NodeInstallInstructions,
  NodeRead,
} from '~/types/nodes'

/**
 * Client for the hybrid agent-node control plane. Shared list state lives in
 * `useState` so the fleet panel and any dashboard widgets stay in sync.
 */
export function useNodes() {
  const { apiFetch } = useApi()
  const nodes = useState<NodeRead[]>('agent-nodes', () => [])
  const loading = useState<boolean>('agent-nodes-loading', () => false)
  const error = useState<string | null>('agent-nodes-error', () => null)

  async function refresh(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      nodes.value = await apiFetch<NodeRead[]>('/nodes')
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load nodes'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function enroll(payload: NodeEnrollPayload): Promise<NodeInstallInstructions> {
    const result = await apiFetch<NodeInstallInstructions>('/nodes', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    await refresh()
    return result
  }

  async function get(nodeId: string): Promise<NodeRead> {
    return apiFetch<NodeRead>(`/nodes/${nodeId}`)
  }

  async function revoke(nodeId: string): Promise<void> {
    await apiFetch<void>(`/nodes/${nodeId}`, { method: 'DELETE' })
    nodes.value = nodes.value.filter((n) => n.id !== nodeId)
    await refresh()
  }

  async function dispatchCommand(
    nodeId: string,
    request: NodeCommandRequest,
  ): Promise<NodeCommandResult> {
    return apiFetch<NodeCommandResult>(`/nodes/${nodeId}/commands`, {
      method: 'POST',
      body: JSON.stringify(request),
    })
  }

  return { nodes, loading, error, refresh, enroll, get, revoke, dispatchCommand }
}

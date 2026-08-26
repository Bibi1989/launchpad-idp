import {
  scaffoldFilesSchema,
  type ProvisioningSpecInput,
  type ScaffoldFile,
} from '~/types/cloudProviders'

/**
 * Preview the provisioning files a tool would generate, and scaffold them into a
 * workspace directory. Backed by:
 *   POST /cloud-providers/{id}/provisioning-preview  (render only)
 *   POST /cloud-providers/{id}/scaffold              (write into the workspace)
 */
export function useProviderProvisioning() {
  const { apiFetch } = useApi()

  const previewing = useState<boolean>('provider-prov-previewing', () => false)
  const scaffolding = useState<boolean>('provider-prov-scaffolding', () => false)
  const error = useState<string | null>('provider-prov-error', () => null)

  async function preview(
    providerId: string,
    tool: string,
    spec: ProvisioningSpecInput,
  ): Promise<ScaffoldFile[]> {
    previewing.value = true
    error.value = null
    try {
      const raw = await apiFetch<unknown>(`/cloud-providers/${providerId}/provisioning-preview`, {
        method: 'POST',
        body: JSON.stringify({ tool, spec }),
      })
      return scaffoldFilesSchema.parse(raw)
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Preview failed'
      return []
    } finally {
      previewing.value = false
    }
  }

  async function scaffoldToWorkspace(
    providerId: string,
    workspaceId: string,
    tool: string,
    spec: ProvisioningSpecInput,
  ): Promise<ScaffoldFile[]> {
    scaffolding.value = true
    error.value = null
    try {
      const raw = await apiFetch<unknown>(`/cloud-providers/${providerId}/scaffold`, {
        method: 'POST',
        body: JSON.stringify({ workspace_id: workspaceId, tool, spec }),
      })
      return scaffoldFilesSchema.parse(raw)
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Scaffold failed'
      throw err
    } finally {
      scaffolding.value = false
    }
  }

  return { previewing, scaffolding, error, preview, scaffoldToWorkspace }
}

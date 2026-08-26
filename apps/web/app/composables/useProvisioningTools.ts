import {
  provisioningToolsSchema,
  ALL_CLOUDS,
  type ProvisioningTool,
} from '~/types/cloudProviders'
import { parentCloudOf } from '~/utils/pluginParentCloud'

/**
 * Loads the provisioning + configuration tool catalog
 * (GET /api/v1/provisioning-tools) and filters it by cloud compatibility.
 * Cloud-native tools (aws-native / azure-native / gcp-native) only apply to their
 * own cloud; Terraform/Pulumi/OpenTofu and Ansible/cloud-init are cloud-agnostic.
 */
export function useProvisioningTools() {
  const { apiFetch } = useApi()

  const tools = useState<ProvisioningTool[]>('provisioning-tools', () => [])
  const loaded = useState<boolean>('provisioning-tools-loaded', () => false)
  const loading = useState<boolean>('provisioning-tools-loading', () => false)

  async function load(force = false): Promise<ProvisioningTool[]> {
    if (loaded.value && !force) return tools.value
    loading.value = true
    try {
      const raw = await apiFetch<unknown>('/provisioning-tools')
      tools.value = provisioningToolsSchema.parse(raw)
      loaded.value = true
      return tools.value
    } catch {
      return tools.value
    } finally {
      loading.value = false
    }
  }

  function supportsCloud(tool: ProvisioningTool, providerId: string): boolean {
    const parent = parentCloudOf(providerId)
    return (
      tool.supported_clouds.includes(ALL_CLOUDS) ||
      tool.supported_clouds.includes(providerId) ||
      tool.supported_clouds.includes(parent)
    )
  }

  function toolsForCloud(providerId: string | null | undefined) {
    if (!providerId) return { iac: [] as ProvisioningTool[], config: [] as ProvisioningTool[] }
    const compatible = tools.value.filter((t) => supportsCloud(t, providerId))
    return {
      iac: compatible.filter((t) => t.category === 'iac'),
      config: compatible.filter((t) => t.category === 'config'),
    }
  }

  return { tools, loaded, loading, load, toolsForCloud, supportsCloud }
}

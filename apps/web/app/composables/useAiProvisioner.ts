import type {
  AiProvisionerStatus,
  BlueprintDeployRequest,
  BlueprintDeployResponse,
  BlueprintFixRequest,
  BlueprintGenerateRequest,
  BlueprintGenerateResponse,
} from '~/types/aiProvisioner'

// Blueprint generation and IaC/cloud rendering can take longer than a plain CRUD call.
const AI_TIMEOUT_MS = 90_000

/** Client for the AI Infrastructure Provisioner endpoints. */
export function useAiProvisioner() {
  const { apiFetch } = useApi()

  async function status(): Promise<AiProvisionerStatus> {
    return apiFetch<AiProvisionerStatus>('/ai/status')
  }

  async function generateBlueprint(
    request: BlueprintGenerateRequest,
  ): Promise<BlueprintGenerateResponse> {
    return apiFetch<BlueprintGenerateResponse>('/ai/generate-blueprint', {
      method: 'POST',
      body: JSON.stringify(request),
      timeoutMs: AI_TIMEOUT_MS,
    })
  }

  async function fixBlueprint(
    request: BlueprintFixRequest,
  ): Promise<BlueprintGenerateResponse> {
    return apiFetch<BlueprintGenerateResponse>('/ai/fix-blueprint', {
      method: 'POST',
      body: JSON.stringify(request),
      timeoutMs: AI_TIMEOUT_MS,
    })
  }

  async function deployBlueprint(
    request: BlueprintDeployRequest,
  ): Promise<BlueprintDeployResponse> {
    return apiFetch<BlueprintDeployResponse>('/ai/deploy-blueprint', {
      method: 'POST',
      body: JSON.stringify(request),
      timeoutMs: AI_TIMEOUT_MS,
    })
  }

  return { status, generateBlueprint, fixBlueprint, deployBlueprint }
}

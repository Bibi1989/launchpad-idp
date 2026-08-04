import {
  analyzePreviewResponseSchema,
  type AnalyzePreviewPayload,
  type AnalyzePreviewResponse,
  type DiagnosticPatch,
  type DiagnosticReport,
} from '~/types/diagnostic'
import { planDiagnosticPatchApply, type PatchApplyMode } from '~/utils/diagnosticPatchApply'

export type PatchDecision = 'pending' | 'applied' | 'rejected'

export function usePreviewAnalyzer() {
  const { apiFetch } = useApi()
  const { readWorkspaceFile, writeWorkspaceFile } = useProvisioning()
  const open = useState<boolean>('preview-analyzer-open', () => false)
  const loading = useState<boolean>('preview-analyzer-loading', () => false)
  const error = useState<string | null>('preview-analyzer-error', () => null)
  const report = useState<DiagnosticReport | null>('preview-analyzer-report', () => null)
  const telemetrySummary = useState<Record<string, unknown>>(
    'preview-analyzer-telemetry',
    () => ({}),
  )
  const environmentId = useState<string | null>('preview-analyzer-env-id', () => null)
  const patchDecision = useState<PatchDecision>('preview-analyzer-patch-decision', () => 'pending')
  const patchBusy = useState<boolean>('preview-analyzer-patch-busy', () => false)
  const patchMessage = useState<string | null>('preview-analyzer-patch-message', () => null)
  const lastApplyMode = useState<PatchApplyMode | null>('preview-analyzer-apply-mode', () => null)

  function openDrawer(id: string) {
    environmentId.value = id
    open.value = true
  }

  function closeDrawer() {
    open.value = false
  }

  function resetPatchDecision() {
    patchDecision.value = 'pending'
    patchMessage.value = null
    lastApplyMode.value = null
  }

  async function analyzeEnvironment(
    id: string,
    payload: AnalyzePreviewPayload = {},
  ): Promise<AnalyzePreviewResponse> {
    environmentId.value = id
    open.value = true
    resetPatchDecision()
    return runRequest({ loading, error }, async () => {
      const raw = await apiFetch<unknown>(`/environments/${id}/analyze`, {
        method: 'POST',
        body: JSON.stringify({
          cicdLogs: payload.cicdLogs ?? null,
          kubernetesLogs: payload.kubernetesLogs ?? null,
          trivySarif: payload.trivySarif ?? null,
          codeqlSarif: payload.codeqlSarif ?? null,
          sastLogs: payload.sastLogs ?? null,
          manifestSnippets: payload.manifestSnippets ?? null,
          includeEnvironmentLogs: payload.includeEnvironmentLogs ?? true,
        }),
        timeoutMs: 90_000,
      })
      const parsed = analyzePreviewResponseSchema.parse(raw)
      report.value = parsed.report
      telemetrySummary.value = parsed.telemetrySummary
      return parsed
    }, 'Analyzer failed')
  }

  async function analyzeAdHoc(payload: AnalyzePreviewPayload): Promise<AnalyzePreviewResponse> {
    open.value = true
    resetPatchDecision()
    return runRequest({ loading, error }, async () => {
      const raw = await apiFetch<unknown>('/preview/analyze', {
        method: 'POST',
        body: JSON.stringify(payload),
        timeoutMs: 90_000,
      })
      const parsed = analyzePreviewResponseSchema.parse(raw)
      report.value = parsed.report
      telemetrySummary.value = parsed.telemetrySummary
      return parsed
    }, 'Analyzer failed')
  }

  async function applyPatch(workspaceId: string): Promise<void> {
    const patch = report.value?.patch
    if (!patch) {
      throw new Error('No patch available to apply')
    }
    if (patchDecision.value !== 'pending') {
      return
    }
    patchBusy.value = true
    patchMessage.value = null
    try {
      const result = await writePatchToWorkspace(workspaceId, patch)
      lastApplyMode.value = result.mode
      patchDecision.value = 'applied'
      patchMessage.value = formatApplySuccess(patch.targetFile, result.mode)
    } catch (err) {
      patchMessage.value = err instanceof Error ? err.message : 'Failed to apply patch'
      throw err
    } finally {
      patchBusy.value = false
    }
  }

  function rejectPatch() {
    if (patchDecision.value !== 'pending') return
    patchDecision.value = 'rejected'
    patchMessage.value = 'Fix rejected - no files were changed.'
    lastApplyMode.value = null
  }

  async function writePatchToWorkspace(
    workspaceId: string,
    patch: DiagnosticPatch,
  ): Promise<{ mode: PatchApplyMode }> {
    let current: string | null = null
    try {
      const file = await readWorkspaceFile(workspaceId, patch.targetFile)
      current = file.content
    } catch {
      current = null
    }
    const plan = planDiagnosticPatchApply(current, patch)
    await writeWorkspaceFile(workspaceId, patch.targetFile, plan.nextContent)
    return { mode: plan.mode }
  }

  function clear() {
    report.value = null
    telemetrySummary.value = {}
    error.value = null
    resetPatchDecision()
  }

  return {
    open,
    loading,
    error,
    report,
    telemetrySummary,
    environmentId,
    patchDecision,
    patchBusy,
    patchMessage,
    lastApplyMode,
    openDrawer,
    closeDrawer,
    analyzeEnvironment,
    analyzeAdHoc,
    applyPatch,
    rejectPatch,
    clear,
  }
}

function formatApplySuccess(targetFile: string, mode: PatchApplyMode): string {
  switch (mode) {
    case 'created':
      return `Created ${targetFile} with the suggested fix.`
    case 'replaced':
      return `Applied fix to ${targetFile}.`
    case 'appended':
      return `Appended suggested fix to ${targetFile} (original snippet was not found).`
    default:
      return `Updated ${targetFile}.`
  }
}

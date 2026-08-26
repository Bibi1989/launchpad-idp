<script setup lang="ts">
import type {
  K8sClusterContext,
  K8sDescribeMetadata,
  K8sPipelineStage,
  K8sResource,
} from '~/types/k8s'

const props = defineProps<{
  workspaceId: string
}>()

const { t } = useI18n()

const { getClusterContext, getResources, deleteResource, describeResource } =
  useKubernetesSuite()

const clusterContext = ref<K8sClusterContext | null>(null)
const resources = ref<K8sResource[]>([])
const pipelineStages = ref<K8sPipelineStage[]>([])
const consoleLogs = ref<string[]>([])
const loadingContext = ref(true)
const loadingResources = ref(true)
const applyingPipeline = ref(false)
const errorMessage = ref<string | null>(null)

// Drawer / Modal states
const describeDrawerOpen = ref(false)
const describeMetadata = ref<K8sDescribeMetadata | null>(null)
const describeLoading = ref(false)

const logsModalOpen = ref(false)
const selectedResourceForLogs = ref<K8sResource | null>(null)

const execModalOpen = ref(false)
const selectedResourceForExec = ref<K8sResource | null>(null)

const aiDrawerOpen = ref(false)
const aiErrorContext = ref<string | null>(null)

function openAiDrawerWithError(ctx?: string | null) {
  aiErrorContext.value = ctx || errorMessage.value || 'Kubernetes deployment error'
  aiDrawerOpen.value = true
}

async function loadContext() {
  if (!props.workspaceId) {
    loadingContext.value = false
    return
  }
  loadingContext.value = true
  try {
    clusterContext.value = await getClusterContext(props.workspaceId)
  } catch (err) {
    errorMessage.value = `Cluster unreachable or credentials invalid: ${err instanceof Error ? err.message : String(err)}`
  } finally {
    loadingContext.value = false
  }
}

async function loadResources() {
  if (!props.workspaceId) {
    loadingResources.value = false
    return
  }
  loadingResources.value = true
  try {
    const ns = clusterContext.value?.target_namespace
    resources.value = await getResources(props.workspaceId, ns)
  } catch (err) {
    consoleLogs.value.push(`[error] Failed to fetch resource grid: ${err instanceof Error ? err.message : String(err)}`)
  } finally {
    loadingResources.value = false
  }
}

async function triggerApplyPipeline() {
  if (applyingPipeline.value) return
  applyingPipeline.value = true
  pipelineStages.value = []
  errorMessage.value = null
  consoleLogs.value.push(`[info] ${new Date().toISOString()} Triggering manifest pipeline apply on cluster...`)

  try {
    const config = useRuntimeConfig()
    const apiBase = config.public.apiBase || 'http://localhost:8000/api/v1'
    const tokenState = useState<string | null>('auth-token')
    const token = tokenState.value || (typeof window !== 'undefined' ? localStorage.getItem('launchpad_access_token') : '')
    const activeOrgState = useState<string | null>('active-org-id')
    const activeOrgId = activeOrgState.value || (typeof window !== 'undefined' ? localStorage.getItem('launchpad_active_org_id') : '')
    const ns = clusterContext.value?.target_namespace
    const qs = ns ? `?namespace=${encodeURIComponent(ns)}` : ''
    const url = `${apiBase}/workspaces/${props.workspaceId}/k8s/apply${qs}`

    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    if (activeOrgId) headers['X-Org-ID'] = activeOrgId

    const res = await fetch(url, {
      method: 'POST',
      headers,
    })

    if (!res.ok || !res.body) {
      throw new Error(`K8s apply API returned status ${res.status}`)
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        if (part.startsWith('data: ')) {
          try {
            const stage = JSON.parse(part.slice(6)) as K8sPipelineStage
            const existingIdx = pipelineStages.value.findIndex((s) => s.stage_id === stage.stage_id)
            if (existingIdx >= 0) {
              pipelineStages.value[existingIdx] = stage
            } else {
              pipelineStages.value.push(stage)
            }
            consoleLogs.value.push(`[stage:${stage.stage_id}] ${stage.message}`)
            if (stage.status === 'failed') {
              errorMessage.value = stage.message
            }
          } catch {}
        }
      }
    }

    const failed = pipelineStages.value.some((s) => s.status === 'failed')
    if (failed) {
      consoleLogs.value.push(`[error] Kubernetes manifest apply finished with failures.`)
    } else {
      consoleLogs.value.push(`[success] Kubernetes manifest apply completed successfully.`)
    }
    await loadResources()
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    errorMessage.value = `Pipeline apply failed: ${msg}`
    consoleLogs.value.push(`[error] Pipeline apply failed: ${msg}`)
  } finally {
    applyingPipeline.value = false
  }
}

async function onDescribeResource(res: K8sResource) {
  describeDrawerOpen.value = true
  describeLoading.value = true
  describeMetadata.value = null
  try {
    describeMetadata.value = await describeResource(props.workspaceId, res.kind, res.name, res.namespace)
    consoleLogs.value.push(`[kubectl] kubectl describe ${res.kind}/${res.name} -n ${res.namespace}`)
  } catch (err) {
    consoleLogs.value.push(`[error] Describe failed: ${err instanceof Error ? err.message : String(err)}`)
  } finally {
    describeLoading.value = false
  }
}

function onOpenLogs(res: K8sResource) {
  selectedResourceForLogs.value = res
  logsModalOpen.value = true
  consoleLogs.value.push(`[kubectl] kubectl logs ${res.name} -n ${res.namespace}`)
}

function onOpenExec(res: K8sResource) {
  if (res.kind !== 'Pod') {
    consoleLogs.value.push(
      `[error] Exec requires a Pod. "${res.kind}/${res.name}" is not a Pod - open a Pod row instead.`,
    )
    errorMessage.value = t('k8s.suite.execPodOnly', { name: res.name, kind: res.kind })
    return
  }
  selectedResourceForExec.value = res
  execModalOpen.value = true
  consoleLogs.value.push(
    `[kubectl] kubectl exec -it ${res.name} -n ${res.namespace} -- /bin/sh`,
  )
}

const pendingDelete = ref<K8sResource | null>(null)
const deletingResource = ref(false)
const confirmNukeOpen = ref(false)
const nuking = ref(false)
const { destroyWorkspace } = useProvisioning()

function requestDeleteResource(res: K8sResource) {
  pendingDelete.value = res
}

async function confirmDeleteResource() {
  const res = pendingDelete.value
  if (!res || deletingResource.value) return
  deletingResource.value = true
  consoleLogs.value.push(`[kubectl] kubectl delete ${res.kind}/${res.name} -n ${res.namespace}`)
  try {
    const result = await deleteResource(props.workspaceId, res.kind, res.name, res.namespace)
    if (!result.success) {
      throw new Error(result.message || 'Delete failed')
    }
    consoleLogs.value.push(`[success] ${result.message}`)
    resources.value = resources.value.filter((r) => r.id !== res.id)
    pendingDelete.value = null
  } catch (err) {
    consoleLogs.value.push(`[error] Resource deletion failed: ${err instanceof Error ? err.message : String(err)}`)
  } finally {
    deletingResource.value = false
  }
}

function requestNukeWorkspace() {
  confirmNukeOpen.value = true
}

async function confirmNukeWorkspace() {
  if (nuking.value) return
  nuking.value = true
  consoleLogs.value.push(`[warning] Deleting workspace ${props.workspaceId}…`)
  try {
    await destroyWorkspace(props.workspaceId)
    confirmNukeOpen.value = false
    consoleLogs.value.push(`[success] Workspace deletion started`)
    await navigateTo('/workspaces')
  } catch (err) {
    consoleLogs.value.push(`[error] Nuke failed: ${err instanceof Error ? err.message : String(err)}`)
  } finally {
    nuking.value = false
  }
}

function onRefreshAll() {
  void (async () => {
    await loadContext()
    await loadResources()
  })()
}

onMounted(() => {
  onRefreshAll()
})
</script>

<template>
  <div class="space-y-6 animate-fade-up">
    <!-- Actionable Warning Alert for Error Boundaries -->
    <div
      v-if="errorMessage"
      class="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-xs font-mono text-rose-300 shadow-lg backdrop-blur-md"
    >
      <div class="flex items-start gap-2.5 min-w-0">
        <span class="material-symbols-outlined text-lg text-rose-400 shrink-0">warning</span>
        <div class="min-w-0">
          <h4 class="font-bold text-rose-200 uppercase tracking-wider">{{ t('k8s.suite.clusterWarning') }}</h4>
          <p class="mt-0.5 text-rose-300/90 break-words">{{ errorMessage }}</p>
        </div>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/20 px-3 py-1.5 text-xs font-semibold text-amber-200 hover:bg-amber-500/30 transition shadow-md"
          @click="openAiDrawerWithError(errorMessage)"
        >
          <span class="material-symbols-outlined text-sm text-amber-400">auto_awesome</span>
          <span>{{ t('k8s.suite.aiAnalyzeFix') }}</span>
        </button>
        <button
          type="button"
          class="rounded p-1 text-rose-400 hover:bg-rose-500/20"
          @click="errorMessage = null"
        >
          <span class="material-symbols-outlined text-base">close</span>
        </button>
      </div>
    </div>

    <!-- 1. Top Cluster Context Banner -->
    <K8sClusterBanner
      :context="clusterContext"
      :loading="loadingContext"
      :applying="applyingPipeline"
      @apply="triggerApplyPipeline"
      @refresh="onRefreshAll"
      @delete-workspace="requestNukeWorkspace"
    />

    <!-- 2. Graphical Resource Deployment Flow (Visualizer) -->
    <K8sDeploymentPipelineVisualizer
      :stages="pipelineStages"
      :active="applyingPipeline"
      @ai-fix="openAiDrawerWithError"
      @aiFix="openAiDrawerWithError"
    />

    <!-- 3. Categorized Resource Grid & Quick Action Cards -->
    <K8sResourceGrid
      :resources="resources"
      :loading="loadingResources"
      @describe="onDescribeResource"
      @logs="onOpenLogs"
      @exec="onOpenExec"
      @delete="requestDeleteResource"
    />

    <!-- 4. Collapsible Bottom Execution Console Buffer -->
    <K8sConsoleBuffer
      :logs="consoleLogs"
      @clear="consoleLogs = []"
    />

    <!-- Drawers & Modals -->
    <K8sDescribeDrawer
      :open="describeDrawerOpen"
      :metadata="describeMetadata"
      :loading="describeLoading"
      @close="describeDrawerOpen = false"
    />

    <K8sLogsModal
      :open="logsModalOpen"
      :resource="selectedResourceForLogs"
      :workspace-id="workspaceId"
      @close="logsModalOpen = false"
    />

    <K8sExecTerminalModal
      :open="execModalOpen"
      :resource="selectedResourceForExec"
      :workspace-id="workspaceId"
      @close="execModalOpen = false"
    />

    <ConfirmDialog
      v-model:open="confirmNukeOpen"
      :title="t('k8s.suite.nukeTitle')"
      :message="t('k8s.suite.nukeMessage')"
      :confirm-label="t('k8s.suite.nukeConfirm')"
      :busy="nuking"
      @confirm="confirmNukeWorkspace"
    />

    <ConfirmDialog
      :open="pendingDelete !== null"
      :title="pendingDelete ? `Delete ${pendingDelete.kind}/${pendingDelete.name}?` : t('k8s.suite.deleteResource')"
      :message="pendingDelete
        ? t('k8s.suite.deleteResourceMessage', { namespace: pendingDelete.namespace })
        : ''"
      :confirm-label="t('common.confirmDelete')"
      :busy="deletingResource"
      @update:open="(value) => { if (!value) pendingDelete = null }"
      @confirm="confirmDeleteResource"
    />

    <WorkspaceAiAnalysisDrawer
      v-model:open="aiDrawerOpen"
      :workspace-id="workspaceId"
      :error-context="aiErrorContext"
    />
  </div>
</template>

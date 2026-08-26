<script setup lang="ts">
import type {
  SastLanguage,
} from '~/types/provisioning'
import {
  containerScanToolsForPlatform,
  sastToolsForPlatform,
} from '~/utils/cicdSecurityTools'
import type { InfraManifestModel, KeyValueItem } from '~/utils/infraManifestMapper'
import {
  K8S_DEPLOYMENT_PATH,
  K8S_SERVICE_PATH,
  composeImageRef,
  derivePreviewRoutes,
  parseInfraManifest,
  serializeInfraManifest,
  serviceUsesNodePort,
} from '~/utils/infraManifestMapper'
import { copyTextToClipboard, downloadTextFile } from '~/utils/clipboardFile'
import { INSTANCE_SIZE_OPTIONS } from '~/utils/cloudRegions'

const WorkspaceMonacoEditor = defineAsyncComponent(
  () => import('~/components/WorkspaceMonacoEditor.vue'),
)

const props = defineProps<{
  workspaceId: string
  selectedPath: string | null
}>()

const emit = defineEmits<{
  saved: []
  deleted: [path: string]
  error: [message: string]
}>()

const { readWorkspaceFile, writeWorkspaceFile, deleteWorkspacePath, inspectImage } = useProvisioning()
const { t } = useI18n()

const loading = ref(false)
const saving = ref(false)
const linking = ref(false)
const confirmDeleteOpen = ref(false)
const inspectingImage = ref(false)
const rawContent = ref('')
const originalRawContent = ref('')
const model = ref<InfraManifestModel | null>(null)
const originalModel = ref<InfraManifestModel | null>(null)
const deleting = ref(false)
const statusMessage = ref<string | null>(null)
const syncServiceOnSave = ref(true)
const autoSavePortsFromImage = ref(true)
const showAiAnalysis = ref(false)
const actionsMenuOpen = ref(false)
let loadToken = 0
let loadAbortController: AbortController | null = null
let loadTimeoutHandle: ReturnType<typeof setTimeout> | null = null
let imageInspectTimer: ReturnType<typeof setTimeout> | null = null
let lastInspectedImage = ''
let suppressImageWatch = false

const STRUCTURED_MANIFEST_KINDS = new Set<InfraManifestModel['kind']>([
  'k8s-deployment',
  'k8s-service',
  'k8s-namespace',
  'k8s-hpa',
  'k8s-vpa',
  'k8s-pdb',
  'k8s-ingress',
  'k8s-configmap',
  'k8s-secret',
  'k8s-serviceaccount',
  'k8s-networkpolicy',
  'k8s-resourcequota',
  'k8s-limitrange',
  'helm-values',
  'terraform',
  'opentofu',
  'pulumi',
  'github-workflow',
  'gitlab-ci',
])

const supported = computed(() =>
  Boolean(model.value && STRUCTURED_MANIFEST_KINDS.has(model.value.kind)),
)
const showsStructuredForm = computed(() =>
  Boolean(model.value && STRUCTURED_MANIFEST_KINDS.has(model.value.kind)),
)
const isRawEditor = computed(() =>
  Boolean(props.selectedPath) && !showsStructuredForm.value,
)
const isDeployment = computed(() => model.value?.kind === 'k8s-deployment')
const isService = computed(() => model.value?.kind === 'k8s-service')
const isNamespace = computed(() => model.value?.kind === 'k8s-namespace')
const isHpa = computed(() => model.value?.kind === 'k8s-hpa')
const isVpa = computed(() => model.value?.kind === 'k8s-vpa')
const isPdb = computed(() => model.value?.kind === 'k8s-pdb')
const isIngress = computed(() => model.value?.kind === 'k8s-ingress')
// Launch Preview route cards derived from a multi-service Ingress (/ web, /api backend).
const previewRoutes = computed(() =>
  isIngress.value ? derivePreviewRoutes(rawContent.value) : [],
)
const isConfigMap = computed(() => model.value?.kind === 'k8s-configmap')
const isSecret = computed(() => model.value?.kind === 'k8s-secret')
const isServiceAccount = computed(() => model.value?.kind === 'k8s-serviceaccount')
const isNetworkPolicy = computed(() => model.value?.kind === 'k8s-networkpolicy')
const isResourceQuota = computed(() => model.value?.kind === 'k8s-resourcequota')
const isLimitRange = computed(() => model.value?.kind === 'k8s-limitrange')
const isHelm = computed(() => model.value?.kind === 'helm-values')
const isProvision = computed(() =>
  model.value?.kind === 'terraform' || model.value?.kind === 'opentofu' || model.value?.kind === 'pulumi',
)
const isCi = computed(() => model.value?.kind === 'github-workflow' || model.value?.kind === 'gitlab-ci')
const isGithubWorkflow = computed(() => model.value?.kind === 'github-workflow')
const cicdPlatform = computed(() => (isGithubWorkflow.value ? 'github' : 'gitlab'))
const sastToolOptions = computed(() => sastToolsForPlatform(cicdPlatform.value))
const containerScanToolOptions = computed(() => containerScanToolsForPlatform(cicdPlatform.value))
const isDataMap = computed(() => isConfigMap.value || isSecret.value)
const showNodePort = computed(() =>
  Boolean(
    model.value
    && (isService.value || isHelm.value)
    && serviceUsesNodePort(model.value.serviceType),
  ),
)
const hasChanges = computed(() => {
  if (isRawEditor.value) return rawContent.value !== originalRawContent.value
  return JSON.stringify(model.value) !== JSON.stringify(originalModel.value)
})

const activeFileContent = computed(() => {
  if (isCi.value && workflowYaml.value) return workflowYaml.value
  return rawContent.value
})

const downloadFileName = computed(() => {
  if (!props.selectedPath) return 'file.txt'
  return props.selectedPath.split('/').pop() || 'file.txt'
})

const workflowYaml = computed(() => {
  if (!model.value || !props.selectedPath || !isCi.value) return ''
  return serializeInfraManifest(props.selectedPath, rawContent.value, model.value)
})

const analysisContent = computed(() => {
  if (!props.selectedPath) return ''
  if (model.value && model.value.kind !== 'unknown') {
    return serializeInfraManifest(props.selectedPath, rawContent.value, model.value)
  }
  return rawContent.value
})

const workflowDownloadName = computed(() =>
  isGithubWorkflow.value ? 'deploy.yml' : '.gitlab-ci.yml',
)

const copiedFile = ref(false)
let copiedFileTimer: ReturnType<typeof setTimeout> | null = null

async function copyActiveFile() {
  if (!activeFileContent.value) return
  const ok = await copyTextToClipboard(activeFileContent.value)
  if (!ok) {
    emit('error', 'Could not copy file contents')
    return
  }
  copiedFile.value = true
  if (copiedFileTimer) clearTimeout(copiedFileTimer)
  copiedFileTimer = setTimeout(() => {
    copiedFile.value = false
  }, 2000)
}

function downloadActiveFile() {
  if (!activeFileContent.value) return
  const name = isCi.value ? workflowDownloadName.value : downloadFileName.value
  downloadTextFile(name, activeFileContent.value)
}

const breadcrumb = computed(() => {
  if (!props.selectedPath) return []
  return props.selectedPath.split('/')
})

function addEnvVar() {
  if (!model.value) return
  model.value.envVars.push({ key: '', value: '' })
}

function removeEnvVar(index: number) {
  if (!model.value) return
  model.value.envVars.splice(index, 1)
}

function addDataEntry() {
  if (!model.value) return
  model.value.dataEntries.push({ key: '', value: '' })
}

function removeDataEntry(index: number) {
  if (!model.value) return
  model.value.dataEntries.splice(index, 1)
}

function addSecretRef() {
  if (!model.value) return
  model.value.buildSecrets.push({ key: '', value: '' })
}

function removeSecretRef(index: number) {
  if (!model.value) return
  model.value.buildSecrets.splice(index, 1)
}

function sanitizeKeyValues(items: KeyValueItem[]): KeyValueItem[] {
  return items
    .map((item) => ({ key: item.key.trim(), value: item.value.trim() }))
    .filter((item) => item.key.length > 0)
}

async function loadSelected() {
  const token = ++loadToken
  statusMessage.value = null
  if (loadTimeoutHandle) {
    clearTimeout(loadTimeoutHandle)
    loadTimeoutHandle = null
  }
  if (loadAbortController) {
    loadAbortController.abort()
    loadAbortController = null
  }
  if (!props.selectedPath) {
    model.value = null
    originalModel.value = null
    rawContent.value = ''
    originalRawContent.value = ''
    loading.value = false
    return
  }
  loadAbortController = new AbortController()
  loadTimeoutHandle = setTimeout(() => {
    loadAbortController?.abort()
  }, 8000)
  loading.value = true
  try {
    const file = await readWorkspaceFile(
      props.workspaceId,
      props.selectedPath,
      loadAbortController.signal,
    )
    if (token !== loadToken) return
    rawContent.value = file.content
    originalRawContent.value = file.content
    suppressImageWatch = true
    model.value = parseInfraManifest(props.selectedPath, file.content)
    originalModel.value = JSON.parse(JSON.stringify(model.value)) as InfraManifestModel
    lastInspectedImage = (isDeployment.value || isHelm.value)
      ? composeImageRef(model.value.appImage, model.value.imageTag)
      : ''
    await nextTick()
    suppressImageWatch = false
  } catch (err) {
    if (token !== loadToken) return
    if (err instanceof Error && err.name === 'AbortError') {
      emit('error', 'Timed out while loading selected file')
    } else {
      emit('error', err instanceof Error ? err.message : 'Failed to load manifest file')
    }
    model.value = null
    originalModel.value = null
    rawContent.value = ''
    originalRawContent.value = ''
  } finally {
    if (loadTimeoutHandle) {
      clearTimeout(loadTimeoutHandle)
      loadTimeoutHandle = null
    }
    if (loadAbortController) {
      loadAbortController = null
    }
    if (token !== loadToken) return
    loading.value = false
  }
}

async function syncServiceFromDeployment(opts: {
  appLabel?: string
  containerPort?: string
}): Promise<boolean> {
  try {
    const file = await readWorkspaceFile(props.workspaceId, K8S_SERVICE_PATH)
    const svcModel = parseInfraManifest(K8S_SERVICE_PATH, file.content)
    if (opts.appLabel) {
      svcModel.appLabel = opts.appLabel
    }
    if (opts.containerPort) {
      // Keep cluster service port (often 80); align targetPort to container listen port.
      svcModel.targetPort = opts.containerPort
    }
    const next = serializeInfraManifest(K8S_SERVICE_PATH, file.content, svcModel)
    await writeWorkspaceFile(props.workspaceId, K8S_SERVICE_PATH, next)
    return true
  } catch {
    return false
  }
}

async function pullSelectorFromDeployment() {
  if (!model.value || model.value.kind !== 'k8s-service') return
  linking.value = true
  statusMessage.value = null
  try {
    const file = await readWorkspaceFile(props.workspaceId, K8S_DEPLOYMENT_PATH)
    const depModel = parseInfraManifest(K8S_DEPLOYMENT_PATH, file.content)
    model.value.appLabel = depModel.appLabel || depModel.resourceName || 'app'
    if (depModel.appPort) {
      model.value.targetPort = depModel.appPort
    }
    statusMessage.value = `Selector linked to deployment label “${model.value.appLabel}”`
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'Failed to read deployment.yaml')
  } finally {
    linking.value = false
  }
}

async function applyImageListenPort(
  listenPort: number,
  opts: { persist: boolean },
): Promise<void> {
  if (!model.value) return
  const port = String(listenPort)
  if (model.value.kind === 'k8s-deployment') {
    model.value.appPort = port
  } else if (model.value.kind === 'helm-values') {
    // Helm chart maps containerPort ← service.targetPort
    model.value.targetPort = port
    const appPortEnv = model.value.envVars.find((item) => item.key === 'APP_PORT' || item.key === 'PORT')
    if (appPortEnv) appPortEnv.value = port
  } else {
    return
  }
  if (opts.persist) {
    await saveChanges({ quiet: true })
  }
}

async function detectPortsFromImage(
  opts: { persist: boolean; force?: boolean } = { persist: true },
) {
  if (!model.value) return
  if (model.value.kind !== 'k8s-deployment' && model.value.kind !== 'helm-values') return
  const image = composeImageRef(model.value.appImage, model.value.imageTag)
  if (!image || (!opts.force && image === lastInspectedImage)) return

  inspectingImage.value = true
  statusMessage.value = `Inspecting image ports for ${image}…`
  try {
    const result = await inspectImage(image)
    lastInspectedImage = image
    const prev =
      model.value.kind === 'helm-values' ? model.value.targetPort : model.value.appPort
    await applyImageListenPort(result.listen_port, { persist: opts.persist })
    const exposed = result.exposed_ports.length
      ? ` (EXPOSE ${result.exposed_ports.join(', ')})`
      : ' (no EXPOSE found; using default)'
    const field = model.value.kind === 'helm-values' ? 'targetPort' : 'containerPort'
    statusMessage.value = `Prefill ${field} ${result.listen_port}${exposed}`
      + (opts.persist && prev !== String(result.listen_port) ? ' · saved' : '')
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'Failed to inspect image ports')
    statusMessage.value = null
  } finally {
    inspectingImage.value = false
  }
}

function scheduleImagePortDetect() {
  if (suppressImageWatch || !model.value) return
  if (!isDeployment.value && !isHelm.value) return
  if (imageInspectTimer) clearTimeout(imageInspectTimer)
  imageInspectTimer = setTimeout(() => {
    void detectPortsFromImage({ persist: autoSavePortsFromImage.value })
  }, 700)
}

async function saveChanges(opts: { quiet?: boolean } = {}) {
  if (!props.selectedPath) return
  if (isRawEditor.value) {
    saving.value = true
    if (!opts.quiet) statusMessage.value = null
    try {
      await writeWorkspaceFile(props.workspaceId, props.selectedPath, rawContent.value)
      originalRawContent.value = rawContent.value
      if (!opts.quiet) statusMessage.value = `Saved ${props.selectedPath}`
      emit('saved')
    } catch (err) {
      emit('error', err instanceof Error ? err.message : 'Failed to save file')
    } finally {
      saving.value = false
    }
    return
  }
  if (!model.value) return
  saving.value = true
  if (!opts.quiet) statusMessage.value = null
  try {
    model.value.envVars = sanitizeKeyValues(model.value.envVars)
    model.value.buildSecrets = sanitizeKeyValues(model.value.buildSecrets)
    model.value.dataEntries = sanitizeKeyValues(model.value.dataEntries)
    if (model.value.appLabel) {
      model.value.appLabel = model.value.appLabel.trim()
    }
    if (model.value.resourceName) {
      model.value.resourceName = model.value.resourceName.trim()
    }
    if (model.value.namespaceName) {
      model.value.namespaceName = model.value.namespaceName.trim()
    }
    if (model.value.kind === 'k8s-service' && !serviceUsesNodePort(model.value.serviceType)) {
      model.value.nodePort = ''
    }
    const content = serializeInfraManifest(props.selectedPath, rawContent.value, model.value)
    await writeWorkspaceFile(props.workspaceId, props.selectedPath, content)
    rawContent.value = content
    originalModel.value = JSON.parse(JSON.stringify(model.value)) as InfraManifestModel

    let linkedNote = ''
    if (
      model.value.kind === 'k8s-deployment'
      && syncServiceOnSave.value
    ) {
      const synced = await syncServiceFromDeployment({
        appLabel: model.value.appLabel || undefined,
        containerPort: model.value.appPort || undefined,
      })
      if (synced) linkedNote = ' · service selector & targetPort updated'
    }

    if (!opts.quiet) {
      statusMessage.value = `Saved ${props.selectedPath}${linkedNote}`
    } else if (linkedNote) {
      statusMessage.value = (statusMessage.value || 'Ports updated') + linkedNote
    }
    emit('saved')
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'Failed to save manifest changes')
  } finally {
    saving.value = false
  }
}

function closeActionsMenu() {
  actionsMenuOpen.value = false
}

function toggleActionsMenu() {
  actionsMenuOpen.value = !actionsMenuOpen.value
}

function discardChanges() {
  if (isRawEditor.value) {
    rawContent.value = originalRawContent.value
    statusMessage.value = null
    return
  }
  if (!originalModel.value) return
  suppressImageWatch = true
  model.value = JSON.parse(JSON.stringify(originalModel.value)) as InfraManifestModel
  statusMessage.value = null
  lastInspectedImage = (isDeployment.value || isHelm.value) && model.value
    ? composeImageRef(model.value.appImage, model.value.imageTag)
    : ''
  void nextTick(() => {
    suppressImageWatch = false
  })
}

async function applyAiImprovedContent(payload: { path: string; content: string }) {
  const path = payload.path || props.selectedPath
  const content = payload.content
  if (!path || !content?.trim()) {
    throw new Error('AI fix is empty')
  }

  // Only apply when the drawer targets the open file (interactive configurator is single-file).
  if (props.selectedPath && path !== props.selectedPath) {
    throw new Error(`AI fix targets ${path}, but ${props.selectedPath} is open`)
  }

  await writeWorkspaceFile(props.workspaceId, path, content)
  if (isRawEditor.value) {
    rawContent.value = content
    originalRawContent.value = content
  } else {
    const next = parseInfraManifest(path, content)
    model.value = next
    originalModel.value = JSON.parse(JSON.stringify(next)) as InfraManifestModel
    rawContent.value = content
    originalRawContent.value = content
  }
  showAiAnalysis.value = false
  statusMessage.value = `Applied AI fix to ${path}`
  emit('saved')
}

function requestDeleteSelectedFile() {
  if (!props.selectedPath || deleting.value) return
  confirmDeleteOpen.value = true
}

async function deleteSelectedFile() {
  if (!props.selectedPath || deleting.value) return
  const path = props.selectedPath
  confirmDeleteOpen.value = false
  deleting.value = true
  statusMessage.value = null
  try {
    await deleteWorkspacePath(props.workspaceId, path)
    emit('deleted', path)
    statusMessage.value = `Deleted ${path}`
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'Failed to delete file')
  } finally {
    deleting.value = false
  }
}

function onAiError(message: string) {
  emit('error', message)
}

watch(
  () => props.selectedPath,
  async () => {
    await loadSelected()
  },
  { immediate: true },
)

watch(
  () => [model.value?.appImage, model.value?.imageTag, isDeployment.value, isHelm.value] as const,
  () => {
    scheduleImagePortDetect()
  },
)

onMounted(() => {
  document.addEventListener('click', closeActionsMenu)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', closeActionsMenu)
  if (loadTimeoutHandle) {
    clearTimeout(loadTimeoutHandle)
    loadTimeoutHandle = null
  }
  if (imageInspectTimer) {
    clearTimeout(imageInspectTimer)
    imageInspectTimer = null
  }
  if (copiedFileTimer) {
    clearTimeout(copiedFileTimer)
    copiedFileTimer = null
  }
  loadAbortController?.abort()
  loadAbortController = null
})
</script>

<template>
  <section class="flex min-h-[78vh] min-w-0 flex-1 flex-col overflow-hidden bg-[var(--lp-panel)]/40">
    <header class="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 px-5 py-4">
      <div class="min-w-0 space-y-1">
        <div
          v-if="breadcrumb.length"
          class="flex flex-wrap items-center gap-1 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]"
        >
          <template v-for="(item, idx) in breadcrumb" :key="`${item}-${idx}`">
            <span>{{ item }}</span>
            <span v-if="idx < breadcrumb.length - 1" class="material-symbols-outlined text-xs">chevron_right</span>
          </template>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="truncate text-lg font-semibold text-[var(--lp-text)]">
            {{ selectedPath?.split('/').pop() || t('manifest.configurator.defaultTitle') }}
          </h1>
          <span
            v-if="supported"
            class="rounded border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-accent)]"
          >
            {{ t('manifest.configurator.mapped') }}
          </span>
          <span
            v-else-if="isRawEditor"
            class="rounded border border-[var(--lp-line)] px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]"
          >
            {{ t('manifest.configurator.ide') }}
          </span>
        </div>
      </div>
      <div class="flex shrink-0 flex-wrap items-center gap-2">
        <button
          type="button"
          class="lp-btn-ghost py-2 text-xs uppercase tracking-wide disabled:opacity-40"
          :disabled="!selectedPath || !rawContent.trim() || loading"
          @click="showAiAnalysis = true"
        >
          <span class="material-symbols-outlined text-sm">auto_awesome</span>
          {{ t('manifest.configurator.aiAnalyze') }}
        </button>
        <button
          type="button"
          class="lp-btn-primary py-2 text-xs uppercase tracking-wide"
          :disabled="!selectedPath || saving || !hasChanges || (!isRawEditor && !supported)"
          @click="saveChanges()"
        >
          <span class="material-symbols-outlined text-sm">save</span>
          {{ saving ? t('common.saving') : t('manifest.configurator.saveChanges') }}
        </button>
        <div class="relative" @click.stop>
          <button
            type="button"
            class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/25 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
            :aria-expanded="actionsMenuOpen"
            aria-haspopup="menu"
            :aria-label="t('manifest.configurator.moreActions')"
            @click="toggleActionsMenu"
          >
            <span class="material-symbols-outlined text-xl">more_vert</span>
          </button>
          <div
            v-if="actionsMenuOpen"
            role="menu"
            class="absolute right-0 top-full z-30 mt-1.5 min-w-[200px] overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] py-1 shadow-xl"
          >
            <button
              type="button"
              role="menuitem"
              class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-40"
              :disabled="!activeFileContent"
              @click="copyActiveFile(); closeActionsMenu()"
            >
              <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">
                {{ copiedFile ? 'check' : 'content_copy' }}
              </span>
              {{ copiedFile ? t('common.copied') : t('common.copy') }}
            </button>
            <button
              type="button"
              role="menuitem"
              class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-40"
              :disabled="!activeFileContent"
              @click="downloadActiveFile(); closeActionsMenu()"
            >
              <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">download</span>
              {{ t('common.download') }}
            </button>
            <button
              type="button"
              role="menuitem"
              class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-40"
              :disabled="!hasChanges || saving"
              @click="discardChanges(); closeActionsMenu()"
            >
              <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">undo</span>
              {{ t('manifest.configurator.discard') }}
            </button>
            <div class="my-1 border-t border-[var(--lp-line)]" role="separator" />
            <button
              type="button"
              role="menuitem"
              class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-danger)] transition hover:bg-[var(--lp-danger)]/10 disabled:opacity-40"
              :disabled="!selectedPath || deleting"
              @click="requestDeleteSelectedFile(); closeActionsMenu()"
            >
              <span class="material-symbols-outlined text-base">delete</span>
              {{ deleting ? t('manifest.configurator.deleting') : t('manifest.configurator.deleteFile') }}
            </button>
          </div>
        </div>
      </div>
    </header>

    <div class="flex-1 overflow-y-auto px-5 py-6">
      <div v-if="!selectedPath" class="rounded-xl border border-dashed border-[var(--lp-line)] px-6 py-10 text-center text-sm text-[var(--lp-muted)]">
        {{ t('manifest.configurator.selectSidebar') }}
      </div>
      <div v-else-if="loading" class="text-sm text-[var(--lp-muted)]">
        {{ t('manifest.configurator.loadingFile') }}
      </div>
      <div
        v-else-if="isRawEditor"
        class="flex h-[min(70vh,720px)] min-h-[420px] flex-col overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/30"
      >
        <ClientOnly>
          <WorkspaceMonacoEditor
            v-model="rawContent"
            :path="selectedPath"
            class="min-h-0 flex-1"
          />
          <template #fallback>
            <p class="p-6 text-sm text-[var(--lp-muted)]">{{ t('manifest.configurator.loadingEditor') }}</p>
          </template>
        </ClientOnly>
      </div>

      <div v-else-if="model" class="mx-auto max-w-4xl space-y-10 pb-16">
        <!-- Namespace -->
        <section v-if="isNamespace" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">folder</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.namespace') }}</h3>
          </div>
          <label class="block max-w-md space-y-2">
            <span class="lp-label">Namespace name</span>
            <input v-model="model.namespaceName" type="text" class="lp-input">
          </label>
        </section>

        <!-- HPA -->
        <section v-if="isHpa" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">speed</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.hpa') }}</h3>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block space-y-2">
              <span class="lp-label">Resource name</span>
              <input v-model="model.resourceName" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Target CPU %</span>
              <input v-model.number="model.hpaTargetCpu" type="number" min="1" max="100" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Min replicas</span>
              <input v-model.number="model.hpaMinReplicas" type="number" min="1" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Max replicas</span>
              <input v-model.number="model.hpaMaxReplicas" type="number" min="1" class="lp-input">
            </label>
          </div>
        </section>

        <!-- VPA -->
        <section v-if="isVpa" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">height</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.vpa') }}</h3>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block space-y-2">
              <span class="lp-label">Resource name</span>
              <input v-model="model.resourceName" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Update mode</span>
              <select v-model="model.vpaUpdateMode" class="lp-input">
                <option value="Off">Off (recommendation only)</option>
                <option value="Initial">Initial</option>
                <option value="Recreate">Recreate</option>
                <option value="Auto">Auto</option>
              </select>
            </label>
          </div>
        </section>

        <!-- PDB -->
        <section v-if="isPdb" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">shield</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.pdb') }}</h3>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block space-y-2">
              <span class="lp-label">Resource name</span>
              <input v-model="model.resourceName" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">App label</span>
              <input v-model="model.appLabel" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Min available</span>
              <input v-model="model.pdbMinAvailable" type="text" class="lp-input" placeholder="1">
            </label>
          </div>
        </section>

        <!-- Ingress -->
        <section v-if="isIngress" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">public</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.ingress') }}</h3>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block space-y-2">
              <span class="lp-label">Resource name</span>
              <input v-model="model.resourceName" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Ingress class</span>
              <input v-model="model.ingressClassName" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Host</span>
              <input v-model="model.ingressHost" type="text" class="lp-input" placeholder="app.example.com">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Path</span>
              <input v-model="model.ingressPath" type="text" class="lp-input" placeholder="/">
            </label>
          </div>

          <!-- Launch Preview routes derived from the multi-service Ingress -->
          <div v-if="previewRoutes.length" class="space-y-2">
            <span class="lp-label">Launch Preview routes</span>
            <a
              v-for="route in previewRoutes"
              :key="route.path"
              :href="route.url"
              target="_blank"
              rel="noopener"
              class="flex items-center justify-between gap-3 rounded-md border border-[var(--lp-line)] px-3 py-2 hover:border-[var(--lp-accent)]"
            >
              <span class="flex items-center gap-2 text-sm">
                <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">
                  {{ route.path === '/' ? 'language' : 'api' }}
                </span>
                {{ route.label }}
              </span>
              <span class="font-mono text-[11px] text-[var(--lp-muted)]">{{ route.url }}</span>
            </a>
          </div>
        </section>

        <!-- ConfigMap / Secret -->
        <section v-if="isDataMap" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">{{ isSecret ? 'key' : 'data_object' }}</span>
            <h3 class="lp-label">{{ isSecret ? t('manifest.configurator.sections.secret') : t('manifest.configurator.sections.configMap') }}</h3>
          </div>
          <label class="block max-w-md space-y-2">
            <span class="lp-label">Resource name</span>
            <input v-model="model.resourceName" type="text" class="lp-input">
          </label>
          <div class="space-y-2">
            <div
              v-for="(entry, idx) in model.dataEntries"
              :key="idx"
              class="grid gap-2 sm:grid-cols-[1fr_1fr_auto]"
            >
              <input v-model="entry.key" type="text" class="lp-input font-mono text-sm" placeholder="KEY">
              <input v-model="entry.value" type="text" class="lp-input font-mono text-sm" placeholder="value">
              <button type="button" class="lp-btn-ghost px-2" @click="removeDataEntry(idx)">
                <span class="material-symbols-outlined text-base">delete</span>
              </button>
            </div>
            <button type="button" class="lp-btn-ghost text-xs" @click="addDataEntry">
              + Add entry
            </button>
          </div>
        </section>

        <!-- ServiceAccount -->
        <section v-if="isServiceAccount" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">badge</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.serviceAccount') }}</h3>
          </div>
          <label class="block max-w-md space-y-2">
            <span class="lp-label">Name</span>
            <input v-model="model.resourceName" type="text" class="lp-input">
          </label>
        </section>

        <!-- NetworkPolicy -->
        <section v-if="isNetworkPolicy" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">firewall</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.networkPolicy') }}</h3>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block space-y-2">
              <span class="lp-label">Resource name</span>
              <input v-model="model.resourceName" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">App selector label</span>
              <input v-model="model.appLabel" type="text" class="lp-input">
            </label>
          </div>
        </section>

        <!-- ResourceQuota -->
        <section v-if="isResourceQuota" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">pie_chart</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.resourceQuota') }}</h3>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block space-y-2">
              <span class="lp-label">Name</span>
              <input v-model="model.resourceName" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Pods</span>
              <input v-model="model.quotaPods" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">CPU requests</span>
              <input v-model="model.quotaCpuRequests" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Memory requests</span>
              <input v-model="model.quotaMemoryRequests" type="text" class="lp-input">
            </label>
          </div>
        </section>

        <!-- LimitRange -->
        <section v-if="isLimitRange" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">straighten</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.limitRange') }}</h3>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block space-y-2">
              <span class="lp-label">Name</span>
              <input v-model="model.resourceName" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Default CPU</span>
              <input v-model="model.limitDefaultCpu" type="text" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Default memory</span>
              <input v-model="model.limitDefaultMemory" type="text" class="lp-input">
            </label>
          </div>
        </section>

        <!-- Identity / labels -->
        <section v-if="isDeployment || isService || isHelm" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">sell</span>
            <h3 class="lp-label">{{ isService ? t('manifest.configurator.sections.serviceLink') : t('manifest.configurator.sections.workloadIdentity') }}</h3>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <label v-if="!isHelm" class="block space-y-2">
              <span class="lp-label">Resource name</span>
              <input v-model="model.resourceName" type="text" class="lp-input" placeholder="app">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">{{ isService ? 'Selector app label' : 'App label' }}</span>
              <input v-model="model.appLabel" type="text" class="lp-input" placeholder="app">
            </label>
            <label v-if="isDeployment || isHelm" class="block space-y-2">
              <span class="lp-label">Replicas</span>
              <input v-model.number="model.replicas" type="number" min="1" class="lp-input">
            </label>
          </div>
          <div v-if="isService" class="flex flex-wrap items-center gap-3">
            <button
              type="button"
              class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide"
              :disabled="linking"
              @click="pullSelectorFromDeployment"
            >
              <span class="material-symbols-outlined text-sm">link</span>
              {{ linking ? 'Linking…' : 'Link to deployment label' }}
            </button>
            <p class="font-mono text-[10px] text-[var(--lp-muted)]">
              Copies <code class="text-[var(--lp-text)]">app</code> from deployment.yaml into this service selector.
            </p>
          </div>
          <label v-if="isDeployment" class="flex cursor-pointer items-center gap-2 text-sm text-[var(--lp-muted)]">
            <input v-model="syncServiceOnSave" type="checkbox" class="accent-[var(--lp-accent)]">
            Also update service.yaml selector and targetPort when saving
          </label>
          <label v-if="isDeployment || isHelm" class="flex cursor-pointer items-center gap-2 text-sm text-[var(--lp-muted)]">
            <input v-model="autoSavePortsFromImage" type="checkbox" class="accent-[var(--lp-accent)]">
            Detect EXPOSE from image and save ports
          </label>
        </section>

        <!-- Init containers (deployment) - read-only, shown when datastores add wait blocks -->
        <section v-if="isDeployment && model.initContainers.length" class="space-y-3">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">hourglass_top</span>
            <h3 class="lp-label">Init containers ({{ model.initContainers.length }})</h3>
          </div>
          <p class="text-[11px] text-[var(--lp-muted)]">
            Generated automatically for each in-cluster datastore - they block startup
            until the dependency is reachable. Read-only.
          </p>
          <ul class="space-y-2">
            <li
              v-for="(init, idx) in model.initContainers"
              :key="`init-${idx}`"
              class="flex items-center justify-between gap-3 rounded-md border border-[var(--lp-line)] px-3 py-2"
            >
              <span class="font-mono text-xs text-[var(--lp-text)]">{{ init.name }}</span>
              <span class="font-mono text-[10px] text-[var(--lp-muted)]">{{ init.image }}</span>
            </li>
          </ul>
        </section>

        <!-- Expose to Launch Preview (deployment / service) -->
        <section v-if="isDeployment || isService" class="space-y-3">
          <label class="flex items-start gap-3 rounded-md border border-[var(--lp-line)] px-3 py-3">
            <input
              v-model="model.exposePreview"
              type="checkbox"
              class="mt-0.5 h-4 w-4 accent-[var(--lp-accent)]"
            >
            <span class="space-y-1">
              <span class="lp-label flex items-center gap-2">
                <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">public</span>
                Expose to Launch Preview
              </span>
              <span class="block text-[11px] text-[var(--lp-muted)]">
                Route Launch Preview to this workload (adds
                <code>launchpad.io/preview-target: "true"</code>). The exposed web stack
                is served at <code>/</code>; backends at <code>/api</code>.
              </span>
            </span>
          </label>
        </section>

        <!-- Container (deployment / helm) -->
        <section v-if="isDeployment || isHelm" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">view_in_ar</span>
            <h3 class="lp-label">{{ isDeployment ? t('manifest.configurator.sections.appContainer') : t('manifest.configurator.sections.containerConfig') }}</h3>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block space-y-2">
              <span class="lp-label">Image</span>
              <input
                v-model="model.appImage"
                type="text"
                class="lp-input"
                @blur="detectPortsFromImage({ persist: autoSavePortsFromImage })"
              >
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Tag</span>
              <input
                v-model="model.imageTag"
                type="text"
                class="lp-input"
                @blur="detectPortsFromImage({ persist: autoSavePortsFromImage })"
              >
            </label>
            <label class="block space-y-2 sm:col-span-2">
              <span class="lp-label">Pull policy</span>
              <select v-model="model.pullPolicy" class="lp-input">
                <option value="IfNotPresent">IfNotPresent</option>
                <option value="Always">Always</option>
                <option value="Never">Never</option>
              </select>
            </label>
          </div>
          <p v-if="isDeployment && inspectingImage" class="font-mono text-[10px] text-[var(--lp-muted)]">
            Inspecting image EXPOSE ports…
          </p>
        </section>

        <!-- Resources (deployment / helm) -->
        <section v-if="isDeployment || isHelm" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">memory</span>
            <h3 class="lp-label">Resources & right-sizing</h3>
          </div>
          <div class="grid gap-2 sm:grid-cols-3">
            <button
              v-for="preset in [
                { id: 'developer', label: 'Developer / Lean', cpu: '100m', mem: '128Mi', cpuLim: '250m', memLim: '256Mi' },
                { id: 'balanced', label: 'Balanced', cpu: '250m', mem: '512Mi', cpuLim: '500m', memLim: '1Gi' },
                { id: 'performance', label: 'Performance', cpu: '1', mem: '2Gi', cpuLim: '2', memLim: '4Gi' },
              ]"
              :key="preset.id"
              type="button"
              class="rounded-lg border px-3 py-2 text-left transition"
              :class="'border-[var(--lp-line)] hover:border-[var(--lp-accent)]/50 hover:bg-[var(--lp-accent)]/5'"
              @click="
                model.cpuRequest = preset.cpu;
                model.memoryRequest = preset.mem;
                model.cpuLimit = preset.cpuLim;
                model.memoryLimit = preset.memLim
              "
            >
              <span class="block text-sm font-medium">{{ preset.label }}</span>
              <span class="mt-0.5 block font-mono text-[10px] text-[var(--lp-muted)]">
                {{ preset.cpu }} / {{ preset.mem }}
              </span>
            </button>
          </div>
          <div class="grid gap-6 lg:grid-cols-2">
            <div class="space-y-3">
              <p class="font-mono text-xs font-semibold uppercase tracking-wide text-[var(--lp-accent)]">CPU</p>
              <div class="grid grid-cols-2 gap-3">
                <label class="block space-y-1.5">
                  <span class="lp-label">Requests</span>
                  <div class="flex">
                    <input v-model="model.cpuRequest" type="text" placeholder="100" class="lp-input rounded-r-none border-r-0">
                    <span class="flex items-center rounded-r-md border border-l-0 border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-2 font-mono text-[10px] text-[var(--lp-muted)]">m</span>
                  </div>
                </label>
                <label class="block space-y-1.5">
                  <span class="lp-label">Limits</span>
                  <div class="flex">
                    <input v-model="model.cpuLimit" type="text" placeholder="500" class="lp-input rounded-r-none border-r-0">
                    <span class="flex items-center rounded-r-md border border-l-0 border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-2 font-mono text-[10px] text-[var(--lp-muted)]">m</span>
                  </div>
                </label>
              </div>
            </div>
            <div class="space-y-3">
              <p class="font-mono text-xs font-semibold uppercase tracking-wide text-[var(--lp-accent)]">Memory</p>
              <div class="grid grid-cols-2 gap-3">
                <label class="block space-y-1.5">
                  <span class="lp-label">Requests</span>
                  <div class="flex">
                    <input v-model="model.memoryRequest" type="text" placeholder="128" class="lp-input rounded-r-none border-r-0">
                    <span class="flex items-center rounded-r-md border border-l-0 border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-2 font-mono text-[10px] text-[var(--lp-muted)]">Mi</span>
                  </div>
                </label>
                <label class="block space-y-1.5">
                  <span class="lp-label">Limits</span>
                  <div class="flex">
                    <input v-model="model.memoryLimit" type="text" placeholder="512" class="lp-input rounded-r-none border-r-0">
                    <span class="flex items-center rounded-r-md border border-l-0 border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-2 font-mono text-[10px] text-[var(--lp-muted)]">Mi</span>
                  </div>
                </label>
              </div>
            </div>
          </div>
        </section>

        <!-- Networking (service / helm) - service type only persists here -->
        <section v-if="isService || isHelm" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">hub</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.networking') }}</h3>
          </div>
          <div class="space-y-4">
            <div class="space-y-2">
              <span class="lp-label">Service type</span>
              <div class="flex flex-wrap gap-4">
                <label
                  v-for="svcType in ['ClusterIP', 'NodePort', 'LoadBalancer']"
                  :key="svcType"
                  class="flex cursor-pointer items-center gap-2"
                >
                  <input
                    v-model="model.serviceType"
                    :value="svcType"
                    type="radio"
                    name="service_type"
                    class="accent-[var(--lp-accent)]"
                  >
                  <span class="text-sm text-[var(--lp-text)]">{{ svcType }}</span>
                </label>
              </div>
              <p class="font-mono text-[10px] text-[var(--lp-muted)]">
                <template v-if="model.serviceType === 'ClusterIP'">
                  ClusterIP: edit <code class="text-[var(--lp-text)]">port</code> and
                  <code class="text-[var(--lp-text)]">targetPort</code> only.
                </template>
                <template v-else-if="model.serviceType === 'NodePort'">
                  NodePort: <code class="text-[var(--lp-text)]">port</code>,
                  <code class="text-[var(--lp-text)]">targetPort</code>, and optional
                  <code class="text-[var(--lp-text)]">nodePort</code>
                  (kind local range 30080-30089; leave empty to auto-assign).
                </template>
                <template v-else>
                  LoadBalancer: same port fields as NodePort; cloud LB may allocate externally.
                </template>
              </p>
            </div>
            <div
              class="grid gap-4"
              :class="showNodePort ? 'sm:grid-cols-3' : 'sm:grid-cols-2'"
            >
              <label class="block space-y-2">
                <span class="lp-label">port</span>
                <input v-model="model.appPort" type="text" class="lp-input" placeholder="80">
                <span class="block font-mono text-[10px] text-[var(--lp-muted)]">
                  Service port clients use inside the cluster
                </span>
              </label>
              <label class="block space-y-2">
                <span class="lp-label">targetPort</span>
                <input v-model="model.targetPort" type="text" class="lp-input" placeholder="80 or http">
                <span class="block font-mono text-[10px] text-[var(--lp-muted)]">
                  {{ isHelm ? 'Container listen port (chart containerPort)' : 'Pod containerPort or named port' }}
                </span>
              </label>
              <label v-if="showNodePort" class="block space-y-2">
                <span class="lp-label">nodePort</span>
                <input
                  v-model="model.nodePort"
                  type="text"
                  class="lp-input"
                  placeholder="auto"
                  inputmode="numeric"
                >
                <span class="block font-mono text-[10px] text-[var(--lp-muted)]">
                  Host port 30000-32767; empty = Launchpad assigns
                </span>
              </label>
            </div>
            <div v-if="isHelm" class="flex flex-wrap items-center gap-3">
              <button
                type="button"
                class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide"
                :disabled="inspectingImage || saving"
                @click="detectPortsFromImage({ persist: autoSavePortsFromImage, force: true })"
              >
                <span class="material-symbols-outlined text-sm">search</span>
                {{ inspectingImage ? 'Detecting…' : 'Detect targetPort from image' }}
              </button>
              <p v-if="inspectingImage" class="font-mono text-[10px] text-[var(--lp-muted)]">
                Inspecting image EXPOSE ports…
              </p>
            </div>
          </div>
        </section>

        <!-- Container port (deployment) -->
        <section v-if="isDeployment" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">lan</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.containerPort') }}</h3>
          </div>
          <label class="block max-w-xs space-y-2">
            <span class="lp-label">containerPort</span>
            <input v-model="model.appPort" type="text" class="lp-input">
            <span class="block font-mono text-[10px] text-[var(--lp-muted)]">
              Prefills from image EXPOSE when you change Image/Tag
            </span>
          </label>
          <button
            type="button"
            class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide"
            :disabled="inspectingImage || saving"
            @click="detectPortsFromImage({ persist: autoSavePortsFromImage, force: true })"
          >
            <span class="material-symbols-outlined text-sm">search</span>
            {{ inspectingImage ? 'Detecting…' : 'Detect from image' }}
          </button>
        </section>

        <!-- Env (deployment / helm) -->
        <section v-if="isDeployment || isHelm" class="space-y-4">
          <div class="flex items-center justify-between gap-3 border-b border-[var(--lp-line)] pb-2">
            <div class="flex items-center gap-2">
              <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">key</span>
              <h3 class="lp-label">{{ t('manifest.configurator.sections.envVars') }}</h3>
            </div>
            <button type="button" class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide" @click="addEnvVar">
              <span class="material-symbols-outlined text-sm">add</span>
              Add row
            </button>
          </div>
          <div class="space-y-2">
            <div
              v-for="(item, idx) in model.envVars"
              :key="idx"
              class="group flex items-center gap-2"
            >
              <div class="grid flex-1 grid-cols-2 gap-2">
                <input v-model="item.key" type="text" placeholder="KEY" class="lp-input py-1.5 text-xs">
                <input v-model="item.value" type="text" placeholder="VALUE" class="lp-input py-1.5 text-xs">
              </div>
              <button
                type="button"
                class="rounded p-1 text-[var(--lp-muted)] opacity-0 transition hover:text-[var(--lp-danger)] group-hover:opacity-100"
                @click="removeEnvVar(idx)"
              >
                <span class="material-symbols-outlined text-base">delete</span>
              </button>
            </div>
          </div>
        </section>

        <!-- Provision -->
        <section v-if="isProvision" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">settings_ethernet</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.provision') }}</h3>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block space-y-2">
              <span class="lp-label">Region</span>
              <input v-model="model.region" placeholder="us-central1" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Instance size</span>
              <select v-model="model.instanceSize" class="lp-input font-mono text-xs">
                <option value="" disabled>Select instance type</option>
                <option
                  v-for="opt in INSTANCE_SIZE_OPTIONS"
                  :key="opt.value"
                  :value="opt.value"
                >
                  {{ opt.label }}
                </option>
              </select>
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Cluster size</span>
              <input v-model="model.clusterSize" placeholder="3" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Resource count</span>
              <input v-model.number="model.resourceCount" type="number" min="1" class="lp-input">
            </label>
          </div>
        </section>

        <!-- CI/CD -->
        <section v-if="isCi" class="space-y-4">
          <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-2">
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">integration_instructions</span>
            <h3 class="lp-label">{{ t('manifest.configurator.sections.cicd') }}</h3>
          </div>
          <div class="space-y-4">
            <div class="grid gap-4 sm:grid-cols-2">
              <label class="block space-y-2">
                <span class="lp-label">Trigger branch</span>
                <input v-model="model.branch" placeholder="main" class="lp-input">
              </label>
              <label class="block space-y-2">
                <span class="lp-label">Runner image / type</span>
                <input v-model="model.runner" placeholder="ubuntu-latest" class="lp-input">
              </label>
            </div>

            <div class="space-y-4 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 p-4">
              <div>
                <p class="lp-label">Pipeline security</p>
                <p class="mt-1 text-xs text-[var(--lp-muted)]">
                  Saving regenerates the workflow with Solutions A/B. Action refs stay SHA-pinned.
                </p>
              </div>

              <label class="flex items-start gap-3">
                <input
                  v-model="model.cicdSecurity.containerScan.enabled"
                  type="checkbox"
                  class="mt-1 h-4 w-4 accent-[var(--lp-accent)]"
                >
                <span>
                  <span class="block text-sm font-medium">Container image scanning (Solution A)</span>
                  <span class="mt-0.5 block text-xs text-[var(--lp-muted)]">Trivy after build, before deploy</span>
                </span>
              </label>
              <div
                v-if="model.cicdSecurity.containerScan.enabled"
                class="grid gap-3 sm:grid-cols-2"
              >
                <label class="block space-y-1.5 sm:col-span-2">
                  <span class="lp-label">Container scanner</span>
                  <select v-model="model.cicdSecurity.containerScan.tool" class="lp-input font-mono text-xs">
                    <option
                      v-for="opt in containerScanToolOptions"
                      :key="opt.id"
                      :value="opt.id"
                    >
                      {{ opt.label }}
                    </option>
                  </select>
                  <span class="block font-mono text-[10px] text-[var(--lp-muted)]">
                    {{ containerScanToolOptions.find((o) => o.id === model?.cicdSecurity.containerScan.tool)?.hint }}
                  </span>
                </label>
                <label class="block space-y-1.5">
                  <span class="lp-label">Severity threshold</span>
                  <select v-model="model.cicdSecurity.containerScan.severityThreshold" class="lp-input">
                    <option value="critical">CRITICAL only</option>
                    <option value="critical_high">CRITICAL + HIGH</option>
                  </select>
                </label>
                <label class="block space-y-1.5">
                  <span class="lp-label">Action on finding</span>
                  <select v-model="model.cicdSecurity.containerScan.onFinding" class="lp-input">
                    <option value="block">Block deployment / fail job</option>
                    <option value="warn">Warn &amp; upload report</option>
                  </select>
                </label>
              </div>

              <label class="flex items-start gap-3 border-t border-[var(--lp-line)] pt-3">
                <input
                  v-model="model.cicdSecurity.sastGuardrails.enabled"
                  type="checkbox"
                  class="mt-1 h-4 w-4 accent-[var(--lp-accent)]"
                >
                <span>
                  <span class="block text-sm font-medium">SAST &amp; production protection (Solution B)</span>
                  <span class="mt-0.5 block text-xs text-[var(--lp-muted)]">Code analysis + rollout health / auto-rollback</span>
                </span>
              </label>
              <div
                v-if="model.cicdSecurity.sastGuardrails.enabled"
                class="space-y-3"
              >
                <label class="flex items-center gap-2 text-sm">
                  <input
                    v-model="model.cicdSecurity.sastGuardrails.enableSast"
                    type="checkbox"
                    class="h-4 w-4 accent-[var(--lp-accent)]"
                  >
                  Enable SAST code analysis
                </label>
                <label
                  v-if="model.cicdSecurity.sastGuardrails.enableSast"
                  class="block space-y-1.5"
                >
                  <span class="lp-label">SAST scanner</span>
                  <select v-model="model.cicdSecurity.sastGuardrails.sastTool" class="lp-input font-mono text-xs">
                    <option
                      v-for="opt in sastToolOptions"
                      :key="opt.id"
                      :value="opt.id"
                    >
                      {{ opt.label }}
                    </option>
                  </select>
                  <span class="block font-mono text-[10px] text-[var(--lp-muted)]">
                    {{ sastToolOptions.find((o) => o.id === model?.cicdSecurity.sastGuardrails.sastTool)?.hint }}
                  </span>
                </label>
                <label
                  v-if="model.cicdSecurity.sastGuardrails.enableSast && model.cicdSecurity.sastGuardrails.sastTool === 'codeql-v3.28.10'"
                  class="block space-y-1.5"
                >
                  <span class="lp-label">CodeQL language pack</span>
                  <select
                    :value="model.cicdSecurity.sastGuardrails.sastLanguages[0] ?? 'javascript-typescript'"
                    class="lp-input"
                    @change="model.cicdSecurity.sastGuardrails.sastLanguages = [($event.target as HTMLSelectElement).value as SastLanguage]"
                  >
                    <option value="javascript-typescript">JavaScript / TypeScript</option>
                    <option value="python">Python</option>
                    <option value="go">Go</option>
                    <option value="java-kotlin">Java / Kotlin</option>
                    <option value="csharp">C#</option>
                    <option value="ruby">Ruby</option>
                  </select>
                </label>
                <label class="flex items-center gap-2 text-sm">
                  <input
                    v-model="model.cicdSecurity.sastGuardrails.enableHealthRollback"
                    type="checkbox"
                    class="h-4 w-4 accent-[var(--lp-accent)]"
                  >
                  Automated health check &amp; instant rollback
                </label>
              </div>
            </div>

            <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 p-4">
              <div class="mb-3 flex items-center justify-between gap-3">
                <span class="lp-label">Build secret references</span>
                <button type="button" class="lp-btn-ghost py-1 text-xs uppercase tracking-wide" @click="addSecretRef">
                  Add reference
                </button>
              </div>
              <div class="space-y-2">
                <div
                  v-for="(item, idx) in model.buildSecrets"
                  :key="idx"
                  class="group flex items-center gap-2"
                >
                  <div class="grid flex-1 grid-cols-2 gap-2">
                    <input v-model="item.key" placeholder="ENV_KEY" class="lp-input py-1.5 text-xs">
                    <input v-model="item.value" placeholder="SECRET_NAME" class="lp-input py-1.5 text-xs">
                  </div>
                  <button
                    type="button"
                    class="rounded p-1 text-[var(--lp-muted)] opacity-0 transition hover:text-[var(--lp-danger)] group-hover:opacity-100"
                    @click="removeSecretRef(idx)"
                  >
                    <span class="material-symbols-outlined text-base">delete</span>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>

    <footer class="flex shrink-0 items-center border-t border-[var(--lp-line)] bg-[var(--lp-panel-2)]/70 px-5 py-3">
      <p class="font-mono text-xs text-[var(--lp-muted)]">
        <span v-if="statusMessage" class="text-[var(--lp-ok)]">{{ statusMessage }}</span>
        <span v-else>{{ hasChanges ? t('manifest.configurator.unsavedChanges') : t('manifest.configurator.noPendingChanges') }}</span>
      </p>
    </footer>

    <WorkspaceAiAnalysisDrawer
      :open="showAiAnalysis"
      :workspace-id="workspaceId"
      :path="selectedPath"
      :content="analysisContent"
      :persist-fix="applyAiImprovedContent"
      @update:open="(value) => { showAiAnalysis = value }"
      @error="onAiError"
    />

    <ConfirmDialog
      v-model:open="confirmDeleteOpen"
      :title="t('manifest.configurator.deleteTitle')"
      :message="selectedPath ? t('workspaceIde.dialogs.deletePathMessage', { path: selectedPath }) : ''"
      :confirm-label="t('common.confirmDelete')"
      :cancel-label="t('common.no')"
      :busy="deleting"
      @confirm="deleteSelectedFile"
    />
  </section>
</template>

<script setup lang="ts">
import type {
  IaCEngine,
  WorkspaceFileNode,
  WorkspaceTemplateInfo,
} from '~/types/provisioning'
import type { WorkspaceTreeNodeModel } from '~/utils/workspaceFileTree'
import { collectAnalyzablePaths } from '~/utils/workspaceFileAnalysis'
import { buildWorkspaceFileTree } from '~/utils/workspaceFileTree'
import { iacRunShortcuts } from '~/utils/workspaceInfraScaffold'
import { ApiError } from '~/composables/useApi'

const props = defineProps<{
  workspaceId: string
  engine: IaCEngine
}>()

const emit = defineEmits<{
  runCommand: [command: string]
}>()

const {
  listWorkspaceFiles,
  restoreWorkspaceFiles,
  readWorkspaceFile,
  writeWorkspaceFile,
  mkdirWorkspace,
  renameWorkspacePath,
  deleteWorkspacePath,
  formatWorkspaceContent,
  listTemplates,
  applyTemplate,
} = useProvisioning()

type TreeNode = WorkspaceTreeNodeModel

interface ContextMenuState {
  x: number
  y: number
  path: string
  type: 'file' | 'directory'
}

interface DropdownItem {
  label: string
  action: () => void
  danger?: boolean
}

const nodes = ref<WorkspaceFileNode[]>([])
const tree = ref<TreeNode[]>([])
const selectedPath = ref<string | null>(null)
const contextTargetPath = ref<string | null>(null)
const editorContent = ref('')
const savedContent = ref('')
const loadingTree = ref(false)
const restoringFiles = ref(false)
const filesMissing = ref(false)
const loadingFile = ref(false)
const saving = ref(false)
const statusMessage = ref<string | null>(null)
const errorMessage = ref<string | null>(null)

const k8sTemplates = ref<WorkspaceTemplateInfo[]>([])
const tfTemplates = ref<WorkspaceTemplateInfo[]>([])
const showNewFile = ref(false)
const showNewFolder = ref(false)
const showRename = ref(false)
const showTemplates = ref<'kubernetes' | 'terraform' | null>(null)
const showPush = ref(false)
const showAiAnalysis = ref(false)
const aiAnalysisTargets = ref<Array<{ path: string; content: string }>>([])
const aiAnalysisLoading = ref(false)
const confirmIacDestroy = ref<{ title: string; message: string; command: string } | null>(null)
const pendingDeletePath = ref<string | null>(null)
const deletingPath = ref(false)
const newName = ref('')
const openDropdown = ref<'k8s' | 'terraform' | 'pulumi' | null>(null)
const contextMenu = ref<ContextMenuState | null>(null)

const dirty = computed(() => editorContent.value !== savedContent.value)
const hasK8sFiles = computed(() =>
  nodes.value.some((n) => n.path.includes('/k8s/') || n.path.includes('/helm/')),
)
const fileName = computed(() => selectedPath.value?.split('/').pop() || 'Untitled')
const isDirectorySelected = computed(() => {
  if (!selectedPath.value) return false
  return nodes.value.some((n) => n.path === selectedPath.value && n.type === 'directory')
})
const isEditorOpen = computed(() => {
  if (!selectedPath.value) return false
  return !isDirectorySelected.value
})
const canAiAnalyze = computed(() => {
  if (!selectedPath.value || aiAnalysisLoading.value) return false
  if (isDirectorySelected.value) {
    return collectAnalyzablePaths(nodes.value, selectedPath.value).length > 0
  }
  return Boolean(editorContent.value.trim())
})

const k8sMenu = computed<DropdownItem[]>(() => [
  { label: 'Add YAML…', action: () => { void openTemplates('kubernetes') } },
  { label: 'kubectl apply (selected)', action: kubectlApplySelected },
  { label: 'kubectl delete (selected)', action: kubectlDeleteSelected },
  ...(hasK8sFiles.value
    ? [{ label: 'Apply all manifests', action: () => runCmd('kubectl apply -f infra/k8s/manifests/') }]
    : []),
])

const terraformMenu = computed<DropdownItem[]>(() => [
  { label: 'Add TF file…', action: () => { void openTemplates('terraform') } },
  ...iacRunShortcuts('terraform').map((item) => ({
    label: item.opensInitWizard ? `terraform ${item.label}…` : `terraform ${item.label}`,
    action: () => {
      if (item.danger) {
        requestIacDestroy(
          'Run terraform destroy?',
          'This will destroy cloud resources managed by Terraform in this workspace sandbox.',
          item.command,
        )
        return
      }
      runCmd(item.command)
    },
    danger: item.danger,
  })),
])

const pulumiMenu = computed<DropdownItem[]>(() =>
  iacRunShortcuts('pulumi').map((item) => ({
    label: item.opensInitWizard ? `pulumi ${item.label}…` : `pulumi ${item.label}`,
    action: () => {
      if (item.danger) {
        requestIacDestroy(
          'Run pulumi destroy?',
          'This will destroy cloud resources managed by Pulumi in this workspace sandbox.',
          item.command,
        )
        return
      }
      runCmd(item.command)
    },
    danger: item.danger,
  })),
)

function requestIacDestroy(title: string, message: string, command: string) {
  openDropdown.value = null
  confirmIacDestroy.value = { title, message, command }
}

function confirmIacDestroyRun() {
  const pending = confirmIacDestroy.value
  confirmIacDestroy.value = null
  if (!pending) return
  runCmd(pending.command)
}

function buildTree(flat: WorkspaceFileNode[]): TreeNode[] {
  return buildWorkspaceFileTree(flat)
}

async function refreshTree() {
  loadingTree.value = true
  errorMessage.value = null
  filesMissing.value = false
  try {
    nodes.value = await listWorkspaceFiles(props.workspaceId)
    tree.value = buildTree(nodes.value)
  } catch (err) {
    const code = err instanceof ApiError ? err.code : null
    filesMissing.value = code === 'workspace_files_missing'
    errorMessage.value = err instanceof Error ? err.message : 'Failed to load files'
    nodes.value = []
    tree.value = []
  } finally {
    loadingTree.value = false
  }
}

async function restoreFiles() {
  if (restoringFiles.value) return
  restoringFiles.value = true
  errorMessage.value = null
  try {
    await restoreWorkspaceFiles(props.workspaceId)
    filesMissing.value = false
    statusMessage.value = 'Workspace files restored'
    await refreshTree()
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Restore failed'
  } finally {
    restoringFiles.value = false
  }
}

async function openFile(path: string) {
  const isDir = nodes.value.some((n) => n.path === path && n.type === 'directory')
  if (isDir) {
    selectedPath.value = path
    contextMenu.value = null
    return
  }
  if (dirty.value && selectedPath.value !== path && !window.confirm('Discard unsaved changes?')) {
    return
  }
  loadingFile.value = true
  errorMessage.value = null
  contextMenu.value = null
  try {
    const file = await readWorkspaceFile(props.workspaceId, path)
    selectedPath.value = file.path
    editorContent.value = file.content
    savedContent.value = file.content
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Failed to open file'
  } finally {
    loadingFile.value = false
  }
}

async function saveFile() {
  if (!selectedPath.value || saving.value) return
  saving.value = true
  errorMessage.value = null
  try {
    const file = await writeWorkspaceFile(
      props.workspaceId,
      selectedPath.value,
      editorContent.value,
    )
    savedContent.value = file.content
    statusMessage.value = `Saved ${file.path}`
    await refreshTree()
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Save failed'
  } finally {
    saving.value = false
  }
}

async function formatFile() {
  if (!selectedPath.value) return
  try {
    const result = await formatWorkspaceContent(
      props.workspaceId,
      selectedPath.value,
      editorContent.value,
    )
    editorContent.value = result.content
    statusMessage.value = 'Formatted'
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Format failed'
  }
}

function actionBaseDir(): string {
  const target = contextTargetPath.value || selectedPath.value
  if (!target) return ''
  const isDir = nodes.value.some((n) => n.path === target && n.type === 'directory')
  if (isDir) return target
  const parts = target.split('/')
  return parts.slice(0, -1).join('/')
}

function openNewFileDialog(fromContext = false) {
  if (!fromContext) contextTargetPath.value = selectedPath.value
  showNewFile.value = true
  newName.value = ''
  contextMenu.value = null
  openDropdown.value = null
}

function openNewFolderDialog(fromContext = false) {
  if (!fromContext) contextTargetPath.value = selectedPath.value
  showNewFolder.value = true
  newName.value = ''
  contextMenu.value = null
  openDropdown.value = null
}

function openRenameDialog(fromContext = false) {
  const path = fromContext ? contextTargetPath.value : selectedPath.value
  if (!path) return
  contextTargetPath.value = path
  selectedPath.value = path
  showRename.value = true
  newName.value = path.split('/').pop() || ''
  contextMenu.value = null
  openDropdown.value = null
}

const copiedFileState = ref(false)

async function copyFileContent() {
  if (!editorContent.value) return
  try {
    await navigator.clipboard.writeText(editorContent.value)
    copiedFileState.value = true
    setTimeout(() => {
      copiedFileState.value = false
    }, 2000)
  } catch {
    // fallback
  }
}

function downloadActiveFile() {
  if (!selectedPath.value || !editorContent.value) return
  const fname = selectedPath.value.split('/').pop() || 'file'
  const blob = new Blob([editorContent.value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fname
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

async function createFile() {
  const name = newName.value.trim().replace(/^\/+/, '')
  if (!name) return
  const base = actionBaseDir()
  const path = base ? `${base}/${name}` : name
  try {
    await writeWorkspaceFile(props.workspaceId, path, '')
    showNewFile.value = false
    newName.value = ''
    await refreshTree()
    await openFile(path)
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Create file failed'
  }
}

async function createFolder() {
  const name = newName.value.trim().replace(/^\/+/, '')
  if (!name) return
  const base = actionBaseDir()
  const path = base ? `${base}/${name}` : name
  try {
    await mkdirWorkspace(props.workspaceId, path)
    showNewFolder.value = false
    newName.value = ''
    await refreshTree()
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Create folder failed'
  }
}

async function renameSelected() {
  const from = contextTargetPath.value || selectedPath.value
  if (!from) return
  const name = newName.value.trim().replace(/^\/+/, '')
  if (!name) return
  const parts = from.split('/')
  parts[parts.length - 1] = name
  const toPath = parts.join('/')
  try {
    await renameWorkspacePath(props.workspaceId, from, toPath)
    showRename.value = false
    newName.value = ''
    selectedPath.value = toPath
    contextTargetPath.value = toPath
    await refreshTree()
    if (nodes.value.some((n) => n.path === toPath && n.type === 'file')) {
      await openFile(toPath)
    }
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Rename failed'
  }
}

function requestDeletePath(path: string | null) {
  if (!path) return
  pendingDeletePath.value = path
  contextMenu.value = null
}

async function confirmDeletePath() {
  const path = pendingDeletePath.value
  if (!path || deletingPath.value) return
  deletingPath.value = true
  try {
    await deleteWorkspacePath(props.workspaceId, path)
    if (selectedPath.value === path || selectedPath.value?.startsWith(`${path}/`)) {
      selectedPath.value = null
      editorContent.value = ''
      savedContent.value = ''
    }
    pendingDeletePath.value = null
    await refreshTree()
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Delete failed'
  } finally {
    deletingPath.value = false
  }
}

async function ensureTemplates(category: 'kubernetes' | 'terraform') {
  if (category === 'kubernetes' && k8sTemplates.value.length === 0) {
    k8sTemplates.value = await listTemplates('kubernetes')
  }
  if (category === 'terraform' && tfTemplates.value.length === 0) {
    tfTemplates.value = await listTemplates('terraform')
  }
}

async function openTemplates(category: 'kubernetes' | 'terraform') {
  showTemplates.value = category
  openDropdown.value = null
  try {
    await ensureTemplates(category)
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Failed to load templates'
    showTemplates.value = null
  }
}

async function addTemplate(template: WorkspaceTemplateInfo) {
  try {
    const file = await applyTemplate(props.workspaceId, template.id)
    showTemplates.value = null
    await refreshTree()
    await openFile(file.path)
    statusMessage.value = `Added ${template.label}`
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Template failed'
  }
}

function runCmd(command: string) {
  emit('runCommand', command)
  statusMessage.value = `Sent to terminal: ${command}`
  openDropdown.value = null
}

function kubectlApplySelected() {
  if (!selectedPath.value) {
    runCmd('kubectl apply -f infra/k8s/manifests/')
    return
  }
  runCmd(`kubectl apply -f ${selectedPath.value}`)
}

function kubectlDeleteSelected() {
  if (!selectedPath.value) {
    runCmd('kubectl delete -f infra/k8s/manifests/')
    return
  }
  runCmd(`kubectl delete -f ${selectedPath.value}`)
}

function onTreeContextMenu(payload: {
  event: MouseEvent
  path: string
  type: 'file' | 'directory'
}) {
  contextTargetPath.value = payload.path
  if (payload.type === 'file') {
    selectedPath.value = payload.path
  }
  const pad = 8
  const menuW = 200
  const menuH = 180
  const x = Math.min(payload.event.clientX, window.innerWidth - menuW - pad)
  const y = Math.min(payload.event.clientY, window.innerHeight - menuH - pad)
  contextMenu.value = { x, y, path: payload.path, type: payload.type }
  openDropdown.value = null
}

function onExplorerBlankContext(event: MouseEvent) {
  event.preventDefault()
  contextTargetPath.value = null
  const pad = 8
  const x = Math.min(event.clientX, window.innerWidth - 200 - pad)
  const y = Math.min(event.clientY, window.innerHeight - 120 - pad)
  contextMenu.value = { x, y, path: '', type: 'directory' }
}

function toggleDropdown(id: 'k8s' | 'terraform' | 'pulumi') {
  openDropdown.value = openDropdown.value === id ? null : id
  contextMenu.value = null
}

async function openPush() {
  showPush.value = true
  openDropdown.value = null
}

async function openAiAnalysis() {
  if (!selectedPath.value || aiAnalysisLoading.value) return
  openDropdown.value = null
  const paths = collectAnalyzablePaths(nodes.value, selectedPath.value)
  if (!paths.length) {
    if (isEditorOpen.value && editorContent.value.trim()) {
      aiAnalysisTargets.value = [{ path: selectedPath.value, content: editorContent.value }]
      showAiAnalysis.value = true
      return
    }
    errorMessage.value = 'No analyzable files in the selection'
    return
  }
  aiAnalysisLoading.value = true
  errorMessage.value = null
  try {
    const targets: Array<{ path: string; content: string }> = []
    for (const path of paths) {
      if (path === selectedPath.value && isEditorOpen.value) {
        targets.push({ path, content: editorContent.value })
        continue
      }
      const file = await readWorkspaceFile(props.workspaceId, path)
      if (file.content.trim()) targets.push({ path: file.path, content: file.content })
    }
    if (!targets.length) {
      errorMessage.value = 'Selected files are empty'
      return
    }
    aiAnalysisTargets.value = targets
    showAiAnalysis.value = true
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Failed to load files for AI analysis'
  } finally {
    aiAnalysisLoading.value = false
  }
}

function onPushSuccess(fullName: string) {
  statusMessage.value = `Pushed to ${fullName}`
}

function onPushError(message: string) {
  errorMessage.value = message
}

async function applyAiImprovedContent(payload: { path: string; content: string }) {
  await writeWorkspaceFile(props.workspaceId, payload.path, payload.content)
  if (selectedPath.value === payload.path) {
    editorContent.value = payload.content
    savedContent.value = payload.content
  }
  statusMessage.value = `Applied AI fix to ${payload.path}`
  await refreshTree()
}

function onGlobalClick() {
  contextMenu.value = null
  openDropdown.value = null
}

function onKeydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key === 's') {
    event.preventDefault()
    void saveFile()
  }
  if (event.key === 'Escape') {
    contextMenu.value = null
    openDropdown.value = null
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('click', onGlobalClick)
  void refreshTree()
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('click', onGlobalClick)
})
</script>

<template>
  <section class="lp-glass overflow-hidden rounded-xl">
    <!-- Title / file actions bar -->
    <div class="flex flex-wrap items-center gap-1 border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/80 px-2 py-1.5">
      <p class="mr-auto px-1 text-[11px] font-medium uppercase tracking-wide text-[var(--lp-muted)]">
        Workspace IDE
      </p>
      <button
        type="button"
        class="lp-btn-ghost px-2 py-1 text-[12px] disabled:opacity-40"
        :disabled="!selectedPath || !editorContent"
        @click="copyFileContent"
      >
        <span class="material-symbols-outlined !text-[13px] mr-1">
          {{ copiedFileState ? 'check' : 'content_copy' }}
        </span>
        {{ copiedFileState ? 'Copied' : 'Copy' }}
      </button>
      <button
        type="button"
        class="lp-btn-ghost px-2 py-1 text-[12px] disabled:opacity-40"
        :disabled="!selectedPath || !editorContent"
        @click="downloadActiveFile"
      >
        <span class="material-symbols-outlined !text-[13px] mr-1">download</span>
        Download
      </button>
      <button
        type="button"
        class="lp-btn-ghost px-2 py-1 text-[12px]"
        :disabled="!selectedPath"
        @click="formatFile"
      >
        Format
      </button>
      <button
        type="button"
        class="lp-btn-ghost px-2 py-1 text-[12px] disabled:opacity-40"
        :disabled="!selectedPath || !dirty || saving"
        @click="saveFile"
      >
        {{ saving ? 'Saving…' : dirty ? 'Save' : 'Saved' }}
      </button>
      <button
        type="button"
        class="lp-btn-ghost px-2 py-1 text-[12px] disabled:opacity-40"
        :disabled="!canAiAnalyze"
        :title="isDirectorySelected ? 'Analyze all files in folder' : 'Analyze selected file'"
        @click="openAiAnalysis"
      >
        <span class="material-symbols-outlined !text-[13px] mr-1">auto_awesome</span>
        {{ aiAnalysisLoading ? 'Loading…' : isDirectorySelected ? 'AI analyze folder' : 'AI analyze' }}
      </button>
      <button
        type="button"
        class="lp-btn-primary px-2.5 py-1 text-[12px]"
        @click="openPush"
      >
        Publish
      </button>
    </div>

    <!-- Tool dropdowns -->
    <div class="relative z-20 flex flex-wrap items-center gap-2 border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/70 px-2 py-1.5">
      <div class="relative" @click.stop>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded border border-[var(--lp-line)] bg-[var(--lp-panel)] px-2.5 py-1 text-[12px] text-[var(--lp-text)] transition hover:border-[var(--lp-accent)]/40 hover:bg-[var(--lp-panel-2)]"
          :class="openDropdown === 'k8s' ? 'border-[var(--lp-accent)]/50 bg-[var(--lp-panel-2)]' : ''"
          @click="toggleDropdown('k8s')"
        >
          Kubernetes
          <span class="material-symbols-outlined !text-[14px] text-[var(--lp-muted)]">expand_more</span>
        </button>
        <div
          v-if="openDropdown === 'k8s'"
          class="absolute left-0 top-full z-30 mt-1 min-w-[220px] rounded border border-[var(--lp-line)] bg-[var(--lp-panel)] py-1 shadow-xl"
        >
          <button
            v-for="item in k8sMenu"
            :key="item.label"
            type="button"
            class="block w-full px-3 py-1.5 text-left text-[12px] text-[var(--lp-text)] transition hover:bg-[var(--lp-accent)]/15"
            @click="item.action()"
          >
            {{ item.label }}
          </button>
        </div>
      </div>

      <div class="relative" @click.stop>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded border border-[var(--lp-line)] bg-[var(--lp-panel)] px-2.5 py-1 text-[12px] text-[var(--lp-text)] transition hover:border-[var(--lp-accent)]/40 hover:bg-[var(--lp-panel-2)]"
          :class="openDropdown === 'terraform' ? 'border-[var(--lp-accent)]/50 bg-[var(--lp-panel-2)]' : ''"
          @click="toggleDropdown('terraform')"
        >
          Terraform
          <span class="material-symbols-outlined !text-[14px] text-[var(--lp-muted)]">expand_more</span>
        </button>
        <div
          v-if="openDropdown === 'terraform'"
          class="absolute left-0 top-full z-30 mt-1 min-w-[220px] rounded border border-[var(--lp-line)] bg-[var(--lp-panel)] py-1 shadow-xl"
        >
          <button
            v-for="item in terraformMenu"
            :key="item.label"
            type="button"
            class="block w-full px-3 py-1.5 text-left text-[12px] transition hover:bg-[var(--lp-accent)]/15"
            :class="item.danger ? 'text-[var(--lp-danger)]' : 'text-[var(--lp-text)]'"
            @click="item.action()"
          >
            {{ item.label }}
          </button>
        </div>
      </div>

      <div class="relative" @click.stop>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded border border-[var(--lp-line)] bg-[var(--lp-panel)] px-2.5 py-1 text-[12px] text-[var(--lp-text)] transition hover:border-[var(--lp-accent)]/40 hover:bg-[var(--lp-panel-2)]"
          :class="openDropdown === 'pulumi' ? 'border-[var(--lp-accent)]/50 bg-[var(--lp-panel-2)]' : ''"
          @click="toggleDropdown('pulumi')"
        >
          Pulumi
          <span class="material-symbols-outlined !text-[14px] text-[var(--lp-muted)]">expand_more</span>
        </button>
        <div
          v-if="openDropdown === 'pulumi'"
          class="absolute left-0 top-full z-30 mt-1 min-w-[200px] rounded border border-[var(--lp-line)] bg-[var(--lp-panel)] py-1 shadow-xl"
        >
          <button
            v-for="item in pulumiMenu"
            :key="item.label"
            type="button"
            class="block w-full px-3 py-1.5 text-left text-[12px] transition hover:bg-[var(--lp-accent)]/15"
            :class="item.danger ? 'text-[var(--lp-danger)]' : 'text-[var(--lp-text)]'"
            @click="item.action()"
          >
            {{ item.label }}
          </button>
        </div>
      </div>

      <span v-if="engine" class="ml-auto font-mono text-[11px] text-[var(--lp-muted)]">
        engine: {{ engine }}
      </span>
    </div>

    <p
      v-if="errorMessage"
      class="flex flex-wrap items-center gap-3 border-b border-[var(--lp-line)] bg-[var(--lp-danger)]/15 px-3 py-1.5 text-[12px] text-[var(--lp-danger)]"
    >
      <span class="min-w-0 flex-1">{{ errorMessage }}</span>
      <button
        v-if="filesMissing"
        type="button"
        class="shrink-0 rounded-md border border-[var(--lp-danger)]/40 bg-[var(--lp-panel)] px-2.5 py-1 font-mono text-[11px] text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-50"
        :disabled="restoringFiles"
        @click="restoreFiles"
      >
        {{ restoringFiles ? 'Restoring…' : 'Restore files' }}
      </button>
    </p>
    <p
      v-else-if="statusMessage"
      class="border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 px-3 py-1.5 text-[11px] text-[var(--lp-muted)]"
    >
      {{ statusMessage }}
    </p>

    <div class="grid min-h-[480px] lg:grid-cols-[260px_1fr]">
      <!-- Explorer -->
      <aside
        class="flex max-h-[560px] flex-col border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/65 lg:border-b-0 lg:border-r"
        @contextmenu="onExplorerBlankContext"
      >
        <div class="flex items-center justify-between px-3 py-2">
          <p class="text-[11px] font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
            Explorer
          </p>
          <div class="flex items-center gap-0.5">
            <button
              type="button"
              title="New File"
              class="rounded p-0.5 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel)] hover:text-[var(--lp-text)]"
              @click.stop="openNewFileDialog(false)"
            >
              <span class="material-symbols-outlined !text-[16px]">note_add</span>
            </button>
            <button
              type="button"
              title="New Folder"
              class="rounded p-0.5 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel)] hover:text-[var(--lp-text)]"
              @click.stop="openNewFolderDialog(false)"
            >
              <span class="material-symbols-outlined !text-[16px]">create_new_folder</span>
            </button>
            <button
              type="button"
              title="Refresh"
              class="rounded p-0.5 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel)] hover:text-[var(--lp-text)]"
              @click.stop="refreshTree"
            >
              <span class="material-symbols-outlined !text-[16px]">refresh</span>
            </button>
          </div>
        </div>
        <p v-if="loadingTree" class="px-3 text-[12px] text-[var(--lp-muted)]">Loading…</p>
        <ul class="flex-1 overflow-y-auto pb-3" @click.stop>
          <WorkspaceTreeNode
            v-for="node in tree"
            :key="node.path"
            :node="node"
            :selected-path="selectedPath"
            :depth="0"
            @select="openFile"
            @contextmenu="onTreeContextMenu"
          />
        </ul>
      </aside>

      <!-- Editor -->
      <div class="flex min-h-[480px] flex-col bg-[var(--lp-ink)]/55">
        <div class="flex items-end gap-0 border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/80">
          <div
            v-if="isEditorOpen"
            class="flex max-w-xs items-center gap-2 border-t-2 border-[var(--lp-accent)] bg-[var(--lp-panel)] px-3 py-1.5 text-[13px] text-[var(--lp-text)]"
          >
            <span class="truncate">{{ fileName }}</span>
            <span v-if="dirty" class="text-[var(--lp-muted)]">●</span>
          </div>
          <div v-else class="px-3 py-1.5 text-[12px] text-[var(--lp-muted)]">
            {{ isDirectorySelected ? `Folder: ${selectedPath}` : 'No file open' }}
          </div>
          <span v-if="loadingFile" class="ml-auto px-3 py-1.5 text-[11px] text-[var(--lp-muted)]">
            Loading…
          </span>
        </div>
        <ClientOnly>
          <div
            v-if="isEditorOpen"
            class="relative min-h-[420px] flex-1"
          >
            <WorkspaceMonacoEditor
              v-model="editorContent"
              class="absolute inset-0"
              :path="selectedPath"
              @save="saveFile"
            />
          </div>
          <div
            v-else
            class="flex flex-1 items-center justify-center bg-[var(--lp-panel)] text-[13px] text-[var(--lp-muted)]"
          >
            Select a file in the explorer to edit
          </div>
        </ClientOnly>
      </div>
    </div>

    <!-- Context menu -->
    <Teleport to="body">
      <div
        v-if="contextMenu"
        class="fixed z-[100] min-w-[200px] rounded border border-[var(--lp-line)] bg-[var(--lp-panel)] py-1 shadow-2xl"
        :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
        @click.stop
      >
        <button
          type="button"
          class="block w-full px-3 py-1.5 text-left text-[12px] text-[var(--lp-text)] transition hover:bg-[var(--lp-accent)]/15"
          @click="openNewFileDialog(true)"
        >
          New File…
        </button>
        <button
          type="button"
          class="block w-full px-3 py-1.5 text-left text-[12px] text-[var(--lp-text)] transition hover:bg-[var(--lp-accent)]/15"
          @click="openNewFolderDialog(true)"
        >
          New Folder…
        </button>
        <div v-if="contextMenu.path" class="my-1 border-t border-[var(--lp-line)]" />
        <button
          v-if="contextMenu.path && contextMenu.type === 'file'"
          type="button"
          class="block w-full px-3 py-1.5 text-left text-[12px] text-[var(--lp-text)] transition hover:bg-[var(--lp-accent)]/15"
          @click="openFile(contextMenu.path); contextMenu = null"
        >
          Open
        </button>
        <button
          v-if="contextMenu.path"
          type="button"
          class="block w-full px-3 py-1.5 text-left text-[12px] text-[var(--lp-text)] transition hover:bg-[var(--lp-accent)]/15"
          @click="openRenameDialog(true)"
        >
          Rename…
        </button>
        <button
          v-if="contextMenu.path"
          type="button"
          class="block w-full px-3 py-1.5 text-left text-[12px] text-[var(--lp-danger)] transition hover:bg-[var(--lp-danger)]/20"
          @click="requestDeletePath(contextMenu.path)"
        >
          Delete
        </button>
      </div>
    </Teleport>

    <!-- Centered dialogs -->
    <Teleport to="body">
      <div
        v-if="showNewFile || showNewFolder || showRename"
        class="fixed inset-0 z-[90] flex items-center justify-center bg-black/55 p-4"
        @click.self="showNewFile = showNewFolder = showRename = false"
      >
        <div class="w-full max-w-md space-y-4 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5 shadow-2xl">
          <h3 class="text-base font-semibold text-[var(--lp-text)]">
            {{ showRename ? 'Rename' : showNewFolder ? 'New Folder' : 'New File' }}
          </h3>
          <input
            v-model="newName"
            class="lp-input w-full"
            :placeholder="showNewFolder ? 'folder-name' : 'filename.ext'"
            autofocus
            @keydown.enter="showRename ? renameSelected() : showNewFolder ? createFolder() : createFile()"
          >
          <div class="flex justify-end gap-2">
            <button
              type="button"
              class="lp-btn-ghost px-3 py-1.5 text-[12px]"
              @click="showNewFile = showNewFolder = showRename = false"
            >
              Cancel
            </button>
            <button
              type="button"
              class="lp-btn-primary px-3 py-1.5 text-[12px]"
              @click="showRename ? renameSelected() : showNewFolder ? createFolder() : createFile()"
            >
              Confirm
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div
        v-if="showTemplates"
        class="fixed inset-0 z-[90] flex items-center justify-center bg-black/55 p-4"
        @click.self="showTemplates = null"
      >
        <div class="max-h-[80vh] w-full max-w-lg space-y-3 overflow-y-auto rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5 shadow-2xl">
          <h3 class="text-base font-semibold text-[var(--lp-text)]">
            {{ showTemplates === 'kubernetes' ? 'Add Kubernetes YAML' : 'Add Terraform file' }}
          </h3>
          <button
            v-for="tpl in showTemplates === 'kubernetes' ? k8sTemplates : tfTemplates"
            :key="tpl.id"
            type="button"
            class="flex w-full flex-col rounded border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 p-3 text-left transition hover:border-[var(--lp-accent)]/60 hover:bg-[var(--lp-panel-2)]/70"
            @click="addTemplate(tpl)"
          >
            <span class="text-[13px] font-medium text-[var(--lp-text)]">{{ tpl.label }}</span>
            <span class="text-[11px] text-[var(--lp-muted)]">{{ tpl.description }}</span>
            <span class="mt-1 font-mono text-[11px] text-[var(--lp-accent)]">{{ tpl.default_path }}</span>
          </button>
          <button
            type="button"
            class="lp-btn-ghost px-3 py-1.5 text-[12px]"
            @click="showTemplates = null"
          >
            Close
          </button>
        </div>
      </div>
    </Teleport>

    <WorkspaceGithubPushModal
      :open="showPush"
      :workspace-id="workspaceId"
      @update:open="(value) => { showPush = value }"
      @pushed="onPushSuccess"
      @converted="(message) => { statusMessage = message }"
      @error="onPushError"
    />

    <WorkspaceAiAnalysisDrawer
      :open="showAiAnalysis"
      :workspace-id="workspaceId"
      :targets="aiAnalysisTargets"
      :persist-fix="applyAiImprovedContent"
      @update:open="(value) => { showAiAnalysis = value }"
      @error="onPushError"
    />

    <ConfirmDialog
      :open="confirmIacDestroy !== null"
      :title="confirmIacDestroy?.title ?? 'Destroy resources?'"
      :message="confirmIacDestroy?.message ?? ''"
      confirm-label="Yes, destroy"
      cancel-label="No"
      @update:open="(value) => { if (!value) confirmIacDestroy = null }"
      @confirm="confirmIacDestroyRun"
    />

    <ConfirmDialog
      :open="pendingDeletePath !== null"
      title="Delete path?"
      :message="pendingDeletePath ? `Delete “${pendingDeletePath}” from this workspace? This cannot be undone.` : ''"
      confirm-label="Yes, delete"
      cancel-label="No"
      :busy="deletingPath"
      @update:open="(value) => { if (!value) pendingDeletePath = null }"
      @confirm="confirmDeletePath"
    />
  </section>
</template>

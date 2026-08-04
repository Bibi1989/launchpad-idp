<script setup lang="ts">
import type { WorkspaceFileNode } from '~/types/provisioning'
import { ApiError } from '~/composables/useApi'
import { inferInfraManifestKind } from '~/utils/infraManifestMapper'
import type { WorkspaceTreeNodeModel } from '~/utils/workspaceFileTree'
import { buildWorkspaceFileTree } from '~/utils/workspaceFileTree'

const props = defineProps<{
  workspaceId: string
  modelValue: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const { listWorkspaceFiles, restoreWorkspaceFiles } = useProvisioning()
const loading = ref(false)
const restoring = ref(false)
const filesMissing = ref(false)
const loadError = ref<string | null>(null)
const nodes = ref<WorkspaceFileNode[]>([])
const tree = ref<WorkspaceTreeNodeModel[]>([])

const filePaths = computed(() =>
  nodes.value.filter((node) => node.type === 'file').map((node) => node.path),
)

const fileCount = computed(() => filePaths.value.length)

function pickDefaultFile(files: string[]): string | undefined {
  const mapped = files.find((path) => inferInfraManifestKind(path) !== 'unknown')
  return mapped ?? files[0]
}

function onSelect(path: string) {
  const node = nodes.value.find((item) => item.path === path)
  if (!node || node.type === 'directory') return
  emit('update:modelValue', path)
}

watch(
  filePaths,
  (files) => {
    if (!files.length) return
    if (!props.modelValue || !files.includes(props.modelValue)) {
      const next = pickDefaultFile(files)
      if (next) emit('update:modelValue', next)
    }
  },
  { immediate: true },
)

async function loadFiles() {
  loading.value = true
  loadError.value = null
  filesMissing.value = false
  try {
    nodes.value = await listWorkspaceFiles(props.workspaceId)
    tree.value = buildWorkspaceFileTree(nodes.value)
  } catch (err) {
    filesMissing.value = err instanceof ApiError && err.code === 'workspace_files_missing'
    loadError.value = err instanceof Error ? err.message : 'Failed to list infra files'
    nodes.value = []
    tree.value = []
  } finally {
    loading.value = false
  }
}

async function restoreFiles() {
  if (restoring.value) return
  restoring.value = true
  loadError.value = null
  try {
    await restoreWorkspaceFiles(props.workspaceId)
    filesMissing.value = false
    await loadFiles()
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Restore failed'
  } finally {
    restoring.value = false
  }
}

watch(
  () => props.workspaceId,
  async () => {
    await loadFiles()
  },
  { immediate: true },
)
</script>

<template>
  <aside class="flex w-full lg:w-[280px] shrink-0 flex-col border-b lg:border-b-0 lg:border-r border-[var(--lp-line)] bg-[var(--lp-panel-2)]/50 lg:sticky lg:top-4 lg:max-h-[calc(100vh-6rem)] h-[400px] lg:h-[calc(100vh-6rem)]">
    <div class="flex items-center justify-between border-b border-[var(--lp-line)] px-4 py-3 shrink-0">
      <div class="min-w-0">
        <h2 class="lp-label">Explorer</h2>
        <p class="mt-0.5 font-mono text-[10px] text-[var(--lp-muted)]">
          {{ loading ? '…' : `${fileCount} file${fileCount === 1 ? '' : 's'}` }}
        </p>
      </div>
      <button
        type="button"
        title="Refresh"
        class="rounded-md p-1.5 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel)] hover:text-[var(--lp-text)]"
        @click="loadFiles"
      >
        <span class="material-symbols-outlined text-base">refresh</span>
      </button>
    </div>

    <div class="flex-1 overflow-y-auto px-2 py-2">
      <p v-if="loading" class="px-2 py-2 font-mono text-xs text-[var(--lp-muted)]">Loading…</p>
      <div v-else-if="loadError" class="space-y-2 px-2 py-2">
        <p class="font-mono text-xs text-[var(--lp-danger)]">{{ loadError }}</p>
        <button
          v-if="filesMissing"
          type="button"
          class="rounded-md border border-[var(--lp-line)] bg-[var(--lp-panel)] px-2 py-1 font-mono text-[11px] text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-50"
          :disabled="restoring"
          @click="restoreFiles"
        >
          {{ restoring ? 'Restoring…' : 'Restore files' }}
        </button>
      </div>
      <p v-else-if="!tree.length" class="px-2 py-2 font-mono text-xs text-[var(--lp-muted)]">
        No files found in workspace.
      </p>
      <ul v-else class="space-y-0.5">
        <WorkspaceTreeNode
          v-for="node in tree"
          :key="node.path"
          :node="node"
          :selected-path="modelValue"
          :depth="0"
          tone="panel"
          @select="onSelect"
        />
      </ul>
    </div>

    <div class="border-t border-[var(--lp-line)] bg-[var(--lp-panel)]/50 px-4 py-3 shrink-0">
      <p class="font-mono text-[10px] leading-relaxed text-[var(--lp-muted)]">
        Select a mapped file to edit structured fields.
      </p>
    </div>
  </aside>
</template>

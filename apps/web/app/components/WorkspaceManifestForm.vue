<script setup lang="ts">
const props = defineProps<{
  workspaceId: string
  files: string[]
}>()

const emit = defineEmits<{
  saved: [path: string]
  error: [message: string]
}>()

const { t } = useI18n()
const { readWorkspaceFile, writeWorkspaceFile } = useProvisioning()

const selectedPath = ref<string | null>(null)
const editorContent = ref('')
const savedContent = ref('')
const loadingFile = ref(false)
const saving = ref(false)
const statusMessage = ref<string | null>(null)

const editableFiles = computed(() =>
  props.files
    .filter(
      (path) =>
        (path.startsWith('infra/') || path.startsWith('ci/')) && !path.endsWith('/'),
    )
    .sort((a, b) => a.localeCompare(b)),
)

const dirty = computed(() => editorContent.value !== savedContent.value)

watch(
  editableFiles,
  (paths) => {
    if (!paths.length) {
      selectedPath.value = null
      editorContent.value = ''
      savedContent.value = ''
      return
    }
    if (!selectedPath.value || !paths.includes(selectedPath.value)) {
      selectedPath.value = paths[0] ?? null
    }
  },
  { immediate: true },
)

watch(selectedPath, async (path) => {
  if (!path) {
    editorContent.value = ''
    savedContent.value = ''
    return
  }
  loadingFile.value = true
  statusMessage.value = null
  try {
    const file = await readWorkspaceFile(props.workspaceId, path)
    editorContent.value = file.content
    savedContent.value = file.content
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to load file'
    emit('error', message)
  } finally {
    loadingFile.value = false
  }
})

async function onSave() {
  if (!selectedPath.value || saving.value || !dirty.value) return
  saving.value = true
  statusMessage.value = null
  try {
    await writeWorkspaceFile(props.workspaceId, selectedPath.value, editorContent.value)
    savedContent.value = editorContent.value
    statusMessage.value = `Saved ${selectedPath.value}`
    emit('saved', selectedPath.value)
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to save file'
    emit('error', message)
  } finally {
    saving.value = false
  }
}

function onEditorUpdate(value: string) {
  editorContent.value = value
}
</script>

<template>
  <section class="space-y-4 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-4">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p class="lp-label">{{ t('manifest.form.label') }}</p>
        <h2 class="text-lg font-semibold">{{ t('manifest.form.title') }}</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">
          {{ t('manifest.form.blurb') }}
        </p>
      </div>
      <button
        type="button"
        class="lp-btn-primary text-xs uppercase tracking-wide"
        :disabled="!selectedPath || saving || !dirty"
        @click="onSave"
      >
        {{ saving ? t('common.saving') : t('manifest.form.saveChanges') }}
      </button>
    </div>

    <div
      v-if="!editableFiles.length"
      class="rounded-xl border border-dashed border-[var(--lp-line)] px-6 py-10 text-center text-sm text-[var(--lp-muted)]"
    >
      {{ t('manifest.form.empty') }}
    </div>

    <div v-else class="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
      <div class="space-y-2">
        <p class="lp-label">{{ t('manifest.form.files') }}</p>
        <div class="max-h-[420px] space-y-1 overflow-y-auto rounded-lg border border-[var(--lp-line)] p-2">
          <button
            v-for="path in editableFiles"
            :key="path"
            type="button"
            class="block w-full rounded-md px-2 py-1.5 text-left font-mono text-xs transition"
            :class="
              selectedPath === path
                ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]'
                : 'text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]'
            "
            @click="selectedPath = path"
          >
            {{ path }}
          </button>
        </div>
      </div>

      <div class="min-h-[420px] rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/30">
        <p v-if="loadingFile" class="p-4 text-sm text-[var(--lp-muted)]">{{ t('manifest.form.loadingFile') }}</p>
        <ClientOnly v-else-if="selectedPath">
          <WorkspaceMonacoEditor
            :model-value="editorContent"
            :path="selectedPath"
            @update:model-value="onEditorUpdate"
            @save="onSave"
          />
        </ClientOnly>
        <p v-else class="p-4 text-sm text-[var(--lp-muted)]">{{ t('manifest.form.selectFile') }}</p>
      </div>
    </div>

    <p v-if="statusMessage" class="text-sm text-[var(--lp-ok)]">{{ statusMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import type { WorkspaceListItem } from '~/types/provisioning'
import { artifactModeLabel, workspaceStackLabel } from '~/utils/workspaceDisplay'

const { t } = useI18n()
const route = useRoute()
const { listWorkspaces, destroyWorkspace, setWorkspaceStarred } = useProvisioning()
const workspaces = ref<WorkspaceListItem[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const destroyingId = ref<string | null>(null)
const starringId = ref<string | null>(null)
const confirmDestroyId = ref<string | null>(null)
const confirmDestroyOpen = computed({
  get: () => confirmDestroyId.value !== null,
  set: (open: boolean) => {
    if (!open) confirmDestroyId.value = null
  },
})
const importOpen = ref(false)

const pendingDestroyName = computed(() => {
  const id = confirmDestroyId.value
  if (!id) return ''
  return workspaces.value.find((ws) => ws.id === id)?.name ?? 'this workspace'
})

async function refresh() {
  loading.value = true
  error.value = null
  try {
    workspaces.value = await listWorkspaces()
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('workspaces.errors.load')
  } finally {
    loading.value = false
  }
}

function requestDestroy(id: string) {
  if (destroyingId.value) return
  confirmDestroyId.value = id
}

async function toggleStar(ws: WorkspaceListItem) {
  if (starringId.value) return
  starringId.value = ws.id
  error.value = null
  try {
    const updated = await setWorkspaceStarred(ws.id, !ws.starred)
    const idx = workspaces.value.findIndex((row) => row.id === ws.id)
    if (idx >= 0) workspaces.value[idx] = updated
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('workspaces.errors.star')
  } finally {
    starringId.value = null
  }
}

async function onDestroy() {
  const id = confirmDestroyId.value
  if (!id || destroyingId.value) return
  confirmDestroyId.value = null
  destroyingId.value = id
  try {
    await destroyWorkspace(id)
    await refresh()
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('workspaces.errors.destroy')
  } finally {
    destroyingId.value = null
  }
}

function openImport() {
  importOpen.value = true
}

async function onImportSaved() {
  importOpen.value = false
  await refresh()
}

onMounted(async () => {
  await refresh()
  if (route.query.import === '1' || route.query.import === 'true') {
    importOpen.value = true
  }
})

watch(
  () => route.query.import,
  (value) => {
    if (value === '1' || value === 'true') importOpen.value = true
  },
)
</script>

<template>
  <div class="space-y-8 animate-fade-up">
    <header class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 class="text-3xl font-semibold tracking-tight">{{ t('workspaces.index.title') }}</h1>
        <p class="mt-1 max-w-xl text-sm text-[var(--lp-muted)]">
          {{ t('workspaces.index.blurb') }}
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="lp-btn-ghost" @click="openImport">
          <span class="material-symbols-outlined text-base">download</span>
          {{ t('workspaces.index.import') }}
        </button>
        <NuxtLink to="/provision" class="lp-btn-primary">
          <span class="material-symbols-outlined text-base">add</span>
          {{ t('workspaces.index.create') }}
        </NuxtLink>
      </div>
    </header>

    <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">{{ t('common.loading') }}</p>

    <div
      v-else-if="workspaces.length === 0"
      class="rounded-xl border border-dashed border-[var(--lp-line)] px-6 py-12 text-center"
    >
      <span class="material-symbols-outlined mb-3 text-4xl text-[var(--lp-muted)]">folder_off</span>
      <p class="text-sm text-[var(--lp-muted)]">{{ t('workspaces.index.empty') }}</p>
      <div class="mt-4 flex flex-wrap justify-center gap-2">
        <button type="button" class="lp-btn-ghost inline-flex" @click="openImport">
          {{ t('workspaces.index.importCta') }}
        </button>
        <NuxtLink to="/provision" class="lp-btn-primary inline-flex">{{ t('workspaces.index.createCta') }}</NuxtLink>
      </div>
    </div>

    <div v-else class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <article
        v-for="ws in workspaces"
        :key="ws.id"
        class="lp-glass cursor-pointer overflow-hidden rounded-xl transition hover:border-[var(--lp-accent)]/40"
        role="link"
        tabindex="0"
        :aria-label="ws.name"
        @click="navigateTo(`/workspaces/${ws.id}`)"
        @keydown.enter.prevent="navigateTo(`/workspaces/${ws.id}`)"
        @keydown.space.prevent="navigateTo(`/workspaces/${ws.id}`)"
      >
        <div class="flex items-start justify-between gap-3 border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 px-4 py-4">
          <div class="min-w-0">
            <h3 class="truncate text-base font-semibold">{{ ws.name }}</h3>
            <p class="mt-1 font-mono text-xs text-[var(--lp-muted)]">
              {{ workspaceStackLabel(ws) }}
            </p>
            <p class="mt-1">
              <span class="rounded border border-[var(--lp-line)] bg-[var(--lp-panel)] px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
                {{ artifactModeLabel(ws.artifact_mode) }}
              </span>
            </p>
          </div>
          <div class="flex shrink-0 gap-2" @click.stop>
            <button
              type="button"
              class="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--lp-line)] transition hover:bg-[var(--lp-panel)]"
              :class="ws.starred ? 'text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
              :disabled="starringId === ws.id"
              :aria-pressed="ws.starred"
              :aria-label="ws.starred ? t('catalog.index.unstar', { name: ws.name }) : t('catalog.index.yours')"
              :title="ws.starred ? t('catalog.index.unstarAction') : t('catalog.index.yours')"
              @click="toggleStar(ws)"
            >
              <span
                class="material-symbols-outlined text-base"
                :class="ws.starred ? 'filled' : ''"
              >
                star
              </span>
            </button>
            <NuxtLink :to="`/workspaces/${ws.id}`" class="lp-btn-ghost px-3 py-1.5 text-xs">
              {{ t('workspaces.index.open') }}
            </NuxtLink>
            <button
              type="button"
              class="lp-btn-danger px-3 py-1.5 text-xs"
              :disabled="destroyingId === ws.id"
              @click="requestDestroy(ws.id)"
            >
              {{ t('workspaces.index.destroy') }}
            </button>
          </div>
        </div>
      </article>
    </div>

    <ConfirmDialog
      v-model:open="confirmDestroyOpen"
      :title="t('workspaces.destroy.title')"
      :message="`Destroy workspace “${pendingDestroyName}”? Generated files and the sandbox will be removed. This cannot be undone.`"
      :confirm-label="t('workspaces.index.destroy')"
      :busy="destroyingId !== null"
      @confirm="onDestroy"
    />

    <RepoImporterModal
      v-model:open="importOpen"
      @saved="onImportSaved"
    />
  </div>
</template>

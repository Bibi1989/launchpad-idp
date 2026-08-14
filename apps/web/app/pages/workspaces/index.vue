<script setup lang="ts">
import type { WorkspaceListItem } from '~/types/provisioning'
import type { ProjectSummary } from '~/types/auth'
import { artifactModeLabel, workspaceStackLabel } from '~/utils/workspaceDisplay'

const { t } = useI18n()
const route = useRoute()
const { listWorkspaces, destroyWorkspace, setWorkspaceStarred } = useProvisioning()
const { getProject } = useProjects()
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
const filterProject = ref<ProjectSummary | null>(null)

const projectIdFilter = computed(() => {
  const raw = route.query.project_id
  return typeof raw === 'string' && raw.trim() ? raw.trim() : null
})

const provisionHref = computed(() =>
  projectIdFilter.value
    ? `/provision?project_id=${encodeURIComponent(projectIdFilter.value)}`
    : '/provision',
)

const pendingDestroyName = computed(() => {
  const id = confirmDestroyId.value
  if (!id) return ''
  return workspaces.value.find((ws) => ws.id === id)?.name ?? 'this workspace'
})

async function refresh() {
  loading.value = true
  error.value = null
  try {
    workspaces.value = await listWorkspaces({
      projectId: projectIdFilter.value,
    })
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('workspaces.errors.load')
  } finally {
    loading.value = false
  }
}

async function loadFilterProject() {
  filterProject.value = null
  if (!projectIdFilter.value) return
  try {
    filterProject.value = await getProject(projectIdFilter.value)
  } catch {
    filterProject.value = null
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
  await loadFilterProject()
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

watch(
  projectIdFilter,
  async () => {
    await loadFilterProject()
    await refresh()
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
        <NuxtLink :to="provisionHref" class="lp-btn-primary">
          <span class="material-symbols-outlined text-base">add</span>
          {{ t('workspaces.index.create') }}
        </NuxtLink>
      </div>
    </header>

    <div
      v-if="projectIdFilter"
      class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/5 px-4 py-3"
    >
      <div class="min-w-0">
        <p class="lp-label">{{ t('workspaces.index.filteredByProject') }}</p>
        <p class="truncate text-sm font-medium text-[var(--lp-text)]">
          {{ filterProject?.name || projectIdFilter }}
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <NuxtLink
          v-if="filterProject"
          :to="`/projects/${filterProject.id}`"
          class="lp-btn-ghost text-xs"
        >
          {{ t('workspaces.index.backToProject') }}
        </NuxtLink>
        <NuxtLink to="/workspaces" class="lp-btn-ghost text-xs">
          {{ t('workspaces.index.clearProjectFilter') }}
        </NuxtLink>
      </div>
    </div>

    <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
    <AppSplash
      v-else-if="loading"
      compact
      :message="t('common.loading')"
    />

    <div
      v-else-if="workspaces.length === 0"
      class="rounded-xl border border-dashed border-[var(--lp-line)] px-6 py-12 text-center"
    >
      <span class="material-symbols-outlined mb-3 text-4xl text-[var(--lp-muted)]">folder_off</span>
      <p class="text-sm text-[var(--lp-muted)]">
        {{
          projectIdFilter
            ? t('workspaces.index.emptyForProject')
            : t('workspaces.index.empty')
        }}
      </p>
      <div class="mt-4 flex flex-wrap justify-center gap-2">
        <button type="button" class="lp-btn-ghost inline-flex" @click="openImport">
          {{ t('workspaces.index.importCta') }}
        </button>
        <NuxtLink :to="provisionHref" class="lp-btn-primary inline-flex">{{ t('workspaces.index.createCta') }}</NuxtLink>
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
            <div class="mt-2 flex flex-wrap items-center gap-1.5">
              <WorkspaceRuntimeModeBadge :runtime-mode="ws.runtime_mode" />
              <ProjectBadge
                :project-id="ws.project_id"
                :project-name="ws.project_name"
              />
              <span class="rounded border border-[var(--lp-line)] px-1.5 py-0.5 font-mono text-[10px] uppercase text-[var(--lp-muted)]">
                {{ artifactModeLabel(ws.artifact_mode) }}
              </span>
            </div>
          </div>
          <button
            type="button"
            class="lp-btn-ghost shrink-0 p-1.5"
            :disabled="starringId === ws.id"
            :aria-label="ws.starred ? t('catalog.index.unstarAction') : 'Star'"
            :title="ws.starred ? t('catalog.index.unstarAction') : 'Star'"
            @click.stop="toggleStar(ws)"
          >
            <span
              class="material-symbols-outlined text-base"
              :class="ws.starred ? 'text-[var(--lp-warn)]' : ''"
            >
              {{ ws.starred ? 'star' : 'star_outline' }}
            </span>
          </button>
        </div>
        <div class="flex items-center justify-between gap-2 px-4 py-3">
          <span class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
            {{ ws.status }}
          </span>
          <button
            type="button"
            class="lp-btn-ghost text-xs text-[var(--lp-danger)]"
            :disabled="destroyingId === ws.id"
            @click.stop="requestDestroy(ws.id)"
          >
            {{ t('workspaces.index.destroy') }}
          </button>
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
      :launchpad-project-id="projectIdFilter"
      @saved="onImportSaved"
    />
  </div>
</template>

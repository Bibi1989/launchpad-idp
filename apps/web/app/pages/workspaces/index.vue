<script setup lang="ts">
import type { WorkspaceListItem } from '~/types/provisioning'
import { artifactModeLabel, workspaceStackLabel } from '~/utils/workspaceDisplay'

const { listWorkspaces, destroyWorkspace } = useProvisioning()
const workspaces = ref<WorkspaceListItem[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const destroyingId = ref<string | null>(null)
const confirmDestroyId = ref<string | null>(null)

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
    error.value = err instanceof Error ? err.message : 'Failed to load workspaces'
  } finally {
    loading.value = false
  }
}

function requestDestroy(id: string) {
  if (destroyingId.value) return
  confirmDestroyId.value = id
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
    error.value = err instanceof Error ? err.message : 'Failed to destroy workspace'
  } finally {
    destroyingId.value = null
  }
}

onMounted(refresh)
</script>

<template>
  <div class="space-y-8 animate-fade-up">
    <header class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 class="text-3xl font-semibold tracking-tight">Workspaces</h1>
        <p class="mt-1 max-w-xl text-sm text-[var(--lp-muted)]">
          Generated Terraform and Pulumi bundles ready for sandbox execution or GitHub bootstrap.
        </p>
      </div>
      <NuxtLink to="/provision" class="lp-btn-primary">
        <span class="material-symbols-outlined text-base">add</span>
        New workspace
      </NuxtLink>
    </header>

    <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">Loading…</p>

    <div
      v-else-if="workspaces.length === 0"
      class="rounded-xl border border-dashed border-[var(--lp-line)] px-6 py-12 text-center"
    >
      <span class="material-symbols-outlined mb-3 text-4xl text-[var(--lp-muted)]">folder_off</span>
      <p class="text-sm text-[var(--lp-muted)]">No workspaces yet.</p>
      <NuxtLink to="/provision" class="lp-btn-primary mt-4 inline-flex">Create one</NuxtLink>
    </div>

    <div v-else class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <article
        v-for="ws in workspaces"
        :key="ws.id"
        class="lp-glass overflow-hidden rounded-xl transition hover:border-[var(--lp-accent)]/40"
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
          <span
            class="shrink-0 rounded border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 px-2 py-1 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-accent)]"
          >
            {{ ws.status }}
          </span>
        </div>
        <div class="space-y-3 p-4">
          <p class="font-mono text-xs text-[var(--lp-muted)]">
            Created {{ new Date(ws.created_at).toLocaleString() }}
          </p>
          <div class="flex flex-wrap gap-2">
            <NuxtLink
              :to="`/workspaces/${ws.id}`"
              class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide"
            >
              <span class="material-symbols-outlined text-sm">terminal</span>
              Open
            </NuxtLink>
            <NuxtLink
              :to="`/launch?workspace=${ws.id}`"
              class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide"
            >
              <span class="material-symbols-outlined text-sm">deployed_code</span>
              Create env
            </NuxtLink>
            <button
              type="button"
              class="lp-btn-danger py-1.5 text-xs uppercase tracking-wide"
              :disabled="destroyingId === ws.id"
              @click="requestDestroy(ws.id)"
            >
              {{ destroyingId === ws.id ? 'Destroying…' : 'Destroy' }}
            </button>
          </div>
        </div>
      </article>
    </div>

    <ConfirmDialog
      :open="confirmDestroyId !== null"
      title="Destroy workspace?"
      :message="`Destroy IaC workspace “${pendingDestroyName}”? Generated files and the sandbox will be removed. This cannot be undone.`"
      confirm-label="Yes, destroy"
      cancel-label="No"
      :busy="destroyingId !== null"
      @update:open="(value) => { if (!value) confirmDestroyId = null }"
      @confirm="onDestroy"
    />
  </div>
</template>

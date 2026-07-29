<script setup lang="ts">
import type { AuditLogEntry } from '~/types/environment'
import type { IaCBundleSummary } from '~/types/provisioning'
import { artifactModeLabel, workspaceStackParts } from '~/utils/workspaceDisplay'

const WorkspaceIde = defineAsyncComponent(() => import('~/components/WorkspaceIde.vue'))

const route = useRoute()
const workspaceId = computed(() => String(route.params.id))
const { getWorkspace, openTerminal, destroyWorkspace, listAudits } = useProvisioning()
const activeTerminalWsPath = useState<string | null>('lp-terminal-ws-path', () => null)

const workspace = ref<IaCBundleSummary | null>(null)
const loadError = ref<string | null>(null)
const loading = ref(true)
const openingTerminal = ref(false)
const destroying = ref(false)
const confirmDestroyOpen = ref(false)
const wsPath = ref<string | null>(null)
const runInit = ref(false)
const advancedMode = useState('lp-workspace-advanced', () => false)
const detailsOpen = ref(false)
const selectedInfraFile = ref<string | null>(null)
const audits = ref<AuditLogEntry[]>([])
const auditsLoading = ref(false)

const stackParts = computed(() =>
  workspace.value ? workspaceStackParts(workspace.value) : null,
)

watch(advancedMode, (enabled) => {
  if (import.meta.client) {
    localStorage.setItem('lp-workspace-advanced', enabled ? '1' : '0')
  }
})

async function load() {
  loading.value = true
  loadError.value = null
  try {
    workspace.value = await getWorkspace(workspaceId.value)
    auditsLoading.value = true
    try {
      audits.value = await listAudits(workspaceId.value)
    } catch {
      audits.value = []
    } finally {
      auditsLoading.value = false
    }
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Failed to load workspace'
    workspace.value = null
  } finally {
    loading.value = false
  }
}

async function onOpenTerminal() {
  if (openingTerminal.value) return
  openingTerminal.value = true
  loadError.value = null
  try {
    const session = await openTerminal(workspaceId.value, { run_init: runInit.value })
    wsPath.value = session.ws_path
    activeTerminalWsPath.value = session.ws_path
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Failed to open terminal'
  } finally {
    openingTerminal.value = false
  }
}

function requestDestroy() {
  if (destroying.value || !workspace.value) return
  confirmDestroyOpen.value = true
}

async function onDestroy() {
  if (destroying.value || !workspace.value) return
  confirmDestroyOpen.value = false
  destroying.value = true
  try {
    await destroyWorkspace(workspaceId.value)
    activeTerminalWsPath.value = null
    await navigateTo('/workspaces')
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Failed to destroy workspace'
  } finally {
    destroying.value = false
  }
}

function onRunCommand(command: string) {
  if (!wsPath.value) {
    loadError.value = 'Open the sandbox terminal before running commands'
    return
  }
  const queue = useState<string[]>('lp-terminal-cmd-queue', () => [])
  queue.value = [...queue.value, command]
}

function onSetupError(message: string) {
  loadError.value = message
}

function onConfiguratorSaved() {
  // Avoid full workspace reload: remounting the form after save re-fetches and
  // re-parses files for no benefit.
}

onMounted(async () => {
  await load()
})

watch(advancedMode, async (enabled) => {
  if (!enabled) return
  if (!wsPath.value) {
    loadError.value = null
  }
})
</script>

<template>
  <div class="space-y-8 animate-fade-up">
    <NuxtLink
      to="/workspaces"
      class="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-wide text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
    >
      <span class="material-symbols-outlined text-sm">arrow_back</span>
      Workspaces
    </NuxtLink>

    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">Loading workspace…</p>
    <p v-else-if="loadError" class="text-sm text-[var(--lp-danger)]">{{ loadError }}</p>

    <template v-if="workspace">
      <section class="lp-glass overflow-hidden rounded-xl">
        <div
          class="flex flex-col gap-4 bg-[var(--lp-panel-2)]/40 px-5 py-4 lg:flex-row lg:items-center lg:justify-between"
          :class="detailsOpen ? 'border-b border-[var(--lp-line)]' : ''"
        >
          <div class="flex min-w-0 items-start gap-3">
            <div
              class="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-[var(--lp-accent)]/10 text-[var(--lp-accent)]"
              aria-hidden="true"
            >
              <span class="material-symbols-outlined text-2xl">deployed_code</span>
            </div>
            <div class="min-w-0 space-y-1.5">
              <div class="flex flex-wrap items-center gap-2">
                <h1 class="truncate text-2xl font-semibold tracking-tight md:text-3xl">
                  {{ workspace.name || workspace.workspace_id }}
                </h1>
                <span class="rounded-full border border-[var(--lp-line)] bg-[var(--lp-panel)] px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
                  {{ artifactModeLabel(workspace.artifact_mode) }}
                </span>
              </div>
              <p
                v-if="stackParts"
                class="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-xs text-[var(--lp-muted)]"
              >
                <span>{{ stackParts.stack }}</span>
                <span aria-hidden="true">·</span>
                <span>{{ stackParts.provider }}</span>
                <template v-if="stackParts.status">
                  <span aria-hidden="true">·</span>
                  <span class="inline-flex items-center gap-1.5 text-[var(--lp-ok)]">
                    <span class="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
                    {{ stackParts.status }}
                  </span>
                </template>
              </p>
            </div>
          </div>

          <div class="flex shrink-0 flex-wrap items-center gap-2 lg:justify-end">
            <NuxtLink
              :to="`/launch?workspace=${workspace.workspace_id}`"
              class="lp-btn-ghost whitespace-nowrap text-xs uppercase tracking-wide"
            >
              <span class="material-symbols-outlined text-base">rocket_launch</span>
              Launch preview
            </NuxtLink>
            <button
              type="button"
              class="lp-btn-ghost whitespace-nowrap text-xs uppercase tracking-wide"
              @click="advancedMode = !advancedMode"
            >
              <span class="material-symbols-outlined text-base">{{ advancedMode ? 'visibility_off' : 'code' }}</span>
              {{ advancedMode ? 'Interface form' : 'Advanced IDE' }}
            </button>
            <button
              type="button"
              class="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-xs font-medium uppercase tracking-wide text-[var(--lp-danger)] transition hover:bg-[var(--lp-danger)]/10 disabled:opacity-60"
              :disabled="destroying"
              @click="requestDestroy"
            >
              {{ destroying ? 'Destroying…' : 'Destroy' }}
            </button>
            <button
              type="button"
              class="lp-btn-ghost whitespace-nowrap text-xs uppercase tracking-wide"
              :aria-expanded="detailsOpen"
              aria-controls="workspace-details"
              @click="detailsOpen = !detailsOpen"
            >
              Details
              <span class="material-symbols-outlined text-base">
                {{ detailsOpen ? 'expand_less' : 'expand_more' }}
              </span>
            </button>
          </div>
        </div>

        <div
          v-show="detailsOpen"
          id="workspace-details"
          class="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-3"
        >
          <div>
            <p class="lp-label">Workspace ID</p>
            <p class="mt-1 break-all font-mono text-xs">{{ workspace.workspace_id }}</p>
          </div>
          <div>
            <p class="lp-label">Generated files</p>
            <p class="mt-1 text-sm">{{ workspace.files.length }}</p>
          </div>
          <div>
            <p class="lp-label">Artifacts mode</p>
            <p class="mt-1 text-sm">{{ artifactModeLabel(workspace.artifact_mode) }}</p>
          </div>
          <div>
            <p class="lp-label">Created</p>
            <p class="mt-1 text-sm text-[var(--lp-muted)]">
              {{
                workspace.created_at
                  ? new Date(workspace.created_at).toLocaleString()
                  : '—'
              }}
            </p>
          </div>
        </div>
      </section>

      <section
        v-if="!advancedMode"
        class="lp-glass overflow-hidden rounded-xl"
      >
        <div class="grid min-h-[78vh] gap-0 lg:grid-cols-[280px_minmax(0,1fr)]">
          <InfraFileSelector
            v-model="selectedInfraFile"
            :workspace-id="workspace.workspace_id"
          />
          <ManifestConfigurator
            :workspace-id="workspace.workspace_id"
            :selected-path="selectedInfraFile"
            @saved="onConfiguratorSaved"
            @error="onSetupError"
          />
        </div>
      </section>

      <ClientOnly v-if="advancedMode">
        <WorkspaceIde
          :workspace-id="workspace.workspace_id"
          :engine="workspace.engine"
          @run-command="onRunCommand"
        />
        <template #fallback>
          <section class="lp-glass flex min-h-[480px] items-center justify-center rounded-xl">
            <p class="text-sm text-[var(--lp-muted)]">Loading workspace IDE…</p>
          </section>
        </template>
      </ClientOnly>

      <section v-if="advancedMode" class="space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h2 class="text-lg font-semibold">Sandbox terminal</h2>
          <div class="flex flex-wrap items-center gap-3">
            <label class="flex items-center gap-2 text-sm text-[var(--lp-muted)]">
              <input v-model="runInit" type="checkbox" class="accent-[var(--lp-accent)]">
              Run IaC init on open
            </label>
            <button
              type="button"
              class="lp-btn-primary text-xs uppercase tracking-wide"
              :disabled="openingTerminal"
              @click="onOpenTerminal"
            >
              <span class="material-symbols-outlined text-base">terminal</span>
              {{ openingTerminal ? 'Opening…' : wsPath ? 'Reconnect' : 'Open terminal' }}
            </button>
          </div>
        </div>
        <p
          v-if="!wsPath"
          class="rounded-lg border border-dashed border-[var(--lp-line)] bg-[var(--lp-panel)]/40 px-4 py-2 text-xs text-[var(--lp-muted)]"
        >
          Open terminal manually to avoid blocking the workspace page on startup.
        </p>

        <ClientOnly>
          <TerminalPanel v-if="wsPath" :ws-path="wsPath" />
          <div
            v-else
            class="rounded-xl border border-dashed border-[var(--lp-line)] px-6 py-10 text-center text-sm text-[var(--lp-muted)]"
          >
            Terminal will attach here once the sandbox session starts.
          </div>
        </ClientOnly>
      </section>

      <AuditTimeline
        :entries="audits"
        :loading="auditsLoading"
        empty-label="No control-plane audit events for this workspace yet."
      />

      <ConfirmDialog
        v-model:open="confirmDestroyOpen"
        title="Destroy workspace?"
        :message="`Destroy IaC workspace “${workspace.name || workspace.workspace_id}”? Generated files and the sandbox will be removed. This cannot be undone.`"
        confirm-label="Yes, destroy"
        cancel-label="No"
        :busy="destroying"
        @confirm="onDestroy"
      />
    </template>
  </div>
</template>

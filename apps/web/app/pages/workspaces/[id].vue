<script setup lang="ts">
import type { AuditLogEntry } from '~/types/environment'
import type { IaCBundleSummary, ProvisionEngine } from '~/types/provisioning'
import { artifactModeLabel, workspaceStackParts } from '~/utils/workspaceDisplay'
import {
  detectWorkspaceInfraFromPaths,
} from '~/utils/workspaceInfraScaffold'

const WorkspaceIde = defineAsyncComponent(() => import('~/components/WorkspaceIde.vue'))
const WorkspaceServiceSetupForm = defineAsyncComponent(
  () => import('~/components/WorkspaceServiceSetupForm.vue'),
)
const KubernetesSuite = defineAsyncComponent(
  () => import('~/components/KubernetesSuite.vue'),
)

const route = useRoute()
const { t } = useI18n()
const workspaceId = computed(() => String(route.params.id))
const { getWorkspace, openTerminal, destroyWorkspace, listAudits, setWorkspaceStarred } = useProvisioning()
const activeTerminalWsPath = useState<string | null>('lp-terminal-ws-path', () => null)

const workspace = ref<IaCBundleSummary | null>(null)
const loadError = ref<string | null>(null)
const loading = ref(true)
const openingTerminal = ref(false)
const destroying = ref(false)
const starring = ref(false)
const confirmDestroyOpen = ref(false)
const wsPath = ref<string | null>(null)
const runInit = ref(false)
const advancedMode = useState('lp-workspace-advanced', () => false)
const activeTabMode = useState<'iac' | 'k8s' | 'ide'>('lp-workspace-view-tab', () => 'iac')
const detailsOpen = ref(false)
const setupOpen = ref(false)
const selectedInfraFile = ref<string | null>(null)
const infraFilesKey = ref(0)
const audits = ref<AuditLogEntry[]>([])
const auditsLoading = ref(false)
const showPush = ref(false)
const actionsMenuOpen = ref(false)
const interactiveTerminalOpen = ref(false)
const iacInitModalOpen = ref(false)
const iacModalMode = ref<'provision' | 'destroy'>('provision')
const sandboxWarm = ref(false)
const sandboxWarming = ref(false)
const formStatusMessage = ref<string | null>(null)
const formErrorMessage = ref<string | null>(null)

const showsKubernetesSuite = computed(() => {
  return (workspace.value?.runtime_mode ?? 'kubernetes') === 'kubernetes'
})

const primarySuiteLabel = computed(() => {
  const mode = workspace.value?.runtime_mode
  if (mode === 'docker_compose') return t('workspaces.detail.composeSuite')
  if (mode === 'running_instance') return t('workspaces.detail.instanceSuite')
  return t('workspaces.detail.kubernetesSuite')
})

const stackParts = computed(() =>
  workspace.value ? workspaceStackParts(workspace.value) : null,
)

const iacEngine = computed<ProvisionEngine>(() => {
  const files = workspace.value?.files ?? []
  const detected = detectWorkspaceInfraFromPaths(files)
  if (detected.provision.enabled) return detected.provision.engine
  const engine = workspace.value?.engine
  if (engine === 'opentofu' || engine === 'pulumi' || engine === 'terraform') {
    return engine
  }
  return 'terraform'
})

const showIacShortcuts = computed(() => {
  if (!workspace.value) return false
  const files = workspace.value.files ?? []
  const detected = detectWorkspaceInfraFromPaths(files)
  if (detected.provision.enabled) return true
  return workspace.value.artifact_mode !== 'manifest_only'
})

function closeActionsMenu() {
  actionsMenuOpen.value = false
}

function toggleActionsMenu() {
  actionsMenuOpen.value = !actionsMenuOpen.value
}

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
    if (workspace.value?.runtime_mode && workspace.value.runtime_mode !== 'kubernetes') {
      if (activeTabMode.value === 'k8s') activeTabMode.value = 'iac'
    } else if (showsKubernetesSuite.value && activeTabMode.value === 'iac') {
      // Keep user preference; only auto-switch away from k8s for non-k8s runtimes.
    }
    auditsLoading.value = true
    try {
      audits.value = await listAudits(workspaceId.value)
    } catch {
      audits.value = []
    } finally {
      auditsLoading.value = false
    }
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : t('workspaces.errors.load')
    workspace.value = null
  } finally {
    loading.value = false
  }
}

async function toggleStar() {
  if (!workspace.value || starring.value) return
  starring.value = true
  loadError.value = null
  try {
    const updated = await setWorkspaceStarred(
      workspaceId.value,
      !(workspace.value.starred ?? false),
    )
    workspace.value = {
      ...workspace.value,
      starred: updated.starred,
    }
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : t('workspaces.errors.star')
  } finally {
    starring.value = false
  }
}

async function onOpenTerminal(opts: { runInitOnOpen?: boolean } = {}) {
  if (openingTerminal.value) return
  openingTerminal.value = true
  loadError.value = null
  try {
    const session = await openTerminal(workspaceId.value, {
      run_init: opts.runInitOnOpen ?? runInit.value,
    })
    wsPath.value = session.ws_path
    activeTerminalWsPath.value = session.ws_path
    sandboxWarm.value = true
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : t('workspaces.errors.terminal')
    formErrorMessage.value = loadError.value
  } finally {
    openingTerminal.value = false
  }
}

/** Start sandbox session without forcing the terminal panel open. */
async function ensureSandboxBackground() {
  sandboxWarm.value = true
  if (wsPath.value || openingTerminal.value) return
  sandboxWarming.value = true
  try {
    // Skip auto IaC bootstrap - the guided wizard runs init/validate/plan/apply itself.
    await onOpenTerminal({ runInitOnOpen: false })
  } finally {
    sandboxWarming.value = false
  }
}

/** Recreate sandbox so newly saved cloud keys are injected into the session env. */
async function restartSandboxBackground() {
  wsPath.value = null
  activeTerminalWsPath.value = null
  sandboxWarm.value = false
  await ensureSandboxBackground()
}

function openIacProvisionModal() {
  iacModalMode.value = 'provision'
  iacInitModalOpen.value = true
  void ensureSandboxBackground()
}

function openIacDestroyModal() {
  iacModalMode.value = 'destroy'
  iacInitModalOpen.value = true
  void ensureSandboxBackground()
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
    loadError.value = err instanceof Error ? err.message : t('workspaces.errors.destroy')
  } finally {
    destroying.value = false
  }
}

function onRunCommand(command: string) {
  void ensureTerminalAndRun(command, { reveal: true })
}

async function ensureTerminalAndRun(
  command: string,
  opts: { reveal?: boolean } = {},
) {
  formErrorMessage.value = null
  await ensureSandboxBackground()
  if (!wsPath.value) {
    formErrorMessage.value = 'Could not start sandbox terminal'
    loadError.value = formErrorMessage.value
    return
  }
  const queue = useState<string[]>('lp-terminal-cmd-queue', () => [])
  queue.value = [...queue.value, command]
  formStatusMessage.value = `Sent to terminal: ${command}`
  if (opts.reveal !== false) {
    interactiveTerminalOpen.value = true
  }
}

function onSetupError(message: string) {
  loadError.value = message
  formErrorMessage.value = message
}

function onConfiguratorSaved() {
  formStatusMessage.value = 'Form changes saved'
  infraFilesKey.value += 1
}

function onConfiguratorDeleted(path: string) {
  if (selectedInfraFile.value === path) {
    selectedInfraFile.value = null
  }
  formStatusMessage.value = `Deleted ${path}`
  infraFilesKey.value += 1
}

async function onWorkspaceSetupSaved() {
  formStatusMessage.value = 'Workspace configuration updated'
  formErrorMessage.value = null
  setupOpen.value = false
  infraFilesKey.value += 1
  await load()
}

function onPushSuccess(fullName: string) {
  formStatusMessage.value = `Published to ${fullName}`
  formErrorMessage.value = null
  infraFilesKey.value += 1
}

function onPushConverted(message: string) {
  formStatusMessage.value = message
  formErrorMessage.value = null
  infraFilesKey.value += 1
}

function onPushError(message: string) {
  formErrorMessage.value = message
}

const publishButtonLabel = computed(() => t('workspaceIde.publish'))

onMounted(async () => {
  document.addEventListener('click', closeActionsMenu)
  await load()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', closeActionsMenu)
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
      {{ t('nav.workspaces') }}
    </NuxtLink>

    <AppSplash
      v-if="loading"
      compact
      :message="t('workspaces.detail.loading')"
    />
    <p v-else-if="loadError" class="text-sm text-[var(--lp-danger)]">{{ loadError }}</p>

    <template v-if="workspace">
      <section class="lp-glass relative z-20 overflow-visible rounded-xl">
        <div
          class="flex flex-col gap-4 overflow-visible bg-[var(--lp-panel-2)]/40 px-5 py-4"
          :class="detailsOpen ? 'border-b border-[var(--lp-line)]' : ''"
        >
          <div class="flex min-w-0 items-start justify-between gap-3">
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

            <div class="flex shrink-0 items-center gap-2">
              <button
                type="button"
                class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/25 transition hover:bg-[var(--lp-panel-2)]"
                :class="workspace.starred ? 'text-[var(--lp-accent)]' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
                :disabled="starring"
                :aria-pressed="Boolean(workspace.starred)"
                :aria-label="workspace.starred ? t('catalog.index.unstarAction') : t('catalog.index.yours')"
                :title="workspace.starred ? t('catalog.index.unstarAction') : t('catalog.index.yours')"
                @click="toggleStar"
              >
                <span
                  class="material-symbols-outlined text-xl"
                  :class="workspace.starred ? 'filled' : ''"
                >
                  star
                </span>
              </button>
              <NuxtLink
                :to="`/launch?workspace=${workspace.workspace_id || (workspace as any).id || workspaceId}`"
                class="lp-btn-ghost hidden whitespace-nowrap text-xs uppercase tracking-wide sm:inline-flex"
              >
                <span class="material-symbols-outlined text-base">rocket_launch</span>
                {{ t('environments.index.launchPreview') }}
              </NuxtLink>
              <button
                v-if="!advancedMode"
                type="button"
                class="lp-btn-primary whitespace-nowrap text-xs uppercase tracking-wide"
                @click="showPush = true"
              >
                <span class="material-symbols-outlined text-base">publish</span>
                {{ publishButtonLabel }}
              </button>
              <div class="relative" @click.stop>
                <button
                  type="button"
                  class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/25 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
                  :aria-expanded="actionsMenuOpen"
                  aria-haspopup="menu"
                  aria-label="More workspace actions"
                  @click="toggleActionsMenu"
                >
                  <span class="material-symbols-outlined text-xl">more_vert</span>
                </button>
                <div
                  v-if="actionsMenuOpen"
                  role="menu"
                  class="absolute right-0 top-full z-50 mt-1.5 min-w-[220px] overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] py-1 shadow-xl"
                >
                  <button
                    type="button"
                    role="menuitem"
                    class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm transition hover:bg-[var(--lp-panel-2)]"
                    @click="setupOpen = !setupOpen; closeActionsMenu()"
                  >
                    <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">tune</span>
                    {{ setupOpen ? 'Close setup' : 'Update workspace' }}
                  </button>
                  <NuxtLink
                    :to="`/launch?workspace=${workspace.workspace_id || (workspace as any).id || workspaceId}`"
                    role="menuitem"
                    class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm transition hover:bg-[var(--lp-panel-2)] sm:hidden"
                    @click="closeActionsMenu()"
                  >
                    <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">rocket_launch</span>
                    {{ t('environments.index.launchPreview') }}
                  </NuxtLink>
                  <button
                    type="button"
                    role="menuitem"
                    class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm transition hover:bg-[var(--lp-panel-2)]"
                    @click="advancedMode = !advancedMode; closeActionsMenu()"
                  >
                    <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">
                      {{ advancedMode ? 'visibility_off' : 'code' }}
                    </span>
                    {{ advancedMode ? 'Interface form' : 'Advanced IDE' }}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm transition hover:bg-[var(--lp-panel-2)]"
                    @click="detailsOpen = !detailsOpen; closeActionsMenu()"
                  >
                    <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">info</span>
                    {{ detailsOpen ? 'Hide details' : 'Details' }}
                  </button>
                  <div class="my-1 border-t border-[var(--lp-line)]" role="separator" />
                  <button
                    type="button"
                    role="menuitem"
                    class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-danger)] transition hover:bg-[var(--lp-danger)]/10 disabled:opacity-60"
                    :disabled="destroying"
                    @click="requestDestroy(); closeActionsMenu()"
                  >
                    <span class="material-symbols-outlined text-base">delete</span>
                    {{ destroying ? t('common.deleting') : t('common.destroy') }}
                  </button>
                </div>
              </div>
            </div>
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
                  : '-'
              }}
            </p>
          </div>
        </div>
      </section>

      <p v-if="formStatusMessage && !advancedMode" class="text-sm text-[var(--lp-ok)]">
        {{ formStatusMessage }}
      </p>
      <p v-if="formErrorMessage && !advancedMode" class="text-sm text-[var(--lp-danger)]">
        {{ formErrorMessage }}
      </p>

      <section
        v-if="setupOpen"
        class="lp-glass overflow-hidden rounded-xl p-5"
      >
        <h2 class="mb-4 text-lg font-semibold">Update workspace configuration</h2>
        <ClientOnly>
          <WorkspaceServiceSetupForm
            :workspace-id="workspace.workspace_id"
            @saved="onWorkspaceSetupSaved"
            @error="onSetupError"
            @cancel="setupOpen = false"
          />
          <template #fallback>
            <p class="text-sm text-[var(--lp-muted)]">{{ t('workspaces.detail.loadingSetup') }}</p>
          </template>
        </ClientOnly>
      </section>

      <!-- Workspace Suite View Tabs -->
      <div class="flex items-center gap-2 border-b border-[var(--lp-line)] pb-3 font-mono text-xs">
        <button
          v-if="showsKubernetesSuite"
          type="button"
          class="flex items-center gap-2 rounded-xl px-4 py-2 font-semibold transition-all"
          :class="activeTabMode === 'k8s' ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)] shadow-md' : 'bg-[var(--lp-panel)] text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          @click="activeTabMode = 'k8s'; advancedMode = false"
        >
          <span class="material-symbols-outlined text-lg">deployed_code</span>
          {{ primarySuiteLabel }}
        </button>
        <button
          type="button"
          class="flex items-center gap-2 rounded-xl px-4 py-2 font-semibold transition-all"
          :class="activeTabMode === 'iac' && !advancedMode ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)] shadow-md' : 'bg-[var(--lp-panel)] text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          @click="activeTabMode = 'iac'; advancedMode = false"
        >
          <span class="material-symbols-outlined text-lg">tune</span>
          {{ showsKubernetesSuite ? t('workspaces.detail.manifestConfigurator') : primarySuiteLabel }}
        </button>
        <button
          type="button"
          class="flex items-center gap-2 rounded-xl px-4 py-2 font-semibold transition-all"
          :class="advancedMode ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)] shadow-md' : 'bg-[var(--lp-panel)] text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          @click="advancedMode = true; activeTabMode = 'ide'"
        >
          <span class="material-symbols-outlined text-lg">code</span>
          Advanced IDE
        </button>
      </div>

      <!-- Kubernetes Management & Visual Execution Suite -->
      <ClientOnly v-if="showsKubernetesSuite && activeTabMode === 'k8s' && !advancedMode">
        <KubernetesSuite :workspace-id="workspace.workspace_id" />
        <template #fallback>
          <div class="lp-glass flex min-h-[300px] items-center justify-center rounded-xl p-8 text-sm text-[var(--lp-muted)]">
            <span class="material-symbols-outlined animate-spin text-2xl mr-2">sync</span>
            {{ t('workspaces.detail.loadingK8s') }}
          </div>
        </template>
      </ClientOnly>

      <section
        v-if="!advancedMode && activeTabMode === 'iac'"
        class="lp-glass overflow-hidden rounded-xl"
      >
        <WorkspaceIacRunToolbar
          v-if="showIacShortcuts"
          :engine="iacEngine"
          :provider="workspace?.provider"
          :status="workspace?.status"
          :busy="openingTerminal"
          :terminal-ready="Boolean(wsPath)"
          @open-provision="openIacProvisionModal"
          @open-destroy="openIacDestroyModal"
          @open-terminal="void ensureSandboxBackground()"
        />
        <div class="grid min-h-[78vh] grid-cols-1 gap-0 lg:grid-cols-[280px_minmax(0,1fr)] items-start">
          <InfraFileSelector
            :key="infraFilesKey"
            v-model="selectedInfraFile"
            :workspace-id="workspace.workspace_id"
          />
          <ManifestConfigurator
            :workspace-id="workspace.workspace_id"
            :selected-path="selectedInfraFile"
            @saved="onConfiguratorSaved"
            @deleted="onConfiguratorDeleted"
            @error="onSetupError"
          />
        </div>
      </section>

      <section
        v-if="!advancedMode && showIacShortcuts"
        class="space-y-3"
      >
        <div class="flex flex-wrap items-center justify-between gap-3">
          <button
            type="button"
            class="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-wide text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
            @click="interactiveTerminalOpen = !interactiveTerminalOpen"
          >
            <span class="material-symbols-outlined text-base">terminal</span>
            Sandbox terminal
            <span class="material-symbols-outlined text-sm">
              {{ interactiveTerminalOpen ? 'expand_less' : 'expand_more' }}
            </span>
          </button>
          <button
            type="button"
            class="lp-btn-ghost text-xs uppercase tracking-wide"
            :disabled="openingTerminal"
            @click="onOpenTerminal(); interactiveTerminalOpen = true"
          >
            <span class="material-symbols-outlined text-base">terminal</span>
            {{ openingTerminal ? 'Opening…' : wsPath ? 'Reconnect' : 'Open terminal' }}
          </button>
        </div>
        <div
          v-if="wsPath && (interactiveTerminalOpen || sandboxWarm || iacInitModalOpen)"
          :class="
            interactiveTerminalOpen
              ? ''
              : 'pointer-events-none fixed left-[-10000px] top-0 h-[420px] w-[800px] opacity-0'
          "
        >
          <ClientOnly>
            <TerminalPanel :ws-path="wsPath" />
          </ClientOnly>
        </div>
        <div
          v-else-if="interactiveTerminalOpen"
          class="rounded-xl border border-dashed border-[var(--lp-line)] px-6 py-8 text-center text-sm text-[var(--lp-muted)]"
        >
          Open the terminal to run Terraform / Pulumi shortcuts from the toolbar above.
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
            <p class="text-sm text-[var(--lp-muted)]">{{ t('workspaces.detail.loadingIde') }}</p>
          </section>
        </template>
      </ClientOnly>

      <section v-if="advancedMode" class="space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h2 class="text-lg font-semibold">{{ t('terminal.title') }}</h2>
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
        title="Execution pipeline"
        :entries="audits"
        :loading="auditsLoading"
        empty-label="No control-plane audit events for this workspace yet."
      />

      <ConfirmDialog
        v-model:open="confirmDestroyOpen"
        :title="t('workspaces.destroy.title')"
        :message="`Destroy IaC workspace “${workspace.name || workspace.workspace_id}”? Generated files and the sandbox will be removed. This cannot be undone.`"
        :confirm-label="t('workspaces.destroy.confirm')"
        :cancel-label="t('workspaces.destroy.cancel')"
        :busy="destroying"
        @confirm="onDestroy"
      />

      <WorkspaceIacInitModal
        :open="iacInitModalOpen"
        :workspace-id="workspace.workspace_id"
        :engine="iacEngine"
        :mode="iacModalMode"
        :terminal-ready="Boolean(wsPath)"
        :sandbox-warming="sandboxWarming || openingTerminal"
        @update:open="(value) => { iacInitModalOpen = value }"
        @run="(cmd) => ensureTerminalAndRun(cmd, { reveal: false })"
        @ensure-sandbox="void ensureSandboxBackground()"
        @restart-sandbox="void restartSandboxBackground()"
        @open-terminal="void ensureSandboxBackground()"
        @saved="formStatusMessage = 'Cloud key saved'"
        @error="onPushError"
      />

      <WorkspaceGithubPushModal
        :open="showPush"
        :workspace-id="workspace.workspace_id"
        :workspace-name="workspace.name || workspace.workspace_id"
        @update:open="(value) => { showPush = value }"
        @pushed="onPushSuccess"
        @converted="onPushConverted"
        @error="onPushError"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import type { AuditLogEntry, Environment, PreviewLaunchPayload } from '~/types/environment'

type CloudProvider = Exclude<PreviewLaunchPayload['provider'], 'local'>

const route = useRoute()
const id = computed(() => String(route.params.id))
const environmentId = computed(() => id.value || null)
const { getById, destroy, extendTtl, promoteToCloud, listAudits, scanDrift, retryProvision, pauseEnvironment, resumeEnvironment } = useEnvironments()
const {
  open: analyzerOpen,
  loading: analyzing,
  analyzeEnvironment,
} = usePreviewAnalyzer()

const environment = ref<Environment | null>(null)
const loadError = ref<string | null>(null)
const destroying = ref(false)
const confirmDestroyOpen = ref(false)
const extending = ref(false)
const promoting = ref(false)
const scanningDrift = ref(false)
const retrying = ref(false)
const pausing = ref(false)
const resuming = ref(false)

async function onPause() {
  if (!environment.value || pausing.value) return
  pausing.value = true
  loadError.value = null
  try {
    environment.value = await pauseEnvironment(environment.value.id)
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Pause failed'
  } finally {
    pausing.value = false
  }
}

async function onResume() {
  if (!environment.value || resuming.value) return
  resuming.value = true
  loadError.value = null
  try {
    environment.value = await resumeEnvironment(environment.value.id)
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Resume failed'
  } finally {
    resuming.value = false
  }
}
const showPromote = ref(false)
const tick = ref(0)
const copied = ref(false)
const audits = ref<AuditLogEntry[]>([])
const auditsLoading = ref(false)
const actionsMenuOpen = ref(false)

const promoteForm = reactive({
  provider: 'gcp' as CloudProvider,
  gcp_sa_key_json: '',
  aws_access_key_id: '',
  aws_secret_access_key: '',
  azure_client_id: '',
  azure_client_secret: '',
  azure_tenant_id: '',
  azure_subscription_id: '',
  cloudflare_api_token: '',
})

const { lines, connected, done, connect } = useEnvironmentLogStream(environmentId)

const remainingLabel = computed(() => {
  tick.value
  if (!environment.value) return '—'
  const left = environment.value.time_remaining_seconds
    ?? Math.max(
      Math.floor((new Date(environment.value.ttl_expires_at).getTime() - Date.now()) / 1000),
      0,
    )
  if (left <= 0) return 'Expired'
  const hours = Math.floor(left / 3600)
  const minutes = Math.floor((left % 3600) / 60)
  return `${hours}h ${minutes}m`
})

const appHref = computed(() => environment.value?.preview_url || null)

const portalHref = computed(() => {
  if (!environment.value) return '#'
  return environment.value.portal_url || `/p/${environment.value.id}`
})

const canOpenApp = computed(() => {
  if (!environment.value) return false
  return Boolean(environment.value.app_ready && appHref.value)
})

const openAppTitle = computed(() => {
  const env = environment.value
  if (!env) return 'Open preview'
  const image = env.workload_image || 'workload'
  const port = env.node_port != null ? `NodePort ${env.node_port}` : env.preview_url
  return `Open ${image} (${port})`
})

const isProvisioning = computed(() => environment.value?.status === 'PROVISIONING')
const isLocal = computed(() => Boolean(environment.value?.is_local))
const canExtend = computed(() => {
  const s = environment.value?.status
  return s === 'RUNNING' || s === 'FAILED'
})
const canPromote = computed(() => {
  if (!environment.value) return false
  return environment.value.status === 'RUNNING' && isLocal.value
})
const canScanDrift = computed(() => environment.value?.status === 'RUNNING')
const canRetry = computed(() => {
  const s = environment.value?.status
  return s === 'FAILED' || s === 'RUNNING'
})
const canAnalyze = computed(() => {
  if (!environment.value) return false
  return (
    environment.value.status === 'FAILED'
    || Boolean(environment.value.error_message)
    || lines.value.some((line) => /error|fail|CrashLoop|OOMKilled|CVE-|trivy|codeql/i.test(line))
  )
})

async function onAnalyze() {
  if (!environment.value || analyzing.value) return
  try {
    await analyzeEnvironment(environment.value.id, {
      cicdLogs: lines.value.join('\n') || null,
      includeEnvironmentLogs: true,
    })
  } catch {
    // error surfaced via usePreviewAnalyzer().error in drawer
  }
}

async function onRetry() {
  if (!environment.value || retrying.value) return
  retrying.value = true
  loadError.value = null
  try {
    environment.value = await retryProvision(environment.value.id)
    connect(environment.value.id)
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Retry failed'
  } finally {
    retrying.value = false
  }
}

async function load(opts: { softAudits?: boolean } = {}) {
  loadError.value = null
  try {
    environment.value = await getById(id.value)
    const soft = opts.softAudits && audits.value.length > 0
    if (!soft) auditsLoading.value = true
    try {
      audits.value = await listAudits(id.value)
    } catch {
      if (!soft) audits.value = []
    } finally {
      auditsLoading.value = false
    }
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Failed to load environment'
  }
}

function closeActionsMenu() {
  actionsMenuOpen.value = false
}

function toggleActionsMenu() {
  actionsMenuOpen.value = !actionsMenuOpen.value
}

function requestDestroy() {
  if (!environment.value || destroying.value) return
  confirmDestroyOpen.value = true
}

async function onDestroy() {
  if (!environment.value || destroying.value) return
  confirmDestroyOpen.value = false
  destroying.value = true
  try {
    environment.value = await destroy(environment.value.id)
    connect(environment.value.id)
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Destroy failed'
  } finally {
    destroying.value = false
  }
}

async function onExtend() {
  if (!environment.value || extending.value) return
  extending.value = true
  try {
    environment.value = await extendTtl(environment.value.id, {})
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Extend failed'
  } finally {
    extending.value = false
  }
}

async function onScanDrift() {
  if (!environment.value || scanningDrift.value) return
  scanningDrift.value = true
  loadError.value = null
  try {
    environment.value = await scanDrift(environment.value.id)
    const soft = audits.value.length > 0
    if (!soft) auditsLoading.value = true
    try {
      audits.value = await listAudits(environment.value.id)
    } catch {
      if (!soft) audits.value = []
    } finally {
      auditsLoading.value = false
    }
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Drift scan failed'
  } finally {
    scanningDrift.value = false
  }
}

async function onPromote() {
  if (!environment.value || promoting.value) return
  promoting.value = true
  loadError.value = null
  try {
    const credentials: Record<string, string> = {}
    if (promoteForm.provider === 'gcp') {
      credentials.gcp_sa_key_json = promoteForm.gcp_sa_key_json
    } else if (promoteForm.provider === 'aws') {
      credentials.aws_access_key_id = promoteForm.aws_access_key_id
      credentials.aws_secret_access_key = promoteForm.aws_secret_access_key
    } else if (promoteForm.provider === 'azure') {
      credentials.azure_client_id = promoteForm.azure_client_id
      credentials.azure_client_secret = promoteForm.azure_client_secret
      credentials.azure_tenant_id = promoteForm.azure_tenant_id
      credentials.azure_subscription_id = promoteForm.azure_subscription_id
    } else {
      credentials.cloudflare_api_token = promoteForm.cloudflare_api_token
    }
    const created = await promoteToCloud(environment.value.id, {
      provider: promoteForm.provider,
      credentials,
    })
    showPromote.value = false
    await navigateTo(`/environments/${created.id}`)
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Promote failed'
  } finally {
    promoting.value = false
  }
}

async function copyAppUrl() {
  if (!appHref.value) return
  try {
    await navigator.clipboard.writeText(appHref.value)
    copied.value = true
    closeActionsMenu()
    setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch {
    loadError.value = 'Could not copy URL'
  }
}

async function onOpenAppClick(e: MouseEvent) {
  if (!appHref.value) return
  // Prevent navigation to a potentially-stale URL; refresh latest preview_url first.
  e.preventDefault()
  const fallbackHref = appHref.value
  try {
    environment.value = await getById(id.value)
  } catch {
    // Fall back to the current href if refresh fails.
  }
  const href = environment.value?.preview_url || fallbackHref
  if (!href) return
  const w = window.open(href, '_blank')
  // Ensure the opened window can't reach back into our page.
  if (w) w.opener = null
}

watch(done, async (isDone) => {
  if (isDone) {
    await load({ softAudits: true })
  }
})

watch(
  () => environment.value?.status,
  async (status, prev) => {
    if (prev === 'PROVISIONING' && status && status !== 'PROVISIONING') {
      await load({ softAudits: true })
    }
  },
)

onMounted(() => {
  void load()
  const onDocClick = () => {
    if (actionsMenuOpen.value) closeActionsMenu()
  }
  document.addEventListener('click', onDocClick)
  const timer = setInterval(() => {
    tick.value += 1
    if (environment.value?.status === 'PROVISIONING') {
      void load({ softAudits: true })
    }
  }, 4_000)
  onUnmounted(() => {
    clearInterval(timer)
    document.removeEventListener('click', onDocClick)
  })
})
</script>

<template>
  <div class="space-y-8 animate-fade-up">
    <NuxtLink
      to="/"
      class="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-wide text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
    >
      <span class="material-symbols-outlined text-sm">arrow_back</span>
      Environments
    </NuxtLink>

    <p v-if="loadError" class="text-sm text-[var(--lp-danger)]">{{ loadError }}</p>

    <template v-if="environment">
      <p
        v-if="environment.ttl_warning"
        class="rounded-lg border border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 px-4 py-3 text-sm text-[var(--lp-warn)]"
      >
        TTL under 2 hours — extend now or this preview will be reaped.
      </p>
      <p
        v-if="environment.soft_cost_cap_exceeded"
        class="rounded-lg border border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/10 px-4 py-3 text-sm text-[var(--lp-danger)]"
      >
        Soft cost cap reached for this environment. Destroy it or raise
        <code class="font-mono text-xs">PREVIEW_SOFT_COST_CAP</code>.
      </p>
      <p
        v-if="environment.drift_detected"
        class="rounded-lg border border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 px-4 py-3 text-sm text-[var(--lp-warn)]"
      >
        Configuration drift detected — live cluster state differs from Launchpad.
        <span v-if="environment.drift_summary" class="mt-1 block font-mono text-xs">
          {{ environment.drift_summary }}
        </span>
      </p>

      <section class="lp-glass overflow-hidden rounded-xl">
        <div class="flex flex-col gap-5 border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 px-5 py-5 lg:flex-row lg:items-start lg:justify-between">
          <div class="min-w-0 space-y-3">
            <h1 class="text-3xl font-semibold tracking-tight">{{ environment.name }}</h1>
            <div class="flex flex-wrap items-center gap-3">
              <StatusBadge :status="environment.status" />
              <span class="break-all font-mono text-xs text-[var(--lp-muted)]">{{ environment.namespace_name }}</span>
            </div>
            <p v-if="environment.runtime_summary" class="font-mono text-xs text-[var(--lp-muted)]">
              {{ environment.runtime_summary }}
            </p>
          </div>

          <div class="flex w-full justify-end gap-2.5 lg:w-6xl lg:max-w-3xl lg:items-end">
            <!-- Primary actions -->
            <div class="flex flex-wrap items-center gap-2 lg:justify-end">
              <a
                v-if="canOpenApp"
                :href="appHref!"
                target="_blank"
                rel="noopener noreferrer"
                class="lp-btn-primary whitespace-nowrap"
                :title="openAppTitle"
                @click="onOpenAppClick"
              >
                <span class="material-symbols-outlined text-base">open_in_new</span>
                Open app
                <span v-if="environment.node_port" class="font-mono text-xs opacity-80">
                  :{{ environment.node_port }}
                </span>
              </a>
              <button
                v-else-if="isProvisioning"
                type="button"
                class="lp-btn-primary whitespace-nowrap opacity-60"
                disabled
                title="App URL is available when status is Running"
              >
                <span class="material-symbols-outlined text-base">hourglass_top</span>
                App provisioning…
              </button>
              <button
                v-if="environment.status === 'RUNNING'"
                type="button"
                class="lp-btn-ghost whitespace-nowrap text-amber-400 border-amber-500/30 hover:bg-amber-500/10"
                :disabled="pausing"
                @click="onPause"
              >
                <span class="material-symbols-outlined text-base">pause</span>
                {{ pausing ? 'Pausing…' : 'Pause' }}
              </button>
              <button
                v-if="environment.status === 'PAUSED'"
                type="button"
                class="lp-btn-primary whitespace-nowrap bg-emerald-600 hover:bg-emerald-500 text-white"
                :disabled="resuming"
                @click="onResume"
              >
                <span class="material-symbols-outlined text-base">play_arrow</span>
                {{ resuming ? 'Resuming…' : 'Resume' }}
              </button>
              <button
                v-if="canRetry"
                type="button"
                class="lp-btn-primary whitespace-nowrap"
                :disabled="retrying"
                @click="onRetry"
              >
                <span class="material-symbols-outlined text-base">replay</span>
                {{ retrying ? 'Retrying…' : 'Retry provision' }}
              </button>
              <button
                v-if="environment.status !== 'DESTROYED' && environment.status !== 'TEARDOWN_PENDING' && environment.status !== 'PROVISIONING'"
                type="button"
                class="lp-btn-danger whitespace-nowrap"
                :disabled="destroying"
                @click="requestDestroy"
              >
                <span class="material-symbols-outlined text-base">delete</span>
                {{ destroying ? 'Queuing teardown…' : 'Destroy' }}
              </button>
            </div>

            <!-- Secondary tools: overflow menu -->
            <div class="relative" @click.stop>
              <button
                type="button"
                class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/25 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
                :aria-expanded="actionsMenuOpen"
                aria-haspopup="menu"
                aria-label="More actions"
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
                  v-if="canOpenApp"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)]"
                  @click="copyAppUrl"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">content_copy</span>
                  {{ copied ? 'Copied' : 'Copy URL' }}
                </button>
                <button
                  v-if="canExtend"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="extending"
                  @click="onExtend(); closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">more_time</span>
                  {{ extending ? 'Extending…' : 'Extend TTL' }}
                </button>
                <button
                  v-if="canScanDrift"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="scanningDrift"
                  @click="onScanDrift(); closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">difference</span>
                  {{ scanningDrift ? 'Scanning…' : 'Scan drift' }}
                </button>
                <button
                  v-if="canAnalyze"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="analyzing"
                  @click="onAnalyze(); closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">psychology</span>
                  {{ analyzing ? 'Analyzing…' : 'Analyze' }}
                </button>
                <button
                  v-if="canPromote"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)]"
                  @click="showPromote = !showPromote; closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">cloud_upload</span>
                  Deploy to cloud
                </button>
                <a
                  :href="portalHref"
                  target="_blank"
                  rel="noopener noreferrer"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)]"
                  @click="closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">monitoring</span>
                  Status page
                </a>
              </div>
            </div>
          </div>
        </div>

        <div
          v-if="showPromote"
          class="space-y-4 border-b border-[var(--lp-line)] bg-[var(--lp-ink)]/30 px-5 py-4"
        >
          <p class="text-sm text-[var(--lp-muted)]">
            Creates a <strong class="text-[var(--lp-text)]">new</strong> cloud preview from the same
            template/repo. Your local environment keeps running until you destroy it.
          </p>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="p in (['gcp', 'aws', 'azure', 'cloudflare'] as CloudProvider[])"
              :key="p"
              type="button"
              class="rounded-lg border px-3 py-1.5 text-sm uppercase"
              :class="
                promoteForm.provider === p
                  ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
                  : 'border-[var(--lp-line)]'
              "
              @click="promoteForm.provider = p"
            >
              {{ p }}
            </button>
          </div>
          <label v-if="promoteForm.provider === 'gcp'" class="block space-y-2">
            <span class="lp-label">Service account JSON</span>
            <textarea v-model="promoteForm.gcp_sa_key_json" rows="4" class="lp-input font-mono text-xs" />
          </label>
          <template v-else-if="promoteForm.provider === 'aws'">
            <label class="block space-y-2">
              <span class="lp-label">Access key ID</span>
              <input v-model="promoteForm.aws_access_key_id" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">Secret access key</span>
              <input v-model="promoteForm.aws_secret_access_key" type="password" class="lp-input">
            </label>
          </template>
          <template v-else-if="promoteForm.provider === 'azure'">
            <div class="grid gap-3 sm:grid-cols-2">
              <label class="block space-y-2">
                <span class="lp-label">Client ID</span>
                <input v-model="promoteForm.azure_client_id" class="lp-input">
              </label>
              <label class="block space-y-2">
                <span class="lp-label">Client secret</span>
                <input v-model="promoteForm.azure_client_secret" type="password" class="lp-input">
              </label>
              <label class="block space-y-2">
                <span class="lp-label">Tenant ID</span>
                <input v-model="promoteForm.azure_tenant_id" class="lp-input">
              </label>
              <label class="block space-y-2">
                <span class="lp-label">Subscription ID</span>
                <input v-model="promoteForm.azure_subscription_id" class="lp-input">
              </label>
            </div>
          </template>
          <label v-else class="block space-y-2">
            <span class="lp-label">API token</span>
            <input v-model="promoteForm.cloudflare_api_token" type="password" class="lp-input">
          </label>
          <button
            type="button"
            class="lp-btn-primary"
            :disabled="promoting"
            @click="onPromote"
          >
            {{ promoting ? 'Launching cloud preview…' : 'Launch cloud preview' }}
          </button>
        </div>

        <div class="grid gap-6 p-5 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <p class="lp-label">App URL</p>
            <a
              v-if="canOpenApp && appHref"
              :href="appHref"
              target="_blank"
              rel="noopener noreferrer"
              class="mt-1 block break-all font-mono text-xs text-[var(--lp-accent)] hover:underline"
            >
              {{ appHref }}
            </a>
            <p v-else-if="isProvisioning" class="mt-1 text-sm text-[var(--lp-muted)]">
              Available when Running — watch logs below
            </p>
            <p v-else class="mt-1 text-sm text-[var(--lp-muted)]">Not available</p>
          </div>
          <div>
            <p class="lp-label">TTL remaining</p>
            <p
              class="mt-1 font-mono text-sm"
              :class="environment.ttl_warning ? 'text-[var(--lp-warn)]' : ''"
            >
              {{ remainingLabel }}
            </p>
            <p class="mt-0.5 text-xs text-[var(--lp-muted)]">
              Expires {{ new Date(environment.ttl_expires_at).toLocaleString() }}
            </p>
          </div>
          <div v-if="!isLocal">
            <p class="lp-label">Cost to date</p>
            <p class="mt-1 text-lg font-semibold text-[var(--lp-accent)]">
              ${{ environment.cost_accrued ?? '0.00' }}
            </p>
            <p class="text-xs text-[var(--lp-muted)]">
              ${{ environment.cost_estimate_hourly }}/hr
            </p>
          </div>
          <div v-else>
            <p class="lp-label">Cost</p>
            <p class="mt-1 text-sm text-[var(--lp-muted)]">Local — $0</p>
          </div>
          <div>
            <p class="lp-label">Git repository</p>
            <p class="mt-1 break-all font-mono text-xs">{{ environment.git_repo_url }}</p>
          </div>
          <div>
            <p class="lp-label">Git branch</p>
            <p class="mt-1 font-mono text-sm">{{ environment.git_branch }}</p>
          </div>
          <div v-if="environment.github_pr_number">
            <p class="lp-label">Linked PR</p>
            <a
              v-if="environment.github_pr_url"
              :href="environment.github_pr_url"
              target="_blank"
              rel="noopener noreferrer"
              class="mt-1 inline-block font-mono text-sm text-[var(--lp-accent)] hover:underline"
            >
              #{{ environment.github_pr_number }}
            </a>
            <p v-else class="mt-1 font-mono text-sm">#{{ environment.github_pr_number }}</p>
          </div>
          <div v-if="environment.template_id">
            <p class="lp-label">Template</p>
            <p class="mt-1 font-mono text-sm">{{ environment.template_id }}</p>
          </div>
          <div v-if="environment.workload_image">
            <p class="lp-label">Workload image</p>
            <p class="mt-1 break-all font-mono text-xs">{{ environment.workload_image }}</p>
          </div>
          <div v-if="environment.workspace_id">
            <p class="lp-label">Linked workspace</p>
            <NuxtLink
              :to="`/workspaces/${environment.workspace_id}`"
              class="mt-1 inline-block font-mono text-xs text-[var(--lp-accent)] hover:underline"
            >
              Open workspace
            </NuxtLink>
          </div>
          <div>
            <p class="lp-label">Environment ID</p>
            <p class="mt-1 break-all font-mono text-xs">{{ environment.id }}</p>
          </div>
        </div>

        <div class="border-t border-[var(--lp-line)] px-5 py-4">
          <p class="lp-label mb-2">Git push rebuilds</p>
          <p class="text-sm text-[var(--lp-muted)]">
            Push to
            <span class="font-mono text-[var(--lp-text)]">{{ environment.git_branch }}</span>
            on this repo to rebuild while the environment is active.
            <template v-if="environment.gitops_rebuild_enabled">
              Webhook is configured —
              <NuxtLink to="/docs#rebuild" class="text-[var(--lp-accent)] hover:underline">docs</NuxtLink>.
            </template>
            <template v-else>
              Set
              <code class="font-mono text-xs text-[var(--lp-accent)]">WEBHOOK_SECRET</code>
              and point GitHub at
              <code class="font-mono text-xs">/api/v1/webhooks/github</code>
              —
              <NuxtLink to="/docs#rebuild" class="text-[var(--lp-accent)] hover:underline">docs</NuxtLink>.
            </template>
          </p>
          <p
            v-if="environment.latest_commit_sha"
            class="mt-2 font-mono text-xs text-[var(--lp-muted)]"
          >
            Latest commit: {{ environment.latest_commit_sha }}
          </p>
          <p
            v-if="environment.max_concurrent_environments != null"
            class="mt-2 text-xs text-[var(--lp-muted)]"
          >
            Concurrent previews:
            {{ environment.concurrent_active_count ?? '—' }}
            /
            {{ environment.max_concurrent_environments }}
          </p>
        </div>

        <div class="border-t border-[var(--lp-line)] px-5 py-3 text-sm text-[var(--lp-muted)]">
          Need custom manifests or Terraform?
          <NuxtLink to="/provision" class="text-[var(--lp-accent)] hover:underline">Open Provision</NuxtLink>
          for an IaC workspace — this page is for the live preview only.
        </div>

        <p v-if="environment.error_message" class="border-t border-[var(--lp-line)] px-5 py-3 text-sm text-[var(--lp-danger)]">
          {{ environment.error_message }}
        </p>
      </section>

      <AuditTimeline
        title="Execution pipeline"
        :entries="audits"
        :loading="auditsLoading"
        empty-label="No control-plane audit events for this preview yet."
      />

      <JobLogStream
        :lines="lines"
        :connected="connected"
        :done="done"
        :can-analyze="canAnalyze"
        :analyzing="analyzing"
        @analyze="onAnalyze"
      />

      <PreviewAnalyzerDrawer
        v-model="analyzerOpen"
        :environment-name="environment.name"
        :workspace-id="environment.workspace_id"
      />

      <ConfirmDialog
        v-model:open="confirmDestroyOpen"
        title="Destroy environment?"
        :message="`Destroy preview “${environment.name}”? Kubernetes resources for this environment will be torn down. This cannot be undone.`"
        confirm-label="Yes, destroy"
        cancel-label="No"
        :busy="destroying"
        @confirm="onDestroy"
      />
    </template>
  </div>
</template>

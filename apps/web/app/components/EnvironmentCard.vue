<script setup lang="ts">
import type { Environment, EnvironmentStatus } from '~/types/environment'

const props = defineProps<{
  environment: Environment
  retrying?: boolean
}>()

const emit = defineEmits<{
  destroy: [id: string]
  retry: [id: string]
  pause: [id: string]
  resume: [id: string]
  update: [patch: Partial<Environment>]
}>()

const CIRCUMFERENCE = 175.9
const logsOpen = ref(false)
const liveStatus = ref<EnvironmentStatus>(props.environment.status)
const liveCommit = ref<string | null>(props.environment.latest_commit_sha)

const environmentId = computed(() => props.environment.id)

const { logLines, connected } = useEnvironmentLiveStream(environmentId, {
  onEvent: (event) => {
    if (event.status) {
      liveStatus.value = event.status as EnvironmentStatus
      emit('update', { id: props.environment.id, status: liveStatus.value })
    }
    if (event.commit_sha) {
      liveCommit.value = event.commit_sha
      emit('update', {
        id: props.environment.id,
        latest_commit_sha: event.commit_sha,
      })
    }
  },
})

watch(
  () => props.environment.status,
  (value) => {
    liveStatus.value = value
  },
)

watch(
  () => props.environment.latest_commit_sha,
  (value) => {
    liveCommit.value = value
  },
)

const remaining = computed(() => {
  const expires = new Date(props.environment.ttl_expires_at).getTime()
  const created = new Date(props.environment.created_at).getTime()
  const now = Date.now()
  const totalMs = Math.max(expires - created, 1)
  const leftMs = Math.max(expires - now, 0)
  const ratio = Math.min(Math.max(leftMs / totalMs, 0), 1)
  return {
    label: formatDuration(Math.floor(leftMs / 1000), { pad: true }),
    dashOffset: CIRCUMFERENCE * (1 - ratio),
    expired: leftMs <= 0,
  }
})

const displayStatus = computed((): EnvironmentStatus => {
  if (liveStatus.value === 'EXPIRED') return 'EXPIRED'
  if (liveStatus.value === 'PAUSED' && remaining.value.expired) return 'EXPIRED'
  return liveStatus.value
})

const canResume = computed(
  () => liveStatus.value === 'PAUSED' && !remaining.value.expired,
)

const canDestroy = computed(() => {
  const s = liveStatus.value
  return s !== 'DESTROYED' && s !== 'TEARDOWN_PENDING' && s !== 'PROVISIONING'
})

const canRetry = computed(() => {
  const s = liveStatus.value
  return s === 'FAILED' || s === 'RUNNING'
})

const isRebuilding = computed(
  () => liveStatus.value === 'PROVISIONING' && Boolean(liveCommit.value),
)

const costToDate = computed(() => {
  const accrued = props.environment.cost_accrued
  if (accrued != null && accrued !== '') return accrued
  return '0.0000'
})

const costSourceLabel = computed(() => formatCostSource(props.environment.cost_source))

const isLocal = computed(() => Boolean(props.environment.is_local))

const hasPostgres = computed(() => Boolean(props.environment.enable_postgres))
const hasRedis = computed(() => Boolean(props.environment.enable_redis))

const previewHref = computed(() => {
  if (props.environment.app_ready && props.environment.preview_url) {
    return resolvePreviewUrl(props.environment)
  }
  return null
})

const portalHref = computed(
  () => props.environment.portal_url || `/p/${props.environment.id}`,
)

const canOpenApp = computed(
  () => liveStatus.value === 'RUNNING' && Boolean(previewHref.value),
)

const isProvisioning = computed(() => liveStatus.value === 'PROVISIONING')

function onRetry() {
  if (!canRetry.value || props.retrying) return
  emit('retry', props.environment.id)
}

const repoShort = computed(() => {
  const url = props.environment.git_repo_url
  try {
    const path = new URL(url).pathname.replace(/^\//, '').replace(/\.git$/, '')
    return path || url
  } catch {
    return url
  }
})
</script>

<template>
  <article
    class="lp-glass group overflow-hidden rounded-xl transition hover:border-[var(--lp-accent)]/50"
    :class="isRebuilding ? 'ring-1 ring-[var(--lp-warn)]/50' : ''"
  >
    <div class="flex items-center justify-between gap-3 border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 px-4 py-4">
      <div class="min-w-0">
        <NuxtLink
          :to="`/environments/${environment.id}`"
          class="block truncate text-base font-semibold text-[var(--lp-text)] transition hover:text-[var(--lp-accent)]"
        >
          {{ environment.name }}
        </NuxtLink>
        <p class="mt-1 flex flex-wrap items-center gap-2 font-mono text-xs text-[var(--lp-accent)]">
          <span class="inline-flex items-center gap-1 truncate">
            <span class="material-symbols-outlined text-sm">commit</span>
            <span class="truncate">{{ environment.git_branch }}</span>
          </span>
          <span
            v-if="liveCommit"
            class="rounded border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 px-1.5 py-0.5 text-[10px] text-[var(--lp-muted)] transition-all duration-300"
            :key="liveCommit"
          >
            commit: {{ liveCommit }}
          </span>
        </p>
      </div>
      <div class="flex flex-col items-end gap-1.5">
        <StatusBadge :status="displayStatus" :rebuilding="isRebuilding" />
        <EnvironmentHealthDot :status="displayStatus" :app-ready="environment.app_ready" />
      </div>
    </div>

    <div class="flex gap-4 p-4">
      <div class="min-w-0 flex-1 space-y-4">
        <div class="grid grid-cols-2 gap-4">
          <div class="space-y-1">
            <p class="lp-label">Namespace</p>
            <p class="truncate font-mono text-sm">{{ environment.namespace_name }}</p>
          </div>
          <div class="space-y-1 text-right">
            <p class="lp-label">{{ isLocal ? 'Cost (shadow)' : 'Cost to date' }}</p>
            <p class="font-mono text-sm">${{ costToDate }}</p>
            <p class="font-mono text-[10px] text-[var(--lp-muted)]">
              ${{ environment.cost_estimate_hourly }}/hr
              <span v-if="costSourceLabel" class="opacity-80"> · {{ costSourceLabel }}</span>
            </p>
          </div>
        </div>

        <div class="flex flex-wrap gap-2">
          <span
            v-if="hasPostgres"
            class="inline-flex items-center gap-1 rounded border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-sky-300"
            title="Ephemeral Postgres in this preview namespace"
          >
            <span class="material-symbols-outlined text-sm">database</span>
            Postgres
          </span>
          <span
            v-if="hasRedis"
            class="inline-flex items-center gap-1 rounded border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-rose-300"
            title="Ephemeral Redis in this preview namespace"
          >
            <span class="material-symbols-outlined text-sm">memory</span>
            Redis
          </span>
          <span
            class="inline-flex items-center gap-1.5 rounded border border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-3 py-1.5 text-xs text-[var(--lp-accent)]"
            :title="environment.git_repo_url"
          >
            <span class="material-symbols-outlined text-sm">code</span>
            <span class="max-w-[160px] truncate">{{ repoShort }}</span>
          </span>
          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded border border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-3 py-1.5 text-xs text-[var(--lp-accent)] transition hover:bg-[var(--lp-accent)]/10"
            @click="logsOpen = !logsOpen"
          >
            <span class="material-symbols-outlined text-sm">terminal</span>
            {{ logsOpen ? 'Hide logs' : 'Logs' }}
            <span
              class="h-1.5 w-1.5 rounded-full"
              :class="connected ? 'bg-[var(--lp-ok)]' : 'bg-[var(--lp-muted)]'"
              :title="connected ? 'Live stream connected' : 'Stream disconnected'"
            />
          </button>
        </div>
      </div>

      <div
        class="flex min-w-[110px] flex-col items-center justify-center rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/50 p-3"
      >
        <div class="relative mb-2 flex h-16 w-16 items-center justify-center">
          <svg class="h-full w-full -rotate-90" viewBox="0 0 64 64" aria-hidden="true">
            <circle
              cx="32"
              cy="32"
              r="28"
              fill="transparent"
              stroke="var(--lp-line)"
              stroke-width="4"
            />
            <circle
              cx="32"
              cy="32"
              r="28"
              fill="transparent"
              stroke="var(--lp-accent)"
              stroke-width="4"
              :stroke-dasharray="CIRCUMFERENCE"
              :stroke-dashoffset="remaining.dashOffset"
              stroke-linecap="round"
              class="transition-[stroke-dashoffset] duration-700"
            />
          </svg>
          <span class="material-symbols-outlined absolute text-xl text-[var(--lp-accent)]">timer</span>
        </div>
        <p class="font-mono text-[11px] text-[var(--lp-text)]">
          {{ remaining.expired ? 'Expired' : remaining.label }}
        </p>
        <p class="text-[9px] font-bold uppercase tracking-wide text-[var(--lp-muted)]">Remaining</p>
      </div>
    </div>

    <div
      v-if="logsOpen"
      class="border-t border-[var(--lp-line)] bg-[var(--lp-ink)]/70 px-4 py-3"
    >
      <p class="lp-label mb-2">Live rebuild stream</p>
      <pre
        class="max-h-40 overflow-y-auto font-mono text-[11px] leading-relaxed text-[var(--lp-muted)]"
      >{{ logLines.length ? logLines.join('\n') : 'Waiting for events…' }}</pre>
    </div>

    <div class="flex gap-2 border-t border-[var(--lp-line)] bg-[var(--lp-panel-2)]/30 px-4 py-3">
      <a
        v-if="canOpenApp && previewHref"
        :href="previewHref"
        target="_blank"
        rel="noopener noreferrer"
        class="lp-btn-primary flex-1 py-1.5 text-center text-xs uppercase tracking-wide"
      >
        Open app
      </a>
      <span
        v-else-if="isProvisioning"
        class="lp-btn-primary flex-1 cursor-default py-1.5 text-center text-xs uppercase tracking-wide opacity-60"
        title="App URL ready when Running"
      >
        Provisioning…
      </span>
      <NuxtLink
        :to="`/environments/${environment.id}`"
        class="lp-btn-ghost flex-1 py-1.5 text-xs uppercase tracking-wide"
      >
        Details
      </NuxtLink>
      <button
        v-if="liveStatus === 'RUNNING'"
        type="button"
        class="lp-btn-ghost inline-flex items-center gap-1 px-3 py-1.5 text-xs uppercase tracking-wide text-amber-400 border-amber-500/30 hover:bg-amber-500/10"
        @click="emit('pause', environment.id)"
      >
        <span class="material-symbols-outlined text-sm">pause</span>
        Pause
      </button>
      <button
        v-if="canResume"
        type="button"
        class="lp-btn-primary inline-flex items-center gap-1 px-3 py-1.5 text-xs uppercase tracking-wide bg-emerald-600 hover:bg-emerald-500 text-white"
        @click="emit('resume', environment.id)"
      >
        <span class="material-symbols-outlined text-sm">play_arrow</span>
        Resume
      </button>
      <span
        v-else-if="displayStatus === 'EXPIRED'"
        class="inline-flex items-center gap-1 px-3 py-1.5 text-xs uppercase tracking-wide text-[var(--lp-muted)]"
        title="TTL expired - resume is disabled"
      >
        <span class="material-symbols-outlined text-sm">timer_off</span>
        Expired
      </span>
      <button
        v-if="canRetry"
        type="button"
        class="lp-btn-primary inline-flex items-center gap-1 px-4 py-1.5 text-xs uppercase tracking-wide"
        :disabled="retrying"
        @click="onRetry"
      >
        <span class="material-symbols-outlined text-sm">replay</span>
        {{ retrying ? 'Retrying…' : 'Retry' }}
      </button>
      <a
        :href="portalHref"
        target="_blank"
        rel="noopener noreferrer"
        class="lp-btn-ghost px-3 py-1.5 text-xs uppercase tracking-wide"
        title="Shareable status page"
      >
        Status
      </a>
      <button
        v-if="canDestroy"
        type="button"
        class="lp-btn-danger px-4 py-1.5 text-xs uppercase tracking-wide"
        @click="emit('destroy', environment.id)"
      >
        Destroy
      </button>
    </div>
  </article>
</template>

<script setup lang="ts">
import type { Environment, EnvironmentStatus } from '~/types/environment'
import { envStreamToPatch } from '~/utils/envStreamPatch'
import { secondaryPreviewEndpoints } from '~/utils/previewEndpoints'
import {
  ttlIsExpired,
  ttlLeftSeconds,
  ttlProgressRatio,
} from '~/utils/previewTtl'

const { t } = useI18n()
const toast = useToast()

const props = defineProps<{
  environment: Environment
  retrying?: boolean
}>()

const emit = defineEmits<{
  destroy: [id: string]
  retry: [id: string]
  pause: [id: string]
  resume: [id: string]
  relaunch: [id: string]
  update: [patch: Partial<Environment>]
}>()

const CIRCUMFERENCE = 175.9
const logsOpen = ref(false)
const actionsMenuOpen = ref(false)
const liveStatus = ref<EnvironmentStatus>(props.environment.status)
const liveCommit = ref<string | null>(props.environment.latest_commit_sha)
const seenNotices = new Set<string>()
const tick = ref(0)
let tickTimer: ReturnType<typeof setInterval> | null = null

// Hold SSE only while logs are open or the env is actively changing.
// List soft-refresh covers RUNNING/PAUSED status drift without exhausting connections.
const streamEnvironmentId = computed(() => {
  if (logsOpen.value) return props.environment.id
  const status = props.environment.status
  if (status === 'PROVISIONING' || status === 'TEARDOWN_PENDING') {
    return props.environment.id
  }
  return null
})

function closeActionsMenu() {
  actionsMenuOpen.value = false
}

function toggleActionsMenu() {
  actionsMenuOpen.value = !actionsMenuOpen.value
}

function onDocClick() {
  if (actionsMenuOpen.value) closeActionsMenu()
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  tickTimer = setInterval(() => {
    tick.value += 1
  }, 1000)
})

onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
  if (tickTimer) clearInterval(tickTimer)
})

const { logLines, connected } = useEnvironmentLiveStream(streamEnvironmentId, {
  onEvent: (event) => {
    if (event.status) {
      liveStatus.value = event.status as EnvironmentStatus
    }
    if (event.commit_sha) {
      liveCommit.value = event.commit_sha
    }
    emit('update', envStreamToPatch(props.environment.id, event))
    if (event.notice && !seenNotices.has(event.notice)) {
      seenNotices.add(event.notice)
      toast.info(t('environments.toasts.portRemap'), event.notice)
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
  tick.value
  const expiresAt = props.environment.ttl_expires_at
  const leftSeconds = ttlLeftSeconds(expiresAt)
  const ratio = ttlProgressRatio(expiresAt)
  return {
    label: formatDuration(leftSeconds, { pad: true }),
    dashOffset: CIRCUMFERENCE * (1 - ratio),
    expired: ttlIsExpired(expiresAt),
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
  return s !== 'DESTROYED'
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
const alsoExposed = computed(() => secondaryPreviewEndpoints(props.environment))

const postgresStatus = computed(
  () => props.environment.postgres_status || (hasPostgres.value ? 'pending' : null),
)
const redisStatus = computed(
  () => props.environment.redis_status || (hasRedis.value ? 'pending' : null),
)

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

const detailPath = computed(() => `/environments/${props.environment.id}`)

function openDetail() {
  void navigateTo(detailPath.value)
}

function onCardKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    openDetail()
  }
}
</script>

<template>
  <article
    class="lp-glass group cursor-pointer overflow-hidden rounded-xl transition hover:border-[var(--lp-accent)]/50"
    :class="isRebuilding ? 'ring-1 ring-[var(--lp-warn)]/50' : ''"
    role="link"
    tabindex="0"
    :aria-label="environment.name"
    @click="openDetail"
    @keydown="onCardKeydown"
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
        <div class="flex flex-wrap items-center justify-end gap-1.5">
          <DeployKindBadge :deploy-mode="environment.deploy_mode" />
          <StatusBadge :status="displayStatus" :rebuilding="isRebuilding" />
          <a
            v-if="environment.jira_issue_key"
            :href="environment.jira_issue_url || undefined"
            target="_blank"
            rel="noopener noreferrer"
            class="rounded border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 px-1.5 py-0.5 font-mono text-[10px] text-[var(--lp-accent)] hover:underline"
            @click.stop
          >
            {{ environment.jira_issue_key }}
          </a>
        </div>
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
            <p class="lp-label">
              {{ isLocal ? t('environments.detail.localShadow') : t('environments.detail.costToDate') }}
            </p>
            <p class="font-mono text-sm">{{ COST_DISPLAY_SYMBOL }}{{ formatCostAmount(costToDate, { decimals: 4 }) }}</p>
            <p class="font-mono text-[10px] text-[var(--lp-muted)]">
              {{ COST_DISPLAY_SYMBOL }}{{ formatCostAmount(environment.cost_estimate_hourly, { decimals: 4 }) }}/hr
              <span v-if="costSourceLabel" class="opacity-80"> · {{ costSourceLabel }}</span>
            </p>
          </div>
        </div>

        <div class="flex flex-wrap gap-2">
          <DatastoreStatusBadge
            v-if="hasPostgres"
            name="postgres"
            :status="postgresStatus"
          />
          <DatastoreStatusBadge
            v-if="hasRedis"
            name="redis"
            :status="redisStatus"
          />
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
            @click.stop="logsOpen = !logsOpen"
          >
            <span class="material-symbols-outlined text-sm">terminal</span>
            {{ logsOpen ? t('common.close') : t('environments.card.logs') }}
            <span
              class="h-1.5 w-1.5 rounded-full"
              :class="connected ? 'bg-[var(--lp-ok)]' : 'bg-[var(--lp-muted)]'"
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
          {{ remaining.expired ? t('environments.actions.expired') : remaining.label }}
        </p>
        <p class="text-[9px] font-bold uppercase tracking-wide text-[var(--lp-muted)]">
          {{ t('environments.card.remaining') }}
        </p>
      </div>
    </div>

    <div
      v-if="logsOpen"
      class="border-t border-[var(--lp-line)] bg-[var(--lp-ink)]/70 px-4 py-3"
      @click.stop
    >
      <p class="lp-label mb-2">{{ t('environments.card.liveRebuild') }}</p>
      <pre
        class="max-h-40 overflow-y-auto font-mono text-[11px] leading-relaxed text-[var(--lp-muted)]"
      >{{ logLines.length ? logLines.join('\n') : t('common.loadingEllipsis') }}</pre>
    </div>

    <div
      v-if="alsoExposed.length"
      class="border-t border-[var(--lp-line)] bg-[var(--lp-ink)]/40 px-4 py-2.5"
      @click.stop
    >
      <p class="lp-label mb-1.5">{{ t('environments.preview.alsoExposed') }}</p>
      <PreviewEndpointsList :endpoints="alsoExposed" dense />
    </div>

    <div class="flex items-center gap-2 border-t border-[var(--lp-line)] bg-[var(--lp-panel-2)]/30 px-4 py-3" @click.stop>
      <a
        v-if="canOpenApp && previewHref"
        :href="previewHref"
        target="_blank"
        rel="noopener noreferrer"
        class="lp-btn-primary flex-1 py-1.5 text-center text-xs uppercase tracking-wide"
      >
        <span class="material-symbols-outlined mr-1 align-middle text-sm">open_in_new</span>
        {{ t('environments.card.openApp') }}
      </a>
      <span
        v-else-if="isProvisioning"
        class="lp-btn-primary flex-1 cursor-default py-1.5 text-center text-xs uppercase tracking-wide opacity-60"
      >
        {{ t('environments.detail.provisioning') }}
      </span>
      <NuxtLink
        :to="detailPath"
        class="lp-btn-ghost flex-1 py-1.5 text-xs uppercase tracking-wide"
      >
        {{ t('environments.card.details') }}
      </NuxtLink>

      <div class="relative shrink-0">
        <button
          type="button"
          class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/25 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
          :aria-expanded="actionsMenuOpen"
          aria-haspopup="menu"
          :aria-label="t('common.actions')"
          @click="toggleActionsMenu"
        >
          <span class="material-symbols-outlined text-xl">more_vert</span>
        </button>
        <div
          v-if="actionsMenuOpen"
          role="menu"
          class="absolute right-0 bottom-full z-30 mb-1.5 min-w-[180px] overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] py-1 shadow-xl"
        >
          <button
            v-if="liveStatus === 'RUNNING'"
            type="button"
            role="menuitem"
            class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-amber-300 transition hover:bg-[var(--lp-panel-2)]"
            @click="emit('pause', environment.id); closeActionsMenu()"
          >
            <span class="material-symbols-outlined text-base">pause</span>
            {{ t('environments.card.pause') }}
          </button>
          <button
            v-if="canResume"
            type="button"
            role="menuitem"
            class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-emerald-300 transition hover:bg-[var(--lp-panel-2)]"
            @click="emit('resume', environment.id); closeActionsMenu()"
          >
            <span class="material-symbols-outlined text-base">play_arrow</span>
            {{ t('environments.card.resume') }}
          </button>
          <button
            v-else-if="displayStatus === 'EXPIRED'"
            type="button"
            role="menuitem"
            class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-accent)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
            @click="emit('relaunch', environment.id); closeActionsMenu()"
          >
            <span class="material-symbols-outlined text-base">replay</span>
            {{ t('environments.actions.relaunch') }}
          </button>
          <button
            v-if="canRetry"
            type="button"
            role="menuitem"
            class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
            :disabled="retrying"
            @click="onRetry(); closeActionsMenu()"
          >
            <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">replay</span>
            {{ retrying ? t('environments.actions.retrying') : t('common.retry') }}
          </button>
          <a
            :href="portalHref"
            target="_blank"
            rel="noopener noreferrer"
            role="menuitem"
            class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)]"
            @click="closeActionsMenu()"
          >
            <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">open_in_new</span>
            {{ t('common.status') }}
          </a>
          <button
            v-if="canDestroy"
            type="button"
            role="menuitem"
            class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-danger)] transition hover:bg-[var(--lp-panel-2)]"
            @click="emit('destroy', environment.id); closeActionsMenu()"
          >
            <span class="material-symbols-outlined text-base">delete</span>
            {{ t('environments.card.destroy') }}
          </button>
        </div>
      </div>
    </div>
  </article>
</template>

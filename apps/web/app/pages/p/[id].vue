<script setup lang="ts">
import type { Environment } from '~/types/environment'
import { applyEnvStreamPatch } from '~/utils/envStreamPatch'

const route = useRoute()
const { t } = useI18n()
const id = computed(() => String(route.params.id))
const environmentId = computed(() => id.value || null)
const { getById } = useEnvironments()
const { reconcileEnvironment } = useNotifications()
const toast = useToast()

definePageMeta({
  layout: false,
})

const environment = ref<Environment | null>(null)
const loadError = ref<string | null>(null)
const tick = ref(0)
const copied = ref(false)
const shareUrl = ref('')

async function copyShareLink() {
  const url = shareUrl.value || (import.meta.client ? window.location.href : '')
  if (!url) return
  try {
    await navigator.clipboard.writeText(url)
    copied.value = true
    toast.success(t('preview.linkCopied'), t('preview.linkCopiedBody'))
    setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch {
    toast.error(t('preview.copyFailed'), t('preview.copyFailedBody'))
  }
}

const remainingLabel = computed(() => {
  tick.value
  if (!environment.value) return '-'
  return formatDuration(environment.value.time_remaining_seconds)
})

const isLive = computed(() => environment.value?.status === 'RUNNING')

const isSettled = computed(() => {
  const s = environment.value?.status
  return s === 'DESTROYED' || s === 'EXPIRED' || s === 'TEARDOWN_PENDING'
})

async function load() {
  try {
    environment.value = await getById(id.value)
    reconcileEnvironment(environment.value)
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : t('preview.notFound')
  }
}

useEnvironmentLiveStream(environmentId, {
  onEvent: (event) => {
    if (!environment.value) return
    applyEnvStreamPatch(environment.value, event)
    reconcileEnvironment(environment.value)
    if (
      event.type === 'STATUS_CHANGE'
      && (event.status === 'RUNNING' || event.status === 'FAILED')
    ) {
      void load()
    }
  },
})

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  shareUrl.value = window.location.href
  await load()
  // TTL / cost tick; status and Open-app flip via SSE.
  pollTimer = setInterval(() => {
    tick.value += 1
    if (!isSettled.value && tick.value % 30 === 0) {
      void load()
    }
  }, 1_000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div class="min-h-screen bg-[var(--lp-ink)] text-[var(--lp-text)]">
    <div class="mx-auto flex min-h-screen max-w-4xl flex-col px-6 py-10">
      <header class="mb-10 flex items-center justify-between gap-4">
        <NuxtLink to="/" class="font-semibold tracking-tight text-[var(--lp-accent)]">
          Launchpad
        </NuxtLink>
        <div v-if="environment" class="flex items-center gap-3">
          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded-lg border border-[var(--lp-line)] px-3 py-1.5 text-sm text-[var(--lp-muted)] transition hover:border-[var(--lp-accent)]/50 hover:text-[var(--lp-text)]"
            @click="copyShareLink"
          >
            <span class="material-symbols-outlined text-base">{{ copied ? 'check' : 'link' }}</span>
            {{ copied ? t('common.copied') : t('preview.copyLink') }}
          </button>
          <NuxtLink
            :to="`/environments/${environment.id}`"
            class="text-sm text-[var(--lp-muted)] hover:text-[var(--lp-text)]"
          >
            {{ t('preview.envDetails') }}
          </NuxtLink>
        </div>
      </header>

      <p v-if="loadError" class="text-[var(--lp-danger)]">{{ loadError }}</p>

      <AppSplash
        v-else-if="!environment"
        compact
        :message="t('preview.loading')"
      />

      <template v-else-if="environment">
        <div class="mb-8 space-y-3">
          <div class="flex items-center gap-3">
            <p class="font-mono text-xs uppercase tracking-[0.2em] text-[var(--lp-accent)]">{{ t('preview.live') }}</p>
            <EnvironmentHealthDot :environment="environment" />
            <a
              v-if="environment.jira_issue_key"
              :href="environment.jira_issue_url || undefined"
              target="_blank"
              rel="noopener noreferrer"
              class="rounded border border-[var(--lp-line)] px-2 py-0.5 font-mono text-xs text-[var(--lp-accent)] hover:underline"
            >
              {{ environment.jira_issue_key }}
            </a>
          </div>
          <h1 class="text-4xl font-semibold tracking-tight">{{ environment.name }}</h1>
          <p class="text-[var(--lp-muted)]">
            {{ environment.template_id || 'custom' }} · {{ environment.git_branch }}
          </p>
        </div>

        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div class="min-w-0 overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
            <p class="text-xs uppercase tracking-wide text-[var(--lp-muted)]">{{ t('preview.status') }}</p>
            <div class="mt-2">
              <StatusBadge :status="environment.status" />
            </div>
          </div>
          <div class="min-w-0 overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
            <p class="text-xs uppercase tracking-wide text-[var(--lp-muted)]">{{ t('preview.timeLeft') }}</p>
            <p class="mt-2 truncate font-mono text-lg">{{ remainingLabel }}</p>
          </div>
          <div class="min-w-0 overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
            <p class="text-xs uppercase tracking-wide text-[var(--lp-muted)]">{{ t('preview.costToDate') }}</p>
            <p class="mt-2 truncate font-mono text-lg text-[var(--lp-accent)]">${{ environment.cost_accrued }}</p>
          </div>
          <div class="min-w-0 overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
            <p class="text-xs uppercase tracking-wide text-[var(--lp-muted)]">{{ t('preview.rate') }}</p>
            <p class="mt-2 truncate font-mono text-lg">${{ environment.cost_estimate_hourly }}/hr</p>
          </div>
        </div>

        <div
          class="mt-8 flex flex-1 flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--lp-line)] bg-[var(--lp-panel)]/80 px-6 py-16 text-center"
        >
          <template v-if="isLive">
            <span class="material-symbols-outlined mb-4 text-5xl text-[var(--lp-accent)]">rocket_launch</span>
            <h2 class="text-2xl font-semibold">{{ t('preview.yourPreviewLive') }}</h2>
            <p class="mt-2 max-w-md text-sm text-[var(--lp-muted)]">
              {{ t('preview.shareableBlurb') }}
            </p>
            <div class="mt-6 flex flex-wrap justify-center gap-3">
              <a
                v-if="environment.app_ready && environment.preview_url"
                :href="environment.preview_url"
                class="inline-flex items-center gap-2 rounded-lg bg-[var(--lp-accent)] px-4 py-2 text-sm font-semibold text-[var(--lp-on-accent)]"
              >
                {{ t('preview.openApp') }}
              </a>
              <NuxtLink
                :to="`/environments/${environment.id}`"
                class="inline-flex items-center gap-2 rounded-lg border border-[var(--lp-line)] px-4 py-2 text-sm"
              >
                {{ t('preview.viewLogs') }}
              </NuxtLink>
            </div>
          </template>
          <template v-else-if="environment.status === 'PROVISIONING'">
            <span class="material-symbols-outlined mb-4 animate-pulse text-5xl text-[var(--lp-warn)]">hourglass_top</span>
            <h2 class="text-2xl font-semibold">{{ t('preview.provisioning') }}</h2>
            <p class="mt-2 text-sm text-[var(--lp-muted)]">{{ t('preview.hangTight') }}</p>
          </template>
          <template v-else>
            <span class="material-symbols-outlined mb-4 text-5xl text-[var(--lp-muted)]">cloud_off</span>
            <h2 class="text-2xl font-semibold">{{ t('preview.unavailable') }}</h2>
            <p class="mt-2 text-sm text-[var(--lp-muted)]">
              {{ t('preview.statusIs', { status: environment.status }) }}
              <span v-if="environment.error_message">{{ environment.error_message }}</span>
            </p>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

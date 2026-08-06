<script setup lang="ts">
import type { Environment, EnvironmentStatus } from '~/types/environment'

const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    /** Pass the whole environment, or the individual signals. */
    environment?: Environment | null
    status?: EnvironmentStatus
    appReady?: boolean
    /** Show a text label next to the dot. */
    showLabel?: boolean
  }>(),
  { showLabel: true },
)

type Health = 'healthy' | 'waking' | 'starting' | 'paused' | 'unhealthy' | 'stopped'

const status = computed<EnvironmentStatus | undefined>(
  () => props.status ?? props.environment?.status,
)
const appReady = computed(() => props.appReady ?? Boolean(props.environment?.app_ready))

/**
 * Derives a readiness signal from the environment's lifecycle state. We use
 * `app_ready` - the backend's "running + reachable URL" flag - as the health
 * proxy. A live HTTP smoke result would need a persisted backend field.
 */
const health = computed<Health>(() => {
  switch (status.value) {
    case 'RUNNING':
      return appReady.value ? 'healthy' : 'waking'
    case 'PROVISIONING':
      return 'starting'
    case 'PAUSED':
      return 'paused'
    case 'FAILED':
      return 'unhealthy'
    case 'EXPIRED':
    case 'TEARDOWN_PENDING':
    case 'DESTROYED':
    default:
      return 'stopped'
  }
})

const STYLE: Record<Health, { dot: string; text: string; pulse: boolean }> = {
  healthy: { dot: 'bg-[var(--lp-ok)]', text: 'text-[var(--lp-ok)]', pulse: false },
  waking: { dot: 'bg-[var(--lp-warn)]', text: 'text-[var(--lp-warn)]', pulse: true },
  starting: { dot: 'bg-[var(--lp-warn)]', text: 'text-[var(--lp-warn)]', pulse: true },
  paused: { dot: 'bg-amber-400', text: 'text-amber-400', pulse: false },
  unhealthy: { dot: 'bg-[var(--lp-danger)]', text: 'text-[var(--lp-danger)]', pulse: false },
  stopped: { dot: 'bg-[var(--lp-muted)]', text: 'text-[var(--lp-muted)]', pulse: false },
}

const style = computed(() => STYLE[health.value])

const label = computed(() => {
  switch (health.value) {
    case 'healthy':
      return t('environments.health.healthy')
    case 'waking':
      return t('environments.health.waking')
    case 'starting':
      return t('environments.health.starting')
    case 'paused':
      return t('environments.health.paused')
    case 'unhealthy':
      return t('environments.health.unhealthy')
    case 'stopped':
      return t('environments.health.stopped')
    default:
      return t('environments.health.stopped')
  }
})
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5"
    :title="label"
  >
    <span class="relative flex h-2 w-2">
      <span
        v-if="style.pulse"
        class="absolute inline-flex h-full w-full animate-ping rounded-full opacity-70"
        :class="style.dot"
      />
      <span
        class="relative inline-flex h-2 w-2 rounded-full"
        :class="[style.dot, health === 'healthy' ? 'shadow-[0_0_8px_currentColor]' : '']"
      />
    </span>
    <span
      v-if="showLabel"
      class="text-[11px] font-medium uppercase tracking-wide"
      :class="style.text"
    >
      {{ label }}
    </span>
  </span>
</template>

<script setup lang="ts">
import type { Environment, EnvironmentStatus } from '~/types/environment'

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

const META: Record<Health, { label: string; dot: string; text: string; pulse: boolean }> = {
  healthy: { label: 'Healthy', dot: 'bg-[var(--lp-ok)]', text: 'text-[var(--lp-ok)]', pulse: false },
  waking: { label: 'Waking up', dot: 'bg-[var(--lp-warn)]', text: 'text-[var(--lp-warn)]', pulse: true },
  starting: { label: 'Starting', dot: 'bg-[var(--lp-warn)]', text: 'text-[var(--lp-warn)]', pulse: true },
  paused: { label: 'Paused', dot: 'bg-amber-400', text: 'text-amber-400', pulse: false },
  unhealthy: { label: 'Unhealthy', dot: 'bg-[var(--lp-danger)]', text: 'text-[var(--lp-danger)]', pulse: false },
  stopped: { label: 'Stopped', dot: 'bg-[var(--lp-muted)]', text: 'text-[var(--lp-muted)]', pulse: false },
}

const meta = computed(() => META[health.value])
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5"
    :title="`Health: ${meta.label}`"
  >
    <span class="relative flex h-2 w-2">
      <span
        v-if="meta.pulse"
        class="absolute inline-flex h-full w-full animate-ping rounded-full opacity-70"
        :class="meta.dot"
      />
      <span
        class="relative inline-flex h-2 w-2 rounded-full"
        :class="[meta.dot, health === 'healthy' ? 'shadow-[0_0_8px_currentColor]' : '']"
      />
    </span>
    <span
      v-if="showLabel"
      class="text-[11px] font-medium uppercase tracking-wide"
      :class="meta.text"
    >
      {{ meta.label }}
    </span>
  </span>
</template>

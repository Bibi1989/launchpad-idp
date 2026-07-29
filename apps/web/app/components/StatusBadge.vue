<script setup lang="ts">
import type { EnvironmentStatus } from '~/types/environment'

const props = defineProps<{
  status: EnvironmentStatus
  rebuilding?: boolean
}>()

const tone = computed(() => {
  switch (props.status) {
    case 'RUNNING':
      return 'text-[var(--lp-ok)] border-[var(--lp-ok)]/40 bg-[var(--lp-ok)]/10 ring-1 ring-[var(--lp-ok)]/30'
    case 'PAUSED':
      return 'text-amber-400 border-amber-500/40 bg-amber-500/10'
    case 'FAILED':
      return 'text-[var(--lp-danger)] border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/10'
    case 'PROVISIONING':
    case 'TEARDOWN_PENDING':
      return 'text-[var(--lp-warn)] border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 ring-2 ring-[var(--lp-warn)]/40 animate-pulse'
    case 'DESTROYED':
      return 'text-[var(--lp-muted)] border-[var(--lp-line)] bg-[var(--lp-panel)]'
    default:
      return 'text-[var(--lp-accent)] border-[var(--lp-accent)]/40 bg-[var(--lp-accent)]/10'
  }
})

const label = computed(() => {
  if (props.status === 'PROVISIONING') {
    return props.rebuilding ? '⚡ REBUILDING' : '⚡ PROVISIONING'
  }
  if (props.status === 'RUNNING') return '🟢 RUNNING'
  if (props.status === 'PAUSED') return '⏸️ PAUSED'
  if (props.status === 'FAILED') return '🔴 FAILED'
  return props.status
})
</script>

<template>
  <span
    class="inline-flex items-center gap-2 rounded border px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-wide transition-colors duration-300"
    :class="tone"
  >
    <span
      v-if="status === 'RUNNING'"
      class="relative -ml-1 mr-0.5 h-1.5 w-1.5 rounded-full bg-current shadow-[0_0_8px_currentColor]"
    />
    <span
      v-else-if="status === 'PROVISIONING' || status === 'TEARDOWN_PENDING'"
      class="h-1.5 w-1.5 rounded-full bg-current animate-pulse-line"
    />
    {{ label }}
  </span>
</template>

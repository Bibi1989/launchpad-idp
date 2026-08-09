<script setup lang="ts">
export type DatastoreRuntimeStatus = 'pending' | 'running' | 'failed' | 'stopped'

const { t } = useI18n()

const props = defineProps<{
  name: 'postgres' | 'redis'
  status?: DatastoreRuntimeStatus | string | null
}>()

const label = computed(() =>
  props.name === 'postgres'
    ? t('environments.detail.postgres')
    : t('environments.detail.redis'),
)

const statusKey = computed(() => {
  const s = (props.status || 'pending').toLowerCase()
  if (s === 'running' || s === 'failed' || s === 'stopped' || s === 'pending') return s
  return 'pending'
})

const statusLabel = computed(() => t(`environments.datastoreStatus.${statusKey.value}`))

const tone = computed(() => {
  switch (statusKey.value) {
    case 'running':
      return props.name === 'postgres'
        ? 'border-sky-500/40 bg-sky-500/15 text-sky-200'
        : 'border-rose-500/40 bg-rose-500/15 text-rose-200'
    case 'failed':
      return 'border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/10 text-[var(--lp-danger)]'
    case 'stopped':
      return 'border-[var(--lp-line)] bg-[var(--lp-panel-2)] text-[var(--lp-muted)]'
    default:
      return 'border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 text-[var(--lp-warn)]'
  }
})

const icon = computed(() => (props.name === 'postgres' ? 'database' : 'memory'))
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide"
    :class="tone"
    :title="`${label}: ${statusLabel}`"
  >
    <span class="material-symbols-outlined text-sm">{{ icon }}</span>
    {{ label }}
    <span
      class="h-1.5 w-1.5 rounded-full"
      :class="{
        'bg-[var(--lp-ok)] shadow-[0_0_6px_var(--lp-ok)]': statusKey === 'running',
        'bg-[var(--lp-danger)]': statusKey === 'failed',
        'bg-[var(--lp-warn)] animate-pulse': statusKey === 'pending',
        'bg-[var(--lp-muted)]': statusKey === 'stopped',
      }"
    />
    <span class="font-normal normal-case tracking-normal opacity-90">{{ statusLabel }}</span>
  </span>
</template>

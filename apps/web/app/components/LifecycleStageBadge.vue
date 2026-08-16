<script setup lang="ts">
import type { LifecycleStage } from '~/types/environment'

const props = defineProps<{
  stage?: LifecycleStage | string | null
}>()

const { t } = useI18n()

const normalized = computed(() => (props.stage || 'preview').toLowerCase())

const label = computed(() => {
  if (normalized.value === 'staging') return t('environments.lifecycle.staging')
  if (normalized.value === 'production') return t('environments.lifecycle.production')
  return t('environments.lifecycle.preview')
})

const toneClass = computed(() => {
  if (normalized.value === 'production') {
    return 'border-amber-500/40 bg-amber-500/10 text-amber-200'
  }
  if (normalized.value === 'staging') {
    return 'border-sky-500/40 bg-sky-500/10 text-sky-200'
  }
  return 'border-[var(--lp-line)] bg-[var(--lp-panel-2)] text-[var(--lp-muted)]'
})
</script>

<template>
  <span
    class="inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide"
    :class="toneClass"
  >
    {{ label }}
  </span>
</template>

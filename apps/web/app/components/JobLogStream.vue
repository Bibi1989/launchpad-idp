<script setup lang="ts">
const props = defineProps<{
  lines: string[]
  connected: boolean
  done: boolean
  analyzing?: boolean
  canAnalyze?: boolean
}>()

const emit = defineEmits<{
  analyze: []
}>()

const { t } = useI18n()

const scroller = ref<HTMLElement | null>(null)

watch(
  () => props.lines.length,
  async () => {
    await nextTick()
    if (scroller.value) {
      scroller.value.scrollTop = scroller.value.scrollHeight
    }
  },
)
</script>

<template>
  <section class="lp-glass overflow-hidden rounded-xl">
    <div class="flex items-center justify-between gap-3 bg-[var(--lp-panel-2)] px-4 py-2">
      <div class="flex items-center gap-4">
        <span class="lp-label">{{ t('jobLog.title') }}</span>
        <div class="flex gap-1.5">
          <span class="h-2 w-2 rounded-full bg-[var(--lp-danger)]" />
          <span class="h-2 w-2 rounded-full bg-[var(--lp-warn)]" />
          <span class="h-2 w-2 rounded-full bg-[var(--lp-ok)]" />
        </div>
      </div>
      <div class="flex items-center gap-3">
        <button
          v-if="canAnalyze"
          type="button"
          class="lp-btn-ghost py-1 text-xs"
          :disabled="analyzing"
          @click="emit('analyze')"
        >
          <span class="material-symbols-outlined text-sm">psychology</span>
          {{ analyzing ? t('jobLog.analyzing') : t('jobLog.analyzeFailure') }}
        </button>
        <span class="font-mono text-xs text-[var(--lp-muted)]">
          <template v-if="connected">{{ t('jobLog.streaming') }}</template>
          <template v-else-if="done">{{ t('jobLog.complete') }}</template>
          <template v-else>{{ t('jobLog.idle') }}</template>
        </span>
      </div>
    </div>
    <pre
      ref="scroller"
      class="lp-console max-h-80 overflow-auto p-4 font-mono text-xs leading-6"
    ><code class="lp-console-line">{{ lines.length ? lines.join('\n') : t('jobLog.waiting') }}</code></pre>
  </section>
</template>

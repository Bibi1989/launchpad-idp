<script setup lang="ts">
const props = defineProps<{
  logs: string[]
}>()

const emit = defineEmits<{
  (e: 'clear'): void
}>()

const open = ref(true)

function copyConsoleLogs() {
  navigator.clipboard.writeText(props.logs.join('\n'))
}
</script>

<template>
  <div class="lp-glass overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/95 shadow-xl font-mono text-xs">
    <!-- Header Drawer Toggle -->
    <div
      class="flex items-center justify-between border-b border-[var(--lp-line)] px-4 py-2.5 bg-[var(--lp-panel-2)]/60 cursor-pointer select-none"
      @click="open = !open"
    >
      <div class="flex items-center gap-2">
        <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">terminal</span>
        <span class="font-semibold text-[var(--lp-text)] uppercase tracking-wider text-[11px]">
          Execution Console Buffer
        </span>
        <span class="rounded-full bg-[var(--lp-panel)] px-2 py-0.5 text-[10px] text-[var(--lp-muted)] border border-[var(--lp-line)]">
          {{ logs.length }} events
        </span>
      </div>

      <div class="flex items-center gap-3">
        <button
          v-if="open && logs.length"
          type="button"
          class="flex items-center gap-1 text-[11px] text-[var(--lp-muted)] hover:text-[var(--lp-text)]"
          @click.stop="copyConsoleLogs"
        >
          <span class="material-symbols-outlined text-sm">content_copy</span>
          Copy
        </button>
        <button
          v-if="open && logs.length"
          type="button"
          class="flex items-center gap-1 text-[11px] text-[var(--lp-muted)] hover:text-[var(--lp-danger)]"
          @click.stop="emit('clear')"
        >
          <span class="material-symbols-outlined text-sm">clear_all</span>
          Clear
        </button>
        <span class="material-symbols-outlined text-sm text-[var(--lp-muted)]">
          {{ open ? 'expand_more' : 'expand_less' }}
        </span>
      </div>
    </div>

    <!-- Buffer Content -->
    <div v-show="open" class="max-h-52 overflow-y-auto p-3 bg-black/80 font-mono text-[11px] leading-relaxed space-y-1 text-emerald-400">
      <div v-if="!logs.length" class="text-[var(--lp-muted)] italic text-center py-3">
        Console buffer ready. kubectl execution stdout/stderr logs will stream here.
      </div>
      <div
        v-for="(log, idx) in logs"
        :key="idx"
        class="whitespace-pre-wrap break-all border-b border-zinc-900/50 pb-0.5"
        :class="log.toLowerCase().includes('error') || log.toLowerCase().includes('failed') ? 'text-rose-400 font-semibold' : 'text-emerald-300'"
      >
        {{ log }}
      </div>
    </div>
  </div>
</template>

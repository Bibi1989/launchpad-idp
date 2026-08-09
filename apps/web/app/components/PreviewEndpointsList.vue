<script setup lang="ts">
import type { PreviewEndpoint } from '~/types/environment'

const props = withDefaults(
  defineProps<{
    endpoints: PreviewEndpoint[]
    dense?: boolean
  }>(),
  { dense: false },
)

const { t } = useI18n()

function kindLabel(kind: string): string {
  if (kind === 'frontend') return t('environments.preview.frontend')
  if (kind === 'backend') return t('environments.preview.backend')
  return kind
}
</script>

<template>
  <ul
    v-if="endpoints.length"
    class="space-y-1.5"
    :class="dense ? 'text-[11px]' : 'text-xs'"
  >
    <li
      v-for="ep in endpoints"
      :key="`${ep.name}-${ep.url}`"
      class="flex items-start gap-2"
    >
      <div class="min-w-0 flex-1">
        <p class="font-medium text-[var(--lp-text)]">
          {{ ep.name }}
          <span class="ml-1 font-normal text-[var(--lp-muted)]">({{ kindLabel(ep.app_kind) }})</span>
        </p>
        <a
          :href="ep.url"
          target="_blank"
          rel="noopener noreferrer"
          class="mt-0.5 block break-all font-mono text-[var(--lp-accent)] hover:underline"
        >
          {{ ep.url }}
        </a>
      </div>
      <a
        :href="ep.url"
        target="_blank"
        rel="noopener noreferrer"
        class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--lp-line)] text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-accent)]"
        :title="t('environments.preview.openTab')"
        :aria-label="t('environments.preview.openTab')"
      >
        <span class="material-symbols-outlined text-base">open_in_new</span>
      </a>
    </li>
  </ul>
</template>

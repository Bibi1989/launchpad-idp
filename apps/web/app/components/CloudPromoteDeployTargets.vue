<script setup lang="ts">
import type { CloudPromoteDeployTarget } from '~/utils/cloudPromoteDeployTargets'

defineProps<{
  targets: CloudPromoteDeployTarget[]
  provider: string
}>()

const { t } = useI18n()

function categoryLabel(category: CloudPromoteDeployTarget['category']): string {
  return t(`environments.detail.promoteTargetCategories.${category}`)
}
</script>

<template>
  <div
    v-if="targets.length"
    class="space-y-2 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 px-3 py-3"
  >
    <p class="text-sm font-medium text-[var(--lp-text)]">
      {{ t('environments.detail.promoteDeployTargets') }}
    </p>
    <p class="text-xs text-[var(--lp-muted)]">
      {{ t('environments.detail.promoteDeployTargetsHint', { provider: provider.toUpperCase() }) }}
    </p>
    <ul class="space-y-2">
      <li
        v-for="item in targets"
        :key="item.id"
        class="flex items-start justify-between gap-3 rounded-md border border-[var(--lp-line)]/80 bg-[var(--lp-ink)]/20 px-3 py-2 text-sm"
      >
        <div class="min-w-0">
          <p class="font-medium text-[var(--lp-text)]">{{ item.title }}</p>
          <p v-if="item.detail" class="mt-0.5 truncate text-xs text-[var(--lp-muted)]">
            {{ item.detail }}
          </p>
        </div>
        <span class="shrink-0 rounded-full border border-[var(--lp-line)] px-2 py-0.5 text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
          {{ categoryLabel(item.category) }}
        </span>
      </li>
    </ul>
  </div>
</template>

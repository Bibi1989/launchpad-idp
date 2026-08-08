<script setup lang="ts">
import type { CloudServiceOption } from '~/utils/cloudServiceOptions'

const props = withDefaults(
  defineProps<{
    options: CloudServiceOption[]
    /** Provider resources object (boolean toggles + nested string fields). */
    resources: Record<string, boolean | string | null | undefined>
    disabled?: boolean
    /** denser grid for AWS/Azure/Cloudflare */
    columns?: 1 | 2 | 3
  }>(),
  {
    disabled: false,
    columns: 1,
  },
)

const gridClass = computed(() => {
  if (props.columns === 3) return 'grid gap-2 sm:grid-cols-3'
  if (props.columns === 2) return 'grid gap-2 sm:grid-cols-2'
  return 'space-y-2'
})

function isEnabled(key: string): boolean {
  return props.resources[key] === true
}

function setEnabled(key: string, enabled: boolean) {
  props.resources[key] = enabled
}
</script>

<template>
  <div :class="gridClass">
    <div
      v-for="opt in options"
      :key="opt.key"
      class="rounded-lg border border-[var(--lp-line)] p-3"
    >
      <label class="flex cursor-pointer items-center justify-between gap-3">
        <span class="min-w-0">
          <span class="block text-sm font-medium text-[var(--lp-text)]">{{ opt.title }}</span>
          <span v-if="opt.desc" class="block text-xs text-[var(--lp-muted)]">{{ opt.desc }}</span>
        </span>
        <input
          type="checkbox"
          class="h-5 w-5 shrink-0 accent-[var(--lp-accent)]"
          :checked="isEnabled(opt.key)"
          :disabled="disabled"
          @change="setEnabled(opt.key, ($event.target as HTMLInputElement).checked)"
        >
      </label>

      <div
        v-if="isEnabled(opt.key) && opt.nestedOptions?.length"
        class="mt-3 space-y-2 border-t border-[var(--lp-line)] pt-3"
      >
        <label
          v-for="nested in opt.nestedOptions"
          :key="nested.field"
          class="block space-y-1.5"
        >
          <span class="lp-label">{{ nested.label }}</span>
          <select
            v-model="resources[nested.field]"
            class="lp-input text-sm"
            :disabled="disabled"
          >
            <option
              v-for="choice in nested.choices"
              :key="choice.value"
              :value="choice.value"
            >
              {{ choice.label }}
            </option>
          </select>
        </label>
      </div>
    </div>
  </div>
</template>

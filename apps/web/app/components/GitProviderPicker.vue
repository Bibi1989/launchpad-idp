<script setup lang="ts">
import type { GitHost } from '~/types/git'

const { t } = useI18n()

const props = withDefaults(defineProps<{
  modelValue: GitHost
  size?: 'sm' | 'md'
}>(), {
  size: 'md',
})

const emit = defineEmits<{
  'update:modelValue': [value: GitHost]
}>()

const options = computed(() => [
  { id: 'github' as const, label: t('integrations.github'), hint: 'github.com' },
  { id: 'gitlab' as const, label: 'GitLab', hint: t('integrations.gitlabHint') },
])
</script>

<template>
  <div class="space-y-2">
    <span class="lp-label">{{ t('common.gitProvider') }}</span>
    <div class="grid grid-cols-2 gap-2">
      <button
        v-for="opt in options"
        :key="opt.id"
        type="button"
        class="rounded-lg border px-3 text-left transition"
        :class="[
          props.size === 'sm' ? 'py-2' : 'py-3',
          modelValue === opt.id
            ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
            : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)]',
        ]"
        @click="emit('update:modelValue', opt.id)"
      >
        <p class="text-sm font-medium text-[var(--lp-text)]">{{ opt.label }}</p>
        <p class="text-[11px] text-[var(--lp-muted)]">{{ opt.hint }}</p>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { GitHubInstallationItem } from '~/types/provisioning'
import { githubAccountTypeLabel } from '~/utils/githubAccount'

const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    installations: GitHubInstallationItem[]
    modelValue: number | null
    disabled?: boolean
    label?: string
    manageLink?: boolean
  }>(),
  {
    disabled: false,
    manageLink: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: number | null]
}>()

const selected = computed(
  () => props.installations.find((item) => item.id === props.modelValue) ?? null,
)

const resolvedLabel = computed(() => props.label ?? t('integrations.githubAccount'))

function select(id: number) {
  if (props.disabled || props.modelValue === id) return
  emit('update:modelValue', id)
}
</script>

<template>
  <div v-if="installations.length" class="space-y-2">
    <p class="lp-label">{{ resolvedLabel }}</p>
    <div class="flex flex-wrap gap-2">
      <button
        v-for="item in installations"
        :key="item.id"
        type="button"
        class="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition"
        :class="
          modelValue === item.id
            ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
            : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:border-[var(--lp-accent)]/40'
        "
        :disabled="disabled"
        @click="select(item.id)"
      >
        <span
          class="flex h-6 w-6 items-center justify-center rounded-full bg-[var(--lp-panel-2)] font-mono text-[10px]"
        >
          {{ item.account_login.slice(0, 2).toUpperCase() }}
        </span>
        {{ item.account_login }}
        <span class="font-mono text-[10px] opacity-60">{{ githubAccountTypeLabel(item) }}</span>
      </button>
    </div>
    <p v-if="selected && manageLink" class="text-xs text-[var(--lp-muted)]">
      {{ t('integrations.using') }}
      <span class="text-[var(--lp-text)]">{{ selected.account_login }}</span>
      ({{ githubAccountTypeLabel(selected) }}).
      <NuxtLink to="/integrations/github" class="text-[var(--lp-accent)] hover:underline">{{ t('integrations.manageInstalls') }}</NuxtLink>
    </p>
  </div>
</template>

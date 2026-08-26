<script setup lang="ts">
defineProps<{
  overrideMode: boolean
}>()

const open = defineModel<boolean>('open', { required: true })
const { t } = useI18n()
</script>

<template>
  <p v-if="overrideMode" class="text-xs text-[var(--lp-muted)]">
    {{ t('credentials.fields.overrideHint') }}
  </p>
  <button
    v-if="overrideMode"
    type="button"
    class="flex w-full items-center justify-between rounded-lg border border-[var(--lp-line)] px-3 py-2 text-left text-xs"
    @click="open = !open"
  >
    <span class="font-medium text-[var(--lp-text)]">{{ t('credentials.fields.overrideToggle') }}</span>
    <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">
      {{ open ? 'expand_less' : 'expand_more' }}
    </span>
  </button>
</template>

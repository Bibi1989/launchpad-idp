<script setup lang="ts">
withDefaults(
  defineProps<{
    size?: 'sm' | 'md' | 'lg'
    showWordmark?: boolean
  }>(),
  {
    size: 'md',
    showWordmark: true,
  },
)

const { t } = useI18n()

const sizeClass = {
  sm: 'h-8 w-8',
  md: 'h-10 w-10',
  lg: 'h-16 w-16',
} as const

const titleClass = {
  sm: 'text-lg',
  md: 'text-xl',
  lg: 'text-3xl',
} as const
</script>

<template>
  <div class="inline-flex items-center gap-3">
    <img
      src="/logo.svg"
      :alt="t('brand.name')"
      class="shrink-0 rounded-xl"
      :class="sizeClass[size]"
      width="64"
      height="64"
      decoding="async"
    />
    <div v-if="showWordmark" class="min-w-0 text-left">
      <p
        class="font-semibold tracking-tight text-[var(--lp-accent)]"
        :class="titleClass[size]"
      >
        {{ t('brand.name') }}
      </p>
      <p class="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--lp-muted)]">
        {{ t('brand.tagline') }}
      </p>
    </div>
  </div>
</template>

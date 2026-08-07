<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    /** Full-viewport overlay (auth boot). Default is an in-page panel. */
    fullscreen?: boolean
    /** Primary status line under the brand. */
    message?: string
    /** Optional secondary context (workspace name, env id, etc.). */
    detail?: string | null
    /** Smaller mark + type for dense pages. */
    compact?: boolean
  }>(),
  {
    fullscreen: false,
    message: undefined,
    detail: null,
    compact: false,
  },
)

const { t } = useI18n()

const resolvedMessage = computed(() => props.message ?? t('shell.initializing'))
</script>

<template>
  <div
    class="lp-splash flex flex-col items-center justify-center"
    :class="fullscreen
      ? 'fixed inset-0 z-[9999]'
      : compact
        ? 'min-h-[16rem] rounded-xl border border-[var(--lp-line)] px-6 py-12'
        : 'min-h-[22rem] rounded-xl border border-[var(--lp-line)] px-6 py-16'"
    role="status"
    aria-live="polite"
    aria-busy="true"
  >
    <div class="relative flex flex-col items-center text-center animate-pulse">
      <BrandLogo
        :size="compact ? 'md' : 'lg'"
        :show-wordmark="false"
        class="mb-5"
        :style="{ boxShadow: '0 0 40px var(--lp-splash-glow)' }"
      />
      <h1
        class="font-bold tracking-tight text-[var(--lp-text)]"
        :class="compact ? 'text-xl' : 'text-3xl'"
      >
        {{ t('brand.name') }}
      </h1>
      <p
        class="mt-2 font-mono uppercase tracking-[0.28em] text-[var(--lp-muted)]"
        :class="compact ? 'text-[10px]' : 'text-xs'"
      >
        {{ resolvedMessage }}
      </p>
      <p
        v-if="detail"
        class="mt-3 max-w-sm truncate text-sm text-[var(--lp-text)]/80"
      >
        {{ detail }}
      </p>
    </div>
  </div>
</template>

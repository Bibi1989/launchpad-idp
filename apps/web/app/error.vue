<script setup lang="ts">
import type { NuxtError } from '#app'

/**
 * Vue production builds replace the errorHandler "info" string with a short code.
 * Nuxt's default error page shows `error.message`; when that is only a digit, it is
 * almost always this Vue info code (not an HTTP sub-status).
 * @see https://vuejs.org/error-reference/#runtime-2
 */
const VUE_RUNTIME_INFO: Record<string, string> = {
  '0': 'setup function',
  '1': 'render function',
  '2': 'watcher getter',
  '3': 'watcher callback',
  '4': 'watcher cleanup function',
  '5': 'native event handler',
  '6': 'component event handler',
  '7': 'vnode hook',
  '8': 'directive hook',
  '9': 'transition hook',
  '10': 'app errorHandler',
  '11': 'app warnHandler',
  '12': 'ref function',
  '13': 'async component loader',
  '14': 'scheduler flush',
  '15': 'component update',
  '16': 'app unmount cleanup function',
}

const props = defineProps<{ error: NuxtError }>()

const statusCode = computed(() => {
  const raw = props.error.statusCode ?? props.error.status ?? 500
  const n = Number(raw)
  return Number.isFinite(n) && n >= 100 && n <= 599 ? n : 500
})

const statusText = computed(
  () =>
    props.error.statusMessage
    || props.error.statusText
    || (statusCode.value === 404 ? 'Page Not Found' : 'Internal Server Error'),
)

const rawMessage = computed(() => {
  const msg = props.error.message
  if (typeof msg === 'string' && msg.trim()) return msg.trim()
  if (typeof msg === 'number') return String(msg)
  try {
    return String(props.error)
  } catch {
    return ''
  }
})

const vueInfoLabel = computed(() => {
  const key = rawMessage.value
  if (!/^\d{1,2}$/.test(key) && !/^(sp|bc|c|bm|m|bu|u|bum|um|a|da|ec|rtc|rtg)$/.test(key)) {
    return null
  }
  return VUE_RUNTIME_INFO[key] ?? null
})

const description = computed(() => {
  if (vueInfoLabel.value) {
    return `Client crash during ${vueInfoLabel.value} (Vue production code ${rawMessage.value}). Open the browser console for the real stack trace.`
  }
  if (rawMessage.value && rawMessage.value !== statusText.value) {
    return rawMessage.value
  }
  return 'Something went wrong while loading Launchpad. Try again, or sign in again if you were authenticated.'
})

function handleClear() {
  void clearError({ redirect: '/' })
}

function handleReload() {
  if (import.meta.client) {
    window.location.reload()
  }
}

if (import.meta.client) {
  console.error('[launchpad] fatal error', {
    statusCode: statusCode.value,
    statusText: statusText.value,
    message: rawMessage.value,
    vueInfo: vueInfoLabel.value,
    data: props.error.data,
    cause: props.error.cause,
    error: props.error,
  })
}
</script>

<template>
  <div class="flex min-h-screen flex-col items-center justify-center bg-[var(--lp-ink,#0c1219)] px-6 text-center text-[var(--lp-text,#f8fafc)]">
    <p class="font-mono text-xs uppercase tracking-[0.28em] text-[var(--lp-accent,#2dd4bf)]">
      Launchpad
    </p>
    <h1 class="mt-4 text-6xl font-semibold tabular-nums tracking-tight sm:text-7xl">
      {{ statusCode }}
    </h1>
    <h2 class="mt-3 text-xl font-medium sm:text-2xl">
      {{ statusText }}
    </h2>
    <p class="mt-4 max-w-lg text-sm leading-relaxed text-[var(--lp-muted,#94a3b8)]">
      {{ description }}
    </p>
    <div class="mt-8 flex flex-wrap items-center justify-center gap-3">
      <button
        type="button"
        class="rounded-lg bg-[var(--lp-accent,#2dd4bf)] px-4 py-2 text-sm font-medium text-[var(--lp-ink,#0c1219)] transition hover:opacity-90"
        @click="handleClear"
      >
        Back to home
      </button>
      <button
        type="button"
        class="rounded-lg border border-[var(--lp-line,#1e293b)] px-4 py-2 text-sm text-[var(--lp-muted,#94a3b8)] transition hover:bg-[var(--lp-panel,#111827)] hover:text-[var(--lp-text,#f8fafc)]"
        @click="handleReload"
      >
        Reload page
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { ToastType } from '~/composables/useToast'

const { toasts, dismiss } = useToast()
const { t } = useI18n()

const iconFor: Record<ToastType, string> = {
  success: 'check_circle',
  error: 'error',
  warning: 'warning',
  info: 'info',
}

const toneFor: Record<ToastType, string> = {
  success: 'border-[var(--lp-ok)]/40 text-[var(--lp-ok)]',
  error: 'border-[var(--lp-danger)]/40 text-[var(--lp-danger)]',
  warning: 'border-[var(--lp-warn)]/40 text-[var(--lp-warn)]',
  info: 'border-[var(--lp-accent)]/40 text-[var(--lp-accent)]',
}
</script>

<template>
  <Teleport to="body">
    <div
      class="pointer-events-none fixed bottom-4 right-4 z-[200] flex w-[min(92vw,24rem)] flex-col gap-3"
      role="region"
      :aria-label="t('notifications.title')"
    >
      <TransitionGroup
        enter-active-class="transition duration-200 ease-out"
        enter-from-class="translate-y-2 opacity-0"
        enter-to-class="translate-y-0 opacity-100"
        leave-active-class="transition duration-150 ease-in absolute w-full"
        leave-from-class="opacity-100"
        leave-to-class="translate-x-4 opacity-0"
      >
        <div
          v-for="toast in toasts"
          :key="toast.id"
          class="pointer-events-auto relative overflow-hidden rounded-xl border bg-[var(--lp-panel)]/95 p-3.5 pr-9 shadow-2xl backdrop-blur-md"
          :class="toneFor[toast.type]"
          :role="toast.type === 'error' ? 'alert' : 'status'"
        >
          <div class="flex items-start gap-3">
            <span class="material-symbols-outlined mt-0.5 text-lg">{{ iconFor[toast.type] }}</span>
            <div class="min-w-0 flex-1">
              <p class="text-sm font-semibold text-[var(--lp-text)]">{{ toast.title }}</p>
              <p v-if="toast.message" class="mt-0.5 break-words text-xs text-[var(--lp-muted)]">
                {{ toast.message }}
              </p>
            </div>
          </div>
          <button
            type="button"
            class="absolute right-2 top-2 rounded-md p-1 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
            :aria-label="t('notifications.dismissAria')"
            @click="dismiss(toast.id)"
          >
            <span class="material-symbols-outlined text-base">close</span>
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

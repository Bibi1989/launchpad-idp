<script setup lang="ts">
import { notificationIcon, type NotificationKind } from '~/composables/useNotifications'

const { items, unreadCount, hydrate, markAllRead, markRead, remove, clear } = useNotifications()
const { t } = useI18n()

const open = ref(false)
const rootRef = ref<HTMLElement | null>(null)

function onDocClick(e: MouseEvent) {
  if (open.value && rootRef.value && !rootRef.value.contains(e.target as Node)) {
    open.value = false
  }
}

onMounted(() => {
  hydrate()
  document.addEventListener('click', onDocClick)
})

onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
})

function toggle() {
  open.value = !open.value
  if (open.value) markAllRead()
}

const toneFor: Record<NotificationKind, string> = {
  ready: 'text-[var(--lp-ok)]',
  failed: 'text-[var(--lp-danger)]',
  ttl: 'text-[var(--lp-warn)]',
  cost: 'text-[var(--lp-danger)]',
  paused: 'text-amber-400',
  info: 'text-[var(--lp-accent)]',
  invite: 'text-[var(--lp-accent)]',
}

async function openNotification(n: { id: string, envId?: string, href?: string }) {
  markRead(n.id)
  open.value = false
  if (n.href?.startsWith('/')) {
    await navigateTo(n.href)
    return
  }
  if (n.envId) await navigateTo(`/environments/${n.envId}`)
}
</script>

<template>
  <div ref="rootRef" class="relative">
    <button
      type="button"
      class="relative flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)] text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
      :aria-expanded="open"
      aria-haspopup="menu"
      :aria-label="t('notifications.title')"
      @click="toggle"
    >
      <span class="material-symbols-outlined text-lg">notifications</span>
      <span
        v-if="unreadCount > 0"
        class="absolute -right-1 -top-1 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-[var(--lp-danger)] px-1 text-[10px] font-bold text-white"
      >
        {{ unreadCount > 9 ? '9+' : unreadCount }}
      </span>
    </button>

    <div
      v-if="open"
      role="menu"
      class="absolute right-0 top-full z-50 mt-2 w-[min(92vw,22rem)] overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] shadow-2xl"
    >
      <div class="flex items-center justify-between border-b border-[var(--lp-line)] px-4 py-2.5">
        <p class="text-sm font-semibold text-[var(--lp-text)]">{{ t('notifications.title') }}</p>
        <button
          v-if="items.length"
          type="button"
          class="text-xs text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
          @click="clear"
        >
          {{ t('notifications.clearAll') }}
        </button>
      </div>

      <div class="max-h-[22rem] overflow-y-auto">
        <p
          v-if="!items.length"
          class="px-4 py-8 text-center text-sm text-[var(--lp-muted)]"
        >
          {{ t('notifications.empty') }}
        </p>
        <ul v-else class="divide-y divide-[var(--lp-line)]">
          <li
            v-for="n in items"
            :key="n.id"
            class="group flex items-start gap-3 px-4 py-3 transition hover:bg-[var(--lp-panel-2)]"
            :class="n.envId || n.href ? 'cursor-pointer' : ''"
            @click="openNotification(n)"
          >
            <span
              class="material-symbols-outlined mt-0.5 text-lg"
              :class="toneFor[n.kind]"
            >
              {{ notificationIcon(n.kind) }}
            </span>
            <div class="min-w-0 flex-1">
              <p class="text-sm font-medium text-[var(--lp-text)]">{{ n.title }}</p>
              <p v-if="n.body" class="mt-0.5 break-words text-xs text-[var(--lp-muted)]">
                {{ n.body }}
              </p>
              <p class="mt-1 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
                {{ formatRelativeTime(n.ts) }}
              </p>
            </div>
            <button
              type="button"
              class="rounded-md p-1 text-[var(--lp-muted)] opacity-0 transition hover:bg-[var(--lp-panel)] hover:text-[var(--lp-text)] group-hover:opacity-100"
              :aria-label="t('notifications.dismissAria')"
              @click.stop="remove(n.id)"
            >
              <span class="material-symbols-outlined text-sm">close</span>
            </button>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

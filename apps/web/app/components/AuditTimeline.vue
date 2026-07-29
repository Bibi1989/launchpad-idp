<script setup lang="ts">
import type { AuditLogEntry } from '~/types/environment'

const props = defineProps<{
  entries: AuditLogEntry[]
  loading?: boolean
  emptyLabel?: string
}>()

function statusClass(status: string): string {
  if (status === 'SUCCESS') return 'text-[var(--lp-ok)]'
  if (status === 'FAILURE' || status === 'REJECTED') return 'text-[var(--lp-danger)]'
  return 'text-[var(--lp-warn)]'
}

function actionClass(action: string): string {
  if (action === 'DRIFT_DETECTED') return 'text-[var(--lp-warn)]'
  return 'text-[var(--lp-text)]'
}

function formatTime(value: string): string {
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}
</script>

<template>
  <section class="lp-glass overflow-hidden rounded-xl">
    <div class="flex items-center justify-between bg-[var(--lp-panel-2)] px-4 py-2">
      <span class="lp-label">Audit trail</span>
      <span class="font-mono text-xs text-[var(--lp-muted)]">
        {{ loading ? 'loading' : `${entries.length} event${entries.length === 1 ? '' : 's'}` }}
      </span>
    </div>
    <div class="max-h-64 overflow-auto p-4">
      <p v-if="loading && !entries.length" class="text-sm text-[var(--lp-muted)]">Loading audit events…</p>
      <p v-else-if="!entries.length" class="text-sm text-[var(--lp-muted)]">
        {{ emptyLabel || 'No audit events yet.' }}
      </p>
      <ol v-else class="space-y-3">
        <li
          v-for="entry in entries"
          :key="entry.id"
          class="border-b border-[var(--lp-line)] pb-3 last:border-0 last:pb-0"
        >
          <div class="flex flex-wrap items-baseline justify-between gap-2">
            <p class="font-mono text-xs" :class="actionClass(entry.action)">{{ entry.action }}</p>
            <p class="font-mono text-[10px] text-[var(--lp-muted)]">
              {{ formatTime(entry.timestamp) }}
            </p>
          </div>
          <p class="mt-1 text-xs">
            <span :class="statusClass(entry.status)">{{ entry.status }}</span>
            <span class="text-[var(--lp-muted)]"> · actor {{ entry.actor_id.slice(0, 8) }}</span>
            <span v-if="entry.commit_sha" class="font-mono text-[var(--lp-muted)]">
              · {{ entry.commit_sha.slice(0, 7) }}
            </span>
          </p>
          <p v-if="entry.detail" class="mt-1 text-xs text-[var(--lp-muted)]">
            {{ entry.detail }}
          </p>
        </li>
      </ol>
    </div>
  </section>
</template>

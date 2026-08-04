<script setup lang="ts">
import type { AuditLogEntry } from '~/types/environment'

const props = defineProps<{
  entries: AuditLogEntry[]
  loading?: boolean
  emptyLabel?: string
  title?: string
}>()

function statusTone(status: string): 'ok' | 'danger' | 'warn' | 'muted' {
  if (status === 'SUCCESS') return 'ok'
  if (status === 'FAILURE' || status === 'REJECTED') return 'danger'
  if (status === 'PENDING') return 'warn'
  return 'muted'
}

function formatTime(value: string): string {
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function correlationId(entry: AuditLogEntry): string {
  return entry.workspace_id || entry.environment_id || entry.id
}

function shortActor(actorId: string): string {
  return actorId.length > 8 ? actorId.slice(0, 8) : actorId
}

function isLive(index: number, entry: AuditLogEntry): boolean {
  return index === 0 && entry.status === 'SUCCESS'
}
</script>

<template>
  <section class="lp-glass overflow-hidden rounded-xl">
    <div class="flex items-center justify-between border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/80 px-5 py-3">
      <span class="lp-label">{{ title || 'Execution pipeline' }}</span>
      <span class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
        {{ loading ? 'loading…' : `${entries.length} event${entries.length === 1 ? '' : 's'} total` }}
      </span>
    </div>

    <div class="max-h-[28rem] overflow-auto p-5">
      <p v-if="loading && !entries.length" class="text-sm text-[var(--lp-muted)]">
        Loading audit events…
      </p>
      <p v-else-if="!entries.length" class="text-sm text-[var(--lp-muted)]">
        {{ emptyLabel || 'No audit events yet.' }}
      </p>

      <ol v-else class="relative space-y-0">
        <li
          v-for="(entry, index) in entries"
          :key="entry.id"
          class="relative flex gap-4 pb-6 last:pb-0"
        >
          <div
            v-if="index < entries.length - 1"
            class="absolute left-[11px] top-6 h-[calc(100%-0.5rem)] w-px bg-[var(--lp-accent)]/25"
            aria-hidden="true"
          />

          <div class="relative z-10 mt-1 flex h-6 w-6 shrink-0 items-center justify-center">
            <span
              v-if="entry.status === 'FAILURE' || entry.status === 'REJECTED'"
              class="flex h-6 w-6 items-center justify-center rounded-full border border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/15"
            >
              <span class="material-symbols-outlined text-sm text-[var(--lp-danger)]">close</span>
            </span>
            <span
              v-else-if="isLive(index, entry)"
              class="flex h-6 w-6 items-center justify-center rounded-full border border-[var(--lp-ok)]/40 bg-[var(--lp-ok)]/15"
            >
              <span class="h-2 w-2 rounded-full bg-[var(--lp-ok)]" />
            </span>
            <span
              v-else
              class="flex h-6 w-6 items-center justify-center rounded-full border border-[var(--lp-line)] bg-[var(--lp-panel-2)]"
            >
              <span class="material-symbols-outlined text-sm text-[var(--lp-muted)]">check</span>
            </span>
          </div>

          <article
            class="min-w-0 flex-1 rounded-lg border px-4 py-3 transition"
            :class="entry.status === 'FAILURE' || entry.status === 'REJECTED'
              ? 'border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/5'
              : isLive(index, entry)
                ? 'border-[var(--lp-accent)]/50 bg-[var(--lp-accent)]/5 shadow-[0_0_0_1px_rgba(45,212,191,0.08)]'
                : 'border-[var(--lp-line)] bg-[var(--lp-panel)]/50'"
          >
            <div class="flex flex-wrap items-start justify-between gap-2">
              <div class="flex flex-wrap items-center gap-2">
                <p class="font-mono text-xs font-semibold uppercase tracking-wide text-[var(--lp-text)]">
                  {{ entry.action }}
                </p>
                <span
                  v-if="isLive(index, entry)"
                  class="rounded border border-[var(--lp-ok)]/40 bg-[var(--lp-ok)]/15 px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-[var(--lp-ok)]"
                >
                  Live
                </span>
              </div>
              <p class="shrink-0 font-mono text-[10px] text-[var(--lp-muted)]">
                {{ formatTime(entry.timestamp) }}
              </p>
            </div>

            <p class="mt-2 font-mono text-[10px] text-[var(--lp-accent)]">
              actor: {{ shortActor(entry.actor_id) }}
              <span class="text-[var(--lp-muted)]"> · </span>
              correlation_id: {{ correlationId(entry).slice(0, 18) }}…
            </p>

            <p
              v-if="entry.detail"
              class="mt-2 text-xs italic leading-relaxed text-[var(--lp-muted)]"
            >
              {{ entry.detail }}
            </p>
            <p
              v-else-if="entry.commit_sha"
              class="mt-2 font-mono text-[10px] text-[var(--lp-muted)]"
            >
              commit {{ entry.commit_sha.slice(0, 7) }}
              <span
                class="ml-2 not-italic"
                :class="{
                  'text-[var(--lp-ok)]': statusTone(entry.status) === 'ok',
                  'text-[var(--lp-danger)]': statusTone(entry.status) === 'danger',
                  'text-[var(--lp-warn)]': statusTone(entry.status) === 'warn',
                }"
              >
                {{ entry.status }}
              </span>
            </p>
          </article>
        </li>
      </ol>
    </div>
  </section>
</template>

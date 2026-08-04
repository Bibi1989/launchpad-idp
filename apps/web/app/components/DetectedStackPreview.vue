<script setup lang="ts">
import type { DetectedService, DetectionResult } from '~/types/repoImport'

const props = defineProps<{
  detection: DetectionResult
  services: DetectedService[]
}>()

const emit = defineEmits<{
  'update:services': [services: DetectedService[]]
}>()

function toggleEnabled(id: string) {
  emit(
    'update:services',
    props.services.map((s) => (s.id === id ? { ...s, enabled: !s.enabled } : s)),
  )
}

function setPort(id: string, port: number) {
  const safe = Number.isFinite(port) && port > 0 && port <= 65535 ? Math.floor(port) : 8080
  emit(
    'update:services',
    props.services.map((s) => (s.id === id ? { ...s, port: safe } : s)),
  )
}

function setPreviewTarget(id: string) {
  emit(
    'update:services',
    props.services.map((s) => ({
      ...s,
      is_preview_target: s.id === id,
      enabled: s.id === id ? true : s.enabled,
    })),
  )
}

function roleBadge(role: string) {
  if (role === 'web') return 'bg-sky-500/15 text-sky-300 border-sky-500/30'
  if (role === 'api') return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
  return 'bg-[var(--lp-panel-2)] text-[var(--lp-muted)] border-[var(--lp-line)]'
}
</script>

<template>
  <div class="space-y-4">
    <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-ink)]/30 p-4">
      <p class="text-sm font-medium text-[var(--lp-text)]">Detected architecture</p>
      <p class="mt-1 text-xs text-[var(--lp-muted)]">{{ detection.summary }}</p>
      <div class="mt-3 flex flex-wrap gap-2">
        <span
          class="rounded-md border border-[var(--lp-line)] px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]"
        >
          {{ detection.layout }}
        </span>
        <span
          v-for="tool in detection.monorepo_tools.filter((t) => t !== 'none')"
          :key="tool"
          class="rounded-md border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/10 px-2 py-0.5 font-mono text-[10px] text-[var(--lp-accent)]"
        >
          {{ tool }}
        </span>
        <span
          v-for="ds in detection.datastores"
          :key="ds"
          class="rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 font-mono text-[10px] text-amber-200"
        >
          {{ ds }}
        </span>
      </div>
    </div>

    <ul class="space-y-3">
      <li
        v-for="svc in services"
        :key="svc.id"
        class="rounded-xl border p-4 transition"
        :class="
          svc.enabled
            ? 'border-[var(--lp-line)] bg-[var(--lp-panel)]'
            : 'border-[var(--lp-line)]/60 bg-[var(--lp-ink)]/20 opacity-60'
        "
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0 space-y-1">
            <div class="flex flex-wrap items-center gap-2">
              <p class="font-mono text-sm font-semibold text-[var(--lp-text)]">{{ svc.name }}</p>
              <span
                class="rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide"
                :class="roleBadge(svc.role)"
              >
                {{ svc.role }}
              </span>
              <span class="font-mono text-[10px] text-[var(--lp-muted)]">{{ svc.framework }}</span>
              <span
                v-if="svc.is_preview_target"
                class="rounded border border-[var(--lp-accent)]/40 bg-[var(--lp-accent)]/10 px-1.5 py-0.5 text-[10px] text-[var(--lp-accent)]"
              >
                preview target
              </span>
            </div>
            <p class="font-mono text-[11px] text-[var(--lp-muted)]">path: {{ svc.path }}</p>
            <p v-if="svc.has_dockerfile" class="text-[11px] text-[var(--lp-muted)]">
              Dockerfile: {{ svc.dockerfile_path || 'present' }}
            </p>
            <p v-else class="text-[11px] text-amber-200/80">
              No Dockerfile - Launchpad will scaffold one on save
            </p>
          </div>
          <label class="inline-flex items-center gap-2 text-xs text-[var(--lp-muted)]">
            <input
              type="checkbox"
              class="accent-[var(--lp-accent)]"
              :checked="svc.enabled"
              @change="toggleEnabled(svc.id)"
            >
            Include
          </label>
        </div>

        <div class="mt-3 flex flex-wrap items-end gap-3">
          <label class="block space-y-1">
            <span class="lp-label">Port</span>
            <input
              type="number"
              min="1"
              max="65535"
              class="lp-input w-28 font-mono text-xs"
              :value="svc.port"
              :disabled="!svc.enabled"
              @change="setPort(svc.id, Number(($event.target as HTMLInputElement).value))"
            >
          </label>
          <button
            type="button"
            class="lp-btn-ghost text-xs"
            :disabled="!svc.enabled || svc.is_preview_target"
            @click="setPreviewTarget(svc.id)"
          >
            {{ svc.is_preview_target ? 'Primary preview' : 'Set as preview target' }}
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>

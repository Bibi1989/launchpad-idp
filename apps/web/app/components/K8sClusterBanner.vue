<script setup lang="ts">
import type { K8sClusterContext } from '~/types/k8s'

const props = defineProps<{
  context: K8sClusterContext | null
  loading: boolean
  applying: boolean
}>()

const emit = defineEmits<{
  (e: 'apply'): void
  (e: 'refresh'): void
  (e: 'deleteWorkspace'): void
}>()

const providerBadgeClass = computed(() => {
  const p = props.context?.provider?.toLowerCase() || 'local'
  if (p === 'gcp' || p.includes('gke')) return 'bg-blue-500/10 text-blue-400 border-blue-500/30'
  if (p === 'aws' || p.includes('eks')) return 'bg-amber-500/10 text-amber-400 border-amber-500/30'
  if (p === 'azure' || p.includes('aks')) return 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30'
  return 'bg-purple-500/10 text-purple-400 border-purple-500/30'
})

const providerLabel = computed(() => {
  const p = props.context?.provider?.toUpperCase() || 'LOCAL'
  if (p === 'GCP') return 'GCP (GKE)'
  if (p === 'AWS') return 'AWS (EKS)'
  if (p === 'AZURE') return 'Azure (AKS)'
  return 'Local (Sandbox)'
})
</script>

<template>
  <div class="lp-glass overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/80 p-4 shadow-lg backdrop-blur-md">
    <div class="flex flex-wrap items-center justify-between gap-4">
      <!-- Connection Chip & Cluster Info -->
      <div class="flex flex-wrap items-center gap-3">
        <div class="flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium" :class="providerBadgeClass">
          <span class="material-symbols-outlined text-sm">cloud</span>
          <span>{{ providerLabel }}</span>
        </div>

        <div class="flex items-center gap-2 rounded-full border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 px-3.5 py-1 font-mono text-xs text-[var(--lp-text)]">
          <span class="relative flex h-2.5 w-2.5">
            <span v-if="context?.status === 'connected'" class="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span
              class="relative inline-flex h-2.5 w-2.5 rounded-full"
              :class="context?.status === 'connected' ? 'bg-emerald-500' : 'bg-amber-500'"
            />
          </span>
          <span class="font-semibold text-emerald-400">Connected:</span>
          <span class="text-[var(--lp-accent)] font-semibold">{{ context?.cluster_name || 'gke-lp-primary' }}</span>
          <span class="text-[var(--lp-muted)]">|</span>
          <span class="text-[var(--lp-muted)]">Context: {{ context?.region || 'us-central1-a' }}</span>
          <template v-if="context?.target_namespace">
            <span class="text-[var(--lp-muted)]">|</span>
            <span class="text-[var(--lp-muted)]">ns: {{ context.target_namespace }}</span>
          </template>
        </div>

        <div class="hidden items-center gap-3 font-mono text-xs text-[var(--lp-muted)] md:flex">
          <span class="flex items-center gap-1">
            <span class="material-symbols-outlined text-sm">view_in_ar</span>
            {{ context?.node_count || 3 }} nodes
          </span>
          <span>·</span>
          <span class="flex items-center gap-1 text-emerald-400">
            <span class="material-symbols-outlined text-sm">health_metrics</span>
            {{ context?.control_plane_health || 'Healthy (100%)' }}
          </span>
        </div>
      </div>

      <!-- Action Toolbar -->
      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="lp-btn-primary flex items-center gap-2 px-3.5 py-1.5 text-xs font-semibold uppercase tracking-wider shadow-md transition-all hover:scale-105 active:scale-95 disabled:opacity-50"
          :disabled="applying || loading"
          @click="emit('apply')"
        >
          <span
            class="material-symbols-outlined text-base"
            :class="applying ? 'animate-spin' : ''"
          >
            {{ applying ? 'sync' : 'rocket_launch' }}
          </span>
          {{ applying ? 'Applying Pipeline…' : 'Apply Manifests' }}
        </button>

        <button
          type="button"
          class="lp-btn-ghost flex items-center gap-1.5 px-3 py-1.5 text-xs uppercase tracking-wider text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
          :disabled="loading"
          @click="emit('refresh')"
        >
          <span class="material-symbols-outlined text-base" :class="loading ? 'animate-spin' : ''">refresh</span>
          Refresh State
        </button>

        <button
          type="button"
          class="flex items-center gap-1.5 rounded-lg border border-[var(--lp-danger)]/30 bg-[var(--lp-danger)]/10 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--lp-danger)] transition hover:bg-[var(--lp-danger)]/20 active:scale-95"
          @click="emit('deleteWorkspace')"
        >
          <span class="material-symbols-outlined text-base">delete_forever</span>
          Nuke Workspace
        </button>
      </div>
    </div>
  </div>
</template>

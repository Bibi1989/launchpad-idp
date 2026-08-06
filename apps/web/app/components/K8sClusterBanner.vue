<script setup lang="ts">
import type { K8sClusterContext } from '~/types/k8s'

const props = defineProps<{
  context: K8sClusterContext | null
  loading: boolean
  applying: boolean
}>()

const { t } = useI18n()

const emit = defineEmits<{
  (e: 'apply'): void
  (e: 'refresh'): void
  (e: 'deleteWorkspace'): void
}>()

const providerBadgeClass = computed(() => {
  const p = props.context?.provider?.toLowerCase() || 'local'
  if (p === 'gcp' || p.includes('gke')) {
    return 'border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/10 text-[var(--lp-accent)]'
  }
  if (p === 'aws' || p.includes('eks')) {
    return 'border-[var(--lp-warn)]/30 bg-[var(--lp-warn)]/10 text-[var(--lp-warn)]'
  }
  if (p === 'azure' || p.includes('aks')) {
    return 'border-[var(--lp-accent-dim)]/30 bg-[var(--lp-accent-dim)]/10 text-[var(--lp-accent-dim)]'
  }
  return 'border-[var(--lp-line)] bg-[var(--lp-panel-2)] text-[var(--lp-muted)]'
})

const providerLabel = computed(() => {
  const p = props.context?.provider?.toUpperCase() || 'LOCAL'
  if (p === 'GCP') return t('k8s.banner.providers.gcp')
  if (p === 'AWS') return t('k8s.banner.providers.aws')
  if (p === 'AZURE') return t('k8s.banner.providers.azure')
  return t('k8s.banner.providers.local')
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
            <span v-if="context?.status === 'connected'" class="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--lp-ok)] opacity-75" />
            <span
              class="relative inline-flex h-2.5 w-2.5 rounded-full"
              :class="context?.status === 'connected' ? 'bg-[var(--lp-ok)]' : 'bg-[var(--lp-warn)]'"
            />
          </span>
          <span class="font-semibold text-[var(--lp-ok)]">{{ t('k8s.banner.connected') }}</span>
          <span class="font-semibold text-[var(--lp-accent)]">{{ context?.cluster_name || 'gke-lp-primary' }}</span>
          <span class="text-[var(--lp-muted)]">|</span>
          <span class="text-[var(--lp-muted)]">{{ t('k8s.banner.context') }} {{ context?.region || 'us-central1-a' }}</span>
          <template v-if="context?.target_namespace">
            <span class="text-[var(--lp-muted)]">|</span>
            <span class="text-[var(--lp-muted)]">{{ t('k8s.banner.namespace') }} {{ context.target_namespace }}</span>
          </template>
        </div>

        <div class="hidden items-center gap-3 font-mono text-xs text-[var(--lp-muted)] md:flex">
          <span class="flex items-center gap-1">
            <span class="material-symbols-outlined text-sm">view_in_ar</span>
            {{ t('k8s.banner.nodes', { count: context?.node_count || 3 }) }}
          </span>
          <span>·</span>
          <span class="flex items-center gap-1 text-[var(--lp-ok)]">
            <span class="material-symbols-outlined text-sm">health_metrics</span>
            {{ context?.control_plane_health || t('k8s.banner.healthyDefault') }}
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
          {{ applying ? t('k8s.banner.applyingPipeline') : t('k8s.banner.applyManifests') }}
        </button>

        <button
          type="button"
          class="lp-btn-ghost flex items-center gap-1.5 px-3 py-1.5 text-xs uppercase tracking-wider text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
          :disabled="loading"
          @click="emit('refresh')"
        >
          <span class="material-symbols-outlined text-base" :class="loading ? 'animate-spin' : ''">refresh</span>
          {{ t('k8s.banner.refreshState') }}
        </button>

        <button
          type="button"
          class="flex items-center gap-1.5 rounded-lg border border-[var(--lp-danger)]/30 bg-[var(--lp-danger)]/10 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--lp-danger)] transition hover:bg-[var(--lp-danger)]/20 active:scale-95"
          @click="emit('deleteWorkspace')"
        >
          <span class="material-symbols-outlined text-base">delete_forever</span>
          {{ t('k8s.banner.nukeWorkspace') }}
        </button>
      </div>
    </div>
  </div>
</template>

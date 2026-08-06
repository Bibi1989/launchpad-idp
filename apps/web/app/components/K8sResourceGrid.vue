<script setup lang="ts">
import type { K8sResource } from '~/types/k8s'

const props = defineProps<{
  resources: K8sResource[]
  loading: boolean
}>()

const { t } = useI18n()

const emit = defineEmits<{
  (e: 'describe', resource: K8sResource): void
  (e: 'logs', resource: K8sResource): void
  (e: 'exec', resource: K8sResource): void
  (e: 'delete', resource: K8sResource): void
}>()

const activeTab = ref<'all' | 'deployments' | 'pods' | 'services' | 'ingress' | 'configmaps'>('all')

const filteredResources = computed(() => {
  if (activeTab.value === 'all') return props.resources
  if (activeTab.value === 'deployments')
    return props.resources.filter((r) => r.kind.toLowerCase().includes('deployment'))
  if (activeTab.value === 'pods')
    return props.resources.filter((r) => r.kind.toLowerCase() === 'pod')
  if (activeTab.value === 'services')
    return props.resources.filter((r) => r.kind.toLowerCase() === 'service')
  if (activeTab.value === 'ingress')
    return props.resources.filter((r) => r.kind.toLowerCase() === 'ingress')
  if (activeTab.value === 'configmaps')
    return props.resources.filter((r) => ['configmap', 'secret'].includes(r.kind.toLowerCase()))
  return props.resources
})

const deletingIds = ref<Set<string>>(new Set())

function onDelete(resource: K8sResource) {
  deletingIds.value.add(resource.id)
  emit('delete', resource)
}

function statusBadgeClass(status: string) {
  const s = status.toLowerCase()
  if (s.includes('run') || s.includes('active') || s.includes('ready'))
    return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
  if (s.includes('pend') || s.includes('init'))
    return 'bg-amber-500/10 text-amber-400 border-amber-500/30'
  if (s.includes('crash') || s.includes('error') || s.includes('failed'))
    return 'bg-rose-500/10 text-rose-400 border-rose-500/30'
  return 'bg-purple-500/10 text-purple-400 border-purple-500/30'
}
</script>

<template>
  <div class="space-y-4">
    <!-- Filter Tabs Header -->
    <div class="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--lp-line)] pb-3">
      <div class="flex flex-wrap items-center gap-1.5 font-mono text-xs">
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 transition"
          :class="activeTab === 'all' ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)] font-bold' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          @click="activeTab = 'all'"
        >
          {{ t('k8s.grid.allResources', { count: resources.length }) }}
        </button>
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 transition"
          :class="activeTab === 'deployments' ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)] font-bold' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          @click="activeTab = 'deployments'"
        >
          {{ t('k8s.grid.deployments') }}
        </button>
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 transition"
          :class="activeTab === 'pods' ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)] font-bold' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          @click="activeTab = 'pods'"
        >
          {{ t('k8s.grid.pods') }}
        </button>
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 transition"
          :class="activeTab === 'services' ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)] font-bold' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          @click="activeTab = 'services'"
        >
          {{ t('k8s.grid.services') }}
        </button>
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 transition"
          :class="activeTab === 'ingress' ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)] font-bold' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          @click="activeTab = 'ingress'"
        >
          {{ t('k8s.grid.ingress') }}
        </button>
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 transition"
          :class="activeTab === 'configmaps' ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)] font-bold' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          @click="activeTab = 'configmaps'"
        >
          {{ t('k8s.grid.configMaps') }}
        </button>
      </div>
    </div>

    <!-- Empty state -->
    <div
      v-if="!loading && filteredResources.length === 0"
      class="rounded-xl border border-dashed border-[var(--lp-line)] p-8 text-center font-mono text-xs text-[var(--lp-muted)]"
    >
      {{ t('k8s.grid.empty') }}
    </div>

    <!-- Loading Skeleton -->
    <div v-if="loading" class="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      <div v-for="n in 3" :key="n" class="h-44 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/40 animate-pulse p-4" />
    </div>

    <!-- Resource Cards Grid -->
    <div v-else class="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      <div
        v-for="res in filteredResources"
        :key="res.id"
        class="group relative flex flex-col justify-between rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/90 p-4 shadow-md transition-all duration-300 hover:border-[var(--lp-accent)]/50 hover:shadow-xl"
        :class="deletingIds.has(res.id) ? 'opacity-40 scale-95 transition-transform' : ''"
      >
        <div>
          <!-- Header (Kind, Name, Status Badge) -->
          <div class="flex items-start justify-between gap-2 border-b border-[var(--lp-line)]/50 pb-3">
            <div class="flex items-center gap-2">
              <span class="material-symbols-outlined text-lg text-[var(--lp-accent)]">
                {{
                  res.kind === 'Pod'
                    ? 'deployed_code'
                    : res.kind === 'Service'
                      ? 'alt_route'
                      : res.kind === 'Ingress'
                        ? 'public'
                        : 'widgets'
                }}
              </span>
              <div>
                <span class="font-mono text-[10px] font-bold uppercase tracking-widest text-[var(--lp-muted)]">
                  {{ res.kind }}
                </span>
                <h4 class="font-mono text-sm font-semibold text-[var(--lp-text)] truncate max-w-[180px]" :title="res.name">
                  {{ res.name }}
                </h4>
              </div>
            </div>
            <span class="rounded-full border px-2.5 py-0.5 font-mono text-[10px] uppercase font-bold" :class="statusBadgeClass(res.status)">
              {{ res.status }}
            </span>
          </div>

          <!-- Metadata List -->
          <div class="mt-3 space-y-1.5 font-mono text-xs text-[var(--lp-muted)]">
            <div class="flex justify-between">
              <span>{{ t('k8s.grid.namespace') }}</span>
              <span class="text-[var(--lp-text)] font-medium">{{ res.namespace }}</span>
            </div>
            <div class="flex justify-between">
              <span>{{ t('k8s.grid.readyReplicas') }}</span>
              <span class="text-[var(--lp-text)] font-medium">{{ res.ready_replicas }}</span>
            </div>
            <div v-if="res.ip" class="flex justify-between">
              <span>{{ t('k8s.grid.ipAddress') }}</span>
              <span class="text-[var(--lp-accent)] font-medium">{{ res.ip }}</span>
            </div>
            <div v-if="res.ports && res.ports.length" class="flex justify-between">
              <span>{{ t('k8s.grid.ports') }}</span>
              <span class="text-[var(--lp-text)] truncate max-w-[140px]">{{ res.ports.join(', ') }}</span>
            </div>
            <div v-if="res.endpoints && res.endpoints.length" class="mt-1">
              <span class="text-[10px] text-[var(--lp-muted)] uppercase">{{ t('k8s.grid.endpoint') }}</span>
              <p class="truncate text-[11px] text-emerald-400 font-semibold">
                {{ res.endpoints[0] }}
              </p>
            </div>
          </div>
        </div>

        <!-- Quick Action Buttons -->
        <div class="mt-4 flex items-center justify-between border-t border-[var(--lp-line)]/50 pt-3 font-mono text-xs">
          <div class="flex items-center gap-1">
            <!-- Describe -->
            <button
              type="button"
              class="flex items-center gap-1 rounded border border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-2 py-1 text-[11px] text-[var(--lp-text)] transition hover:border-[var(--lp-accent)] hover:text-[var(--lp-accent)]"
              :title="t('k8s.grid.describeTitle')"
              @click="emit('describe', res)"
            >
              <span class="material-symbols-outlined text-sm">description</span>
              {{ t('k8s.grid.describe') }}
            </button>

            <!-- Logs (for Pods/Deployments) -->
            <button
              v-if="['pod', 'deployment'].includes(res.kind.toLowerCase())"
              type="button"
              class="flex items-center gap-1 rounded border border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-2 py-1 text-[11px] text-[var(--lp-text)] transition hover:border-[var(--lp-accent)] hover:text-[var(--lp-accent)]"
              :title="t('k8s.grid.logsTitle')"
              @click="emit('logs', res)"
            >
              <span class="material-symbols-outlined text-sm">article</span>
              {{ t('common.logs') }}
            </button>

            <!-- Exec Terminal (for Pods) -->
            <button
              v-if="res.kind.toLowerCase() === 'pod'"
              type="button"
              class="flex items-center gap-1 rounded border border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-2 py-1 text-[11px] text-[var(--lp-accent)] transition hover:border-[var(--lp-accent)] hover:bg-[var(--lp-accent)]/10"
              :title="t('k8s.grid.execTitle')"
              @click="emit('exec', res)"
            >
              <span class="material-symbols-outlined text-sm">terminal</span>
              {{ t('k8s.grid.exec') }}
            </button>
          </div>

          <!-- Delete -->
          <button
            type="button"
            class="flex items-center gap-1 rounded px-2 py-1 text-[11px] text-[var(--lp-danger)] transition hover:bg-[var(--lp-danger)]/10"
            :title="t('k8s.grid.deleteTitle')"
            @click="onDelete(res)"
          >
            <span class="material-symbols-outlined text-sm">delete</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

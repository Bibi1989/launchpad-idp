<script setup lang="ts">
import type { K8sPipelineStage } from '~/types/k8s'

const props = defineProps<{
  stages: K8sPipelineStage[]
  active: boolean
  lastMessage?: string | null
}>()

const defaultStages: Array<{ id: K8sPipelineStage['stage_id']; label: string; icon: string }> = [
  { id: 'manifest_parsed', label: 'Manifest Parsed', icon: 'description' },
  { id: 'kube_api_accepted', label: 'Kube-API Accepted', icon: 'api' },
  { id: 'pods_provisioning', label: 'Pods Provisioning', icon: 'deployed_code' },
  { id: 'ingress_ready', label: 'Ingress / Public IP Ready', icon: 'public' },
]

function getStageState(stageId: string) {
  const current = props.stages.find((s) => s.stage_id === stageId)
  if (!current) return { status: 'pending', message: 'Awaiting pipeline trigger', details: null }
  return {
    status: current.status,
    message: current.message,
    details: current.details,
  }
}
</script>

<template>
  <div class="lp-glass relative overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/90 p-5 shadow-xl transition-all">
    <div class="mb-4 flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="material-symbols-outlined text-lg text-[var(--lp-accent)]">account_tree</span>
        <h3 class="font-mono text-xs font-semibold uppercase tracking-wider text-[var(--lp-text)]">
          Graphical Deployment Flow
        </h3>
      </div>
      <span v-if="active" class="flex items-center gap-1.5 font-mono text-[11px] text-[var(--lp-accent)] animate-pulse">
        <span class="h-2 w-2 rounded-full bg-[var(--lp-accent)]" />
        Pipeline Executing…
      </span>
    </div>

    <!-- Pipeline Stages Horizontal Graph -->
    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div
        v-for="(stage, idx) in defaultStages"
        :key="stage.id"
        class="relative flex flex-col justify-between rounded-lg border p-4 transition-all duration-300"
        :class="[
          getStageState(stage.id).status === 'success'
            ? 'border-emerald-500/40 bg-emerald-500/5 shadow-emerald-500/10'
            : getStageState(stage.id).status === 'running'
              ? 'border-[var(--lp-accent)]/60 bg-[var(--lp-accent)]/10 ring-2 ring-[var(--lp-accent)]/30 animate-pulse'
              : getStageState(stage.id).status === 'failed'
                ? 'border-rose-500/50 bg-rose-500/10'
                : 'border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 opacity-70',
        ]"
      >
        <!-- Connector Arrow for Large Screen -->
        <div
          v-if="idx < defaultStages.length - 1"
          class="hidden lg:block absolute -right-3.5 top-1/2 -translate-y-1/2 z-10 text-[var(--lp-muted)]"
        >
          <span class="material-symbols-outlined text-sm">arrow_forward</span>
        </div>

        <div class="flex items-start justify-between gap-2">
          <div class="flex items-center gap-2.5">
            <div
              class="flex h-8 w-8 items-center justify-center rounded-lg border font-mono text-sm shadow-inner"
              :class="[
                getStageState(stage.id).status === 'success'
                  ? 'border-emerald-500/40 bg-emerald-500/20 text-emerald-400'
                  : getStageState(stage.id).status === 'running'
                    ? 'border-[var(--lp-accent)]/40 bg-[var(--lp-accent)]/20 text-[var(--lp-accent)]'
                    : 'border-[var(--lp-line)] bg-[var(--lp-panel)] text-[var(--lp-muted)]',
              ]"
            >
              <span
                class="material-symbols-outlined text-base"
                :class="getStageState(stage.id).status === 'running' ? 'animate-spin' : ''"
              >
                {{
                  getStageState(stage.id).status === 'success'
                    ? 'check_circle'
                    : getStageState(stage.id).status === 'running'
                      ? 'sync'
                      : getStageState(stage.id).status === 'failed'
                        ? 'error'
                        : stage.icon
                }}
              </span>
            </div>
            <div>
              <p class="font-mono text-xs font-semibold text-[var(--lp-text)]">
                {{ stage.label }}
              </p>
              <p class="text-[10px] uppercase tracking-wider font-mono" :class="[
                getStageState(stage.id).status === 'success' ? 'text-emerald-400' :
                getStageState(stage.id).status === 'running' ? 'text-[var(--lp-accent)] font-bold' :
                getStageState(stage.id).status === 'failed' ? 'text-rose-400' : 'text-[var(--lp-muted)]'
              ]">
                {{ getStageState(stage.id).status }}
              </p>
            </div>
          </div>
        </div>

        <p class="mt-3 text-[11px] text-[var(--lp-muted)] line-clamp-2 leading-relaxed font-mono">
          {{ getStageState(stage.id).message }}
        </p>

        <!-- Ingress details preview if ready -->
        <div
          v-if="stage.id === 'ingress_ready' && getStageState('ingress_ready').details?.ingress_url"
          class="mt-2.5 rounded bg-emerald-500/10 border border-emerald-500/20 px-2 py-1 text-[11px] font-mono text-emerald-300 truncate"
        >
          <a
            :href="getStageState('ingress_ready').details.ingress_url"
            target="_blank"
            class="hover:underline flex items-center gap-1"
          >
            <span class="material-symbols-outlined text-xs">open_in_new</span>
            {{ getStageState('ingress_ready').details.ingress_url }}
          </a>
        </div>
      </div>
    </div>
  </div>
</template>

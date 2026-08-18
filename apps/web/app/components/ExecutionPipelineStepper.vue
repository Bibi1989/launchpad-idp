<script setup lang="ts">
import type { EnvironmentStatus } from '~/types/environment'

const props = defineProps<{
  status?: EnvironmentStatus | string | null
  currentStage?: string | null
  message?: string | null
  errorMessage?: string | null
  commitSha?: string | null
  isLocal?: boolean
}>()

const { t } = useI18n()

type StageKey = 'INIT' | 'VALIDATE' | 'PLAN' | 'BUILD' | 'APPLY'

interface PipelineStep {
  key: StageKey
  label: string
  description: string
  icon: string
}

const STEPS: PipelineStep[] = [
  {
    key: 'INIT',
    label: 'Initialization',
    description: 'Preparing environment context & git repository',
    icon: 'power_settings_new',
  },
  {
    key: 'VALIDATE',
    label: 'Validation',
    description: 'Checking cluster reachability & deployment spec',
    icon: 'fact_check',
  },
  {
    key: 'PLAN',
    label: 'Plan',
    description: 'Resolving container image & resource manifests',
    icon: 'account_tree',
  },
  {
    key: 'BUILD',
    label: 'Image Build',
    description: 'Building container image from application source',
    icon: 'build_circle',
  },
  {
    key: 'APPLY',
    label: 'Deploy & Apply',
    description: 'Applying manifests & waiting for pods ready',
    icon: 'rocket_launch',
  },
]

const stageOrder: StageKey[] = ['INIT', 'VALIDATE', 'PLAN', 'BUILD', 'APPLY']

function getStepState(key: StageKey): 'completed' | 'active' | 'failed' | 'pending' {
  const currentKey = (props.currentStage as StageKey) || 'INIT'
  const currentIndex = stageOrder.indexOf(currentKey)
  const stepIndex = stageOrder.indexOf(key)

  if (props.status === 'FAILED') {
    if (stepIndex === currentIndex) return 'failed'
    if (stepIndex < currentIndex) return 'completed'
    return 'pending'
  }

  if (props.status === 'RUNNING') {
    return 'completed'
  }

  if (stepIndex < currentIndex) return 'completed'
  if (stepIndex === currentIndex) return 'active'
  return 'pending'
}
</script>

<template>  
  <section class="lp-glass overflow-hidden rounded-xl border border-[var(--lp-line)] p-5">
    <div class="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--lp-line)]/60 pb-4">
      <div class="flex items-center gap-2.5">
        <span class="material-symbols-outlined text-lg text-[var(--lp-accent)]">account_tree</span>
        <h3 class="text-base font-semibold tracking-tight">Execution Pipeline</h3>
        <span
          v-if="status === 'PROVISIONING'"
          class="inline-flex items-center gap-1.5 rounded-full bg-teal-500/10 px-2.5 py-0.5 text-xs font-medium text-teal-400 border border-teal-500/30 animate-pulse"
        >
          <span class="h-1.5 w-1.5 rounded-full bg-teal-400" />
          Live Executing
        </span>
        <span
          v-else-if="status === 'RUNNING'"
          class="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-xs font-medium text-emerald-400 border border-emerald-500/30"
        >
          <span class="material-symbols-outlined text-xs">check_circle</span>
          Pipeline Complete
        </span>
        <span
          v-else-if="status === 'FAILED'"
          class="inline-flex items-center gap-1.5 rounded-full bg-red-500/10 px-2.5 py-0.5 text-xs font-medium text-red-400 border border-red-500/30"
        >
          <span class="material-symbols-outlined text-xs">error</span>
          Pipeline Failed
        </span>
      </div>

      <div v-if="commitSha" class="font-mono text-xs text-[var(--lp-muted)]">
        Commit: <span class="text-[var(--lp-text)] font-semibold">{{ commitSha.slice(0, 7) }}</span>
      </div>
    </div>

    <!-- Stepper Grid -->
    <div class="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-5">
      <div
        v-for="(step, idx) in STEPS"
        :key="step.key"
        class="relative flex flex-col rounded-lg border p-3.5 transition-all duration-300"
        :class="{
          'border-emerald-500/40 bg-emerald-500/5 text-emerald-300': getStepState(step.key) === 'completed',
          'border-teal-400/60 bg-teal-500/10 text-teal-200 ring-2 ring-teal-500/30 shadow-lg shadow-teal-500/10': getStepState(step.key) === 'active',
          'border-red-500/40 bg-red-500/10 text-red-300': getStepState(step.key) === 'failed',
          'border-[var(--lp-line)] bg-[var(--lp-panel-2)]/20 text-[var(--lp-muted)] opacity-60': getStepState(step.key) === 'pending',
        }"
      >
        <div class="flex items-center justify-between gap-2">
          <span class="font-mono text-[11px] font-bold uppercase tracking-wider opacity-75">
            0{{ idx + 1 }}. {{ step.key }}
          </span>

          <!-- Status indicator icon -->
          <template v-if="getStepState(step.key) === 'completed'">
            <span class="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-400">
              <span class="material-symbols-outlined text-xs">check</span>
            </span>
          </template>
          <template v-else-if="getStepState(step.key) === 'active'">
            <span class="flex h-5 w-5 items-center justify-center rounded-full bg-teal-400/20 text-teal-300 animate-spin">
              <span class="material-symbols-outlined text-xs">sync</span>
            </span>
          </template>
          <template v-else-if="getStepState(step.key) === 'failed'">
            <span class="flex h-5 w-5 items-center justify-center rounded-full bg-red-500/20 text-red-400">
              <span class="material-symbols-outlined text-xs">close</span>
            </span>
          </template>
          <template v-else>
            <span class="h-2 w-2 rounded-full bg-[var(--lp-line)]" />
          </template>
        </div>

        <div class="mt-2 flex items-center gap-2">
          <span class="material-symbols-outlined text-base">{{ step.icon }}</span>
          <span class="text-sm font-semibold leading-tight text-[var(--lp-text)]">{{ step.label }}</span>
        </div>

        <p class="mt-1 text-[11px] leading-snug text-[var(--lp-muted)]">
          {{ step.description }}
        </p>

        <!-- Progress line indicator for active stage -->
        <div
          v-if="getStepState(step.key) === 'active'"
          class="mt-3 h-1 w-full overflow-hidden rounded-full bg-teal-950"
        >
          <div class="h-full bg-gradient-to-r from-teal-500 to-cyan-400 animate-pulse w-full" />
        </div>
      </div>
    </div>

    <!-- Active/Latest Execution Message Banner -->
    <div
      v-if="message || errorMessage"
      class="mt-4 flex items-start gap-3 rounded-lg border px-4 py-3 text-xs font-mono"
      :class="status === 'FAILED' ? 'border-red-500/30 bg-red-500/10 text-red-200' : 'border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 text-[var(--lp-text)]'"
    >
      <span class="material-symbols-outlined text-sm mt-0.5 shrink-0" :class="status === 'FAILED' ? 'text-red-400' : 'text-teal-400'">
        {{ status === 'FAILED' ? 'error' : 'info' }}
      </span>
      <div class="min-w-0 flex-1 leading-relaxed">
        <p v-if="errorMessage" class="text-red-300 font-semibold mb-0.5">{{ errorMessage }}</p>
        <p v-if="message" class="text-[var(--lp-text)] opacity-90">{{ message }}</p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { CostOptimizationConfig, ResourceSizingPreset } from '~/types/provisioning'
import {
  applyResourcePreset,
  defaultCostOptimizationConfig,
} from '~/utils/costOptimization'

const cost = defineModel<CostOptimizationConfig>('cost', { required: true })

const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    disabled?: boolean
  }>(),
  { disabled: false },
)

const presets = computed(() => [
  { value: 'developer' as ResourceSizingPreset, title: t('scaffold.cost.presets.developer'), hint: '100m CPU / 128Mi RAM' },
  { value: 'balanced' as ResourceSizingPreset, title: t('scaffold.cost.presets.balanced'), hint: '250m CPU / 512Mi RAM' },
  { value: 'performance' as ResourceSizingPreset, title: t('scaffold.cost.presets.performance'), hint: '1 Core / 2Gi RAM' },
  { value: 'custom' as ResourceSizingPreset, title: t('scaffold.cost.presets.custom'), hint: 'Override requests & limits' },
])

function ensureCost(): CostOptimizationConfig {
  return cost.value ?? defaultCostOptimizationConfig()
}

function patch(partial: Partial<CostOptimizationConfig>) {
  cost.value = { ...ensureCost(), ...partial }
}

function onPresetChange(preset: ResourceSizingPreset) {
  const next = ensureCost()
  patch({
    resources: applyResourcePreset(preset, next.resources),
  })
}
</script>

<template>
  <section class="space-y-4 rounded-xl border border-[var(--lp-line)] p-4">
    <div>
      <p class="lp-label">{{ t('scaffold.cost.label') }}</p>
      <h3 class="mt-1 text-lg font-semibold">{{ t('scaffold.cost.title') }}</h3>
      <p class="mt-1 text-sm text-[var(--lp-muted)]">
        {{ t('scaffold.cost.blurb') }}
      </p>
    </div>

    <!-- A. Spot -->
    <div class="space-y-3 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/30 p-4">
      <label class="flex cursor-pointer items-start gap-3">
        <input
          v-model="cost.spotScheduling.enabled"
          type="checkbox"
          class="mt-0.5 h-4 w-4 accent-[var(--lp-accent)]"
          :disabled="disabled"
        >
        <span>
          <span class="block text-sm font-medium">{{ t('scaffold.cost.spot') }}</span>
          <span class="block text-xs text-[var(--lp-muted)]">
            {{ t('scaffold.cost.spotBlurb') }}
          </span>
        </span>
      </label>

      <div v-if="cost.spotScheduling.enabled" class="space-y-3 border-t border-[var(--lp-line)] pt-3 pl-7">
        <label class="block space-y-2">
          <span class="lp-label">{{ t('scaffold.cost.spotPlacement') }}</span>
          <select v-model="cost.spotScheduling.placement" class="lp-input" :disabled="disabled">
            <option value="stateless_nonprod">Stateless / Non-prod</option>
            <option value="production_ondemand_fallback">Production with On-Demand Fallback</option>
          </select>
        </label>
        <label class="block space-y-2">
          <span class="lp-label">
            {{ t('scaffold.cost.spotAllocation', { percent: cost.spotScheduling.allocationPercent }) }}
          </span>
          <input
            v-model.number="cost.spotScheduling.allocationPercent"
            type="range"
            min="0"
            max="100"
            step="5"
            class="w-full accent-[var(--lp-accent)]"
            :disabled="disabled"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('scaffold.cost.spotProvisioner') }}</span>
          <select v-model="cost.spotScheduling.provisioner" class="lp-input" :disabled="disabled">
            <option value="karpenter">Karpenter / Dynamic Provisioner</option>
            <option value="cluster_autoscaler">Standard Cluster Autoscaler</option>
          </select>
        </label>
      </div>
    </div>

    <!-- B. Autoscaling -->
    <div class="space-y-3 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/30 p-4">
      <label class="flex cursor-pointer items-start gap-3">
        <input
          v-model="cost.hpa.enabled"
          type="checkbox"
          class="mt-0.5 h-4 w-4 accent-[var(--lp-accent)]"
          :disabled="disabled"
        >
        <span>
          <span class="block text-sm font-medium">{{ t('scaffold.cost.hpa') }}</span>
          <span class="block text-xs text-[var(--lp-muted)]">
            {{ t('scaffold.cost.hpaBlurb') }}
          </span>
        </span>
      </label>
      <div v-if="cost.hpa.enabled" class="grid gap-3 border-t border-[var(--lp-line)] pt-3 pl-7 sm:grid-cols-3">
        <label class="block space-y-2">
          <span class="lp-label">Min replicas</span>
          <input v-model.number="cost.hpa.minReplicas" type="number" min="1" max="100" class="lp-input" :disabled="disabled">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Max replicas</span>
          <input v-model.number="cost.hpa.maxReplicas" type="number" min="1" max="200" class="lp-input" :disabled="disabled">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Target CPU %</span>
          <input v-model.number="cost.hpa.targetCpuUtilization" type="number" min="1" max="100" class="lp-input" :disabled="disabled">
        </label>
      </div>

      <label class="flex cursor-pointer items-start gap-3 border-t border-[var(--lp-line)] pt-3">
        <input
          v-model="cost.vpa.enabled"
          type="checkbox"
          class="mt-0.5 h-4 w-4 accent-[var(--lp-accent)]"
          :disabled="disabled"
        >
        <span>
          <span class="block text-sm font-medium">{{ t('scaffold.cost.vpa') }}</span>
          <span class="block text-xs text-[var(--lp-muted)]">
            {{ t('scaffold.cost.vpaBlurb') }}
          </span>
        </span>
      </label>
    </div>

    <!-- C. Resources -->
    <div class="space-y-3 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/30 p-4">
      <p class="text-sm font-medium">{{ t('scaffold.cost.resources') }}</p>
      <div class="grid gap-2 sm:grid-cols-2">
        <label
          v-for="preset in presets"
          :key="preset.value"
          class="flex cursor-pointer flex-col rounded-lg border p-3 transition"
          :class="
            cost.resources.preset === preset.value
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
              : 'border-[var(--lp-line)] hover:bg-[var(--lp-panel)]'
          "
        >
          <input
            type="radio"
            class="sr-only"
            name="cost_resource_preset"
            :value="preset.value"
            :checked="cost.resources.preset === preset.value"
            :disabled="disabled"
            @change="onPresetChange(preset.value)"
          >
          <span class="text-sm font-medium">{{ preset.title }}</span>
          <span class="mt-0.5 font-mono text-[11px] text-[var(--lp-muted)]">{{ preset.hint }}</span>
        </label>
      </div>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="block space-y-2">
          <span class="lp-label">CPU request</span>
          <input
            v-model="cost.resources.cpuRequest"
            type="text"
            class="lp-input font-mono text-sm"
            :disabled="disabled || cost.resources.preset !== 'custom'"
            @input="cost.resources.preset = 'custom'"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">CPU limit</span>
          <input
            v-model="cost.resources.cpuLimit"
            type="text"
            class="lp-input font-mono text-sm"
            :disabled="disabled || cost.resources.preset !== 'custom'"
            @input="cost.resources.preset = 'custom'"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Memory request</span>
          <input
            v-model="cost.resources.memoryRequest"
            type="text"
            class="lp-input font-mono text-sm"
            :disabled="disabled || cost.resources.preset !== 'custom'"
            @input="cost.resources.preset = 'custom'"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Memory limit</span>
          <input
            v-model="cost.resources.memoryLimit"
            type="text"
            class="lp-input font-mono text-sm"
            :disabled="disabled || cost.resources.preset !== 'custom'"
            @input="cost.resources.preset = 'custom'"
          >
        </label>
      </div>
    </div>

    <!-- D. Idle shutdown -->
    <div class="space-y-3 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/30 p-4">
      <label class="flex cursor-pointer items-start gap-3">
        <input
          v-model="cost.idleShutdown.enabled"
          type="checkbox"
          class="mt-0.5 h-4 w-4 accent-[var(--lp-accent)]"
          :disabled="disabled"
        >
        <span>
          <span class="block text-sm font-medium">{{ t('scaffold.cost.idleShutdown') }}</span>
          <span class="block text-xs text-[var(--lp-muted)]">
            {{ t('scaffold.cost.idleBlurb') }}
          </span>
        </span>
      </label>
      <div v-if="cost.idleShutdown.enabled" class="border-t border-[var(--lp-line)] pt-3 pl-7">
        <label class="block space-y-2">
          <span class="lp-label">{{ t('scaffold.cost.schedule') }}</span>
          <select v-model="cost.idleShutdown.schedule" class="lp-input" :disabled="disabled">
            <option value="weeknights_weekends">Mon-Fri 7PM-7AM + weekends</option>
          </select>
        </label>
      </div>
    </div>
  </section>
</template>

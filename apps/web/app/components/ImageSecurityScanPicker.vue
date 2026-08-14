<script setup lang="ts">
import type { ContainerScanToolId, ImageSecurityScanConfig } from '~/types/provisioning'
import { CONTAINER_SCAN_TOOL_OPTIONS } from '~/utils/cicdSecurityTools'

const scan = defineModel<ImageSecurityScanConfig>('scan', { required: true })
const { t } = useI18n()

const trivyTools = CONTAINER_SCAN_TOOL_OPTIONS.filter((opt) => Boolean(opt.image))

function setEnabled(enabled: boolean) {
  scan.value = { ...scan.value, enabled }
}

function setTool(tool: ContainerScanToolId) {
  scan.value = { ...scan.value, tool }
}
</script>

<template>
  <div class="space-y-3 rounded-lg border border-[var(--lp-line)] p-3">
    <label class="flex items-start gap-3">
      <input
        :checked="scan.enabled"
        type="checkbox"
        class="mt-1 h-4 w-4 accent-[var(--lp-accent)]"
        @change="setEnabled(($event.target as HTMLInputElement).checked)"
      >
      <span>
        <span class="block text-sm font-medium text-[var(--lp-text)]">
          {{ t('launch.imageScan.title') }}
        </span>
        <span class="mt-0.5 block text-xs text-[var(--lp-muted)]">
          {{ t('launch.imageScan.blurb') }}
        </span>
      </span>
    </label>
    <div
      v-if="scan.enabled"
      class="grid gap-3 border-t border-[var(--lp-line)] pt-3 sm:grid-cols-3"
    >
      <label class="block space-y-1.5">
        <span class="lp-label">{{ t('scaffold.infra.pipelineSecurity.scanner') }}</span>
        <select
          :value="scan.tool"
          class="lp-input font-mono text-xs"
          @change="setTool(($event.target as HTMLSelectElement).value as ContainerScanToolId)"
        >
          <option
            v-for="opt in trivyTools"
            :key="opt.id"
            :value="opt.id"
          >
            {{ opt.label }}
          </option>
        </select>
      </label>
      <label class="block space-y-1.5">
        <span class="lp-label">{{ t('scaffold.infra.pipelineSecurity.severity') }}</span>
        <select v-model="scan.severity_threshold" class="lp-input">
          <option value="critical">
            {{ t('scaffold.infra.pipelineSecurity.criticalOnly') }}
          </option>
          <option value="critical_high">
            {{ t('scaffold.infra.pipelineSecurity.criticalHigh') }}
          </option>
        </select>
      </label>
      <label class="block space-y-1.5">
        <span class="lp-label">{{ t('scaffold.infra.pipelineSecurity.onFinding') }}</span>
        <select v-model="scan.on_finding" class="lp-input">
          <option value="block">
            {{ t('scaffold.infra.pipelineSecurity.blockDeploy') }}
          </option>
          <option value="warn">
            {{ t('scaffold.infra.pipelineSecurity.warnUpload') }}
          </option>
        </select>
      </label>
    </div>
  </div>
</template>

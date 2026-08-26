<script setup lang="ts">
import type { ProvisionEngine } from '~/types/provisioning'

const props = withDefaults(
  defineProps<{
    engine: ProvisionEngine
    provider?: string
    status?: string | null
    busy?: boolean
    terminalReady?: boolean
    /** Show a dedicated Run Ansible CTA (infra/ansible present alongside TF/Pulumi). */
    hasAnsible?: boolean
  }>(),
  {
    hasAnsible: false,
  },
)

const { t } = useI18n()

const emit = defineEmits<{
  openProvision: []
  openDestroy: []
  openAnsible: []
  openTerminal: []
}>()

const engineLabel = computed(() => t(`workspaceIde.engines.${props.engine}`))
const isAnsibleEngine = computed(() => props.engine === 'ansible')
const showAnsibleCta = computed(
  () => props.hasAnsible || isAnsibleEngine.value,
)
const cloudLabel = computed(() => (props.provider || 'local').toUpperCase())

const provisionLabel = computed(() =>
  isAnsibleEngine.value
    ? t('workspaceIde.toolbar.runAnsible')
    : t('workspaceIde.toolbar.provisionStack'),
)
const destroyLabel = computed(() => t('common.destroy'))
const ansibleLabel = computed(() =>
  t('workspaceIde.toolbar.runAnsibleForCloud', { cloud: cloudLabel.value }),
)

const provisionDescription = computed(() =>
  isAnsibleEngine.value
    ? t('workspaceIde.toolbar.runAnsibleDescription', { cloud: cloudLabel.value })
    : t('workspaceIde.toolbar.provisionDescription', { engine: engineLabel.value }),
)
const destroyDescription = computed(() =>
  isAnsibleEngine.value
    ? t('workspaceIde.toolbar.ansibleDestroyHint')
    : t('workspaceIde.toolbar.destroyDescription', { engine: engineLabel.value }),
)
const ansibleDescription = computed(() =>
  t('workspaceIde.toolbar.runAnsibleDescription', { cloud: cloudLabel.value }),
)

const blurb = computed(() =>
  isAnsibleEngine.value
    ? t('workspaceIde.toolbar.ansibleBlurb')
    : showAnsibleCta.value
      ? t('workspaceIde.toolbar.blurbWithAnsible')
      : t('workspaceIde.toolbar.blurb'),
)

function ensureTerminalThen(emitName: 'openProvision' | 'openDestroy' | 'openAnsible') {
  if (props.busy) return
  if (!props.terminalReady) emit('openTerminal')
  if (emitName === 'openProvision') emit('openProvision')
  else if (emitName === 'openDestroy') emit('openDestroy')
  else emit('openAnsible')
}

function onProvision() {
  if (isAnsibleEngine.value) {
    ensureTerminalThen('openAnsible')
    return
  }
  ensureTerminalThen('openProvision')
}

function onDestroy() {
  if (isAnsibleEngine.value) return
  ensureTerminalThen('openDestroy')
}

function onAnsible() {
  ensureTerminalThen('openAnsible')
}
</script>

<template>
  <div
    class="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/50 px-4 py-3"
  >
    <div class="min-w-0 space-y-0.5">
      <div class="flex flex-wrap items-center gap-2">
        <p class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
          {{ t('workspaceIde.toolbar.iacLabel') }} · {{ engineLabel }}
          <span v-if="showAnsibleCta && !isAnsibleEngine"> · {{ t('workspaceIde.engines.ansible') }}</span>
        </p>
        <span
          v-if="status"
          class="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide"
          :class="
            status === 'provisioned' || status === 'ready' || status === 'active' || status === 'ok'
              ? 'border-[var(--lp-ok)]/40 bg-[var(--lp-ok)]/10 text-[var(--lp-ok)]'
              : status === 'error' || status === 'failed'
                ? 'border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/10 text-[var(--lp-danger)]'
                : 'border-[var(--lp-line)] bg-[var(--lp-panel)] text-[var(--lp-muted)]'
          "
        >
          <span class="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
          {{ provider ? `${provider.toUpperCase()} · ${status}` : status }}
        </span>
      </div>
      <p class="text-xs text-[var(--lp-muted)]">
        {{ blurb }}
        <span v-if="!terminalReady" class="text-[var(--lp-warn)]">{{ t('workspaceIde.toolbar.opensSandbox') }}</span>
      </p>
    </div>
    <div class="flex flex-wrap items-center gap-1.5">
      <button
        v-if="!isAnsibleEngine"
        type="button"
        class="inline-flex items-center gap-1 rounded-lg border border-[var(--lp-accent)]/50 bg-[var(--lp-accent)]/10 px-2.5 py-1.5 font-mono text-[11px] uppercase tracking-wide text-[var(--lp-accent)] transition hover:bg-[var(--lp-accent)]/20 disabled:opacity-50"
        :disabled="busy"
        :title="provisionDescription"
        @click="onProvision"
      >
        <span class="material-symbols-outlined text-sm text-[var(--lp-accent)]">rocket_launch</span>
        {{ provisionLabel }}
      </button>
      <button
        v-if="showAnsibleCta"
        type="button"
        class="inline-flex items-center gap-1 rounded-lg border border-[var(--lp-accent)]/50 bg-[var(--lp-accent)]/10 px-2.5 py-1.5 font-mono text-[11px] uppercase tracking-wide text-[var(--lp-accent)] transition hover:bg-[var(--lp-accent)]/20 disabled:opacity-50"
        :disabled="busy"
        :title="ansibleDescription"
        @click="onAnsible"
      >
        <span class="material-symbols-outlined text-sm text-[var(--lp-accent)]">terminal</span>
        {{ ansibleLabel }}
      </button>
      <button
        v-if="!isAnsibleEngine"
        type="button"
        class="inline-flex items-center gap-1 rounded-lg border border-[var(--lp-danger)]/40 px-2.5 py-1.5 font-mono text-[11px] uppercase tracking-wide text-[var(--lp-danger)] transition hover:bg-[var(--lp-danger)]/10 disabled:opacity-50"
        :disabled="busy"
        :title="destroyDescription"
        @click="onDestroy"
      >
        <span class="material-symbols-outlined text-sm text-[var(--lp-danger)]">dangerous</span>
        {{ destroyLabel }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import type {
  CloudProvider,
  RunningInstanceConfig,
  WorkspaceRuntimeMode,
} from '~/types/provisioning'
import {
  hasServerlessRuntime,
  runtimeModesForProvider,
} from '~/utils/workspaceRuntimeMode'

const mode = defineModel<WorkspaceRuntimeMode>('mode', { required: true })
const runningInstance = defineModel<RunningInstanceConfig>('runningInstance', { required: true })

const props = defineProps<{
  provider: CloudProvider
  resources?: Record<string, unknown>
  disabled?: boolean
}>()

const { t } = useI18n()

const availableModes = computed(() => runtimeModesForProvider(props.provider))

const showAttachFields = computed(() => mode.value === 'running_instance')

const serverlessAvailable = computed(() =>
  hasServerlessRuntime(props.provider, props.resources ?? {}),
)

watch(
  () => props.provider,
  (provider) => {
    if (!availableModes.value.includes(mode.value)) {
      mode.value = availableModes.value[0] ?? 'kubernetes'
    }
    if (provider !== 'local' && mode.value === 'docker_compose') {
      mode.value = 'kubernetes'
    }
  },
)

watch(
  serverlessAvailable,
  (available) => {
    if (mode.value !== 'running_instance') return
    if (available && runningInstance.value.kind === 'kube_context') {
      runningInstance.value = { ...runningInstance.value, kind: 'serverless' }
    }
  },
)
</script>

<template>
  <div class="space-y-4">
    <div>
      <p class="lp-label">{{ t('provision.runtimeMode.title') }}</p>
      <p class="mt-1 text-xs text-[var(--lp-muted)]">
        {{ t('provision.runtimeMode.blurb') }}
      </p>
    </div>

    <div class="grid gap-3 sm:grid-cols-3">
      <button
        v-for="id in availableModes"
        :key="id"
        type="button"
        class="rounded-xl border p-4 text-left transition active:scale-[0.98]"
        :class="
          mode === id
            ? 'border-2 border-[var(--lp-accent)] bg-[var(--lp-panel-2)]'
            : 'border-[var(--lp-line)] bg-[var(--lp-panel)]/60 hover:border-[var(--lp-accent)]/50'
        "
        :disabled="disabled"
        @click="mode = id"
      >
        <span
          class="material-symbols-outlined text-2xl"
          :class="mode === id ? 'text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
        >
          {{
            id === 'kubernetes'
              ? 'deployed_code'
              : id === 'docker_compose'
                ? 'dock'
                : 'link'
          }}
        </span>
        <p class="mt-2 text-sm font-medium text-[var(--lp-text)]">
          {{ t(`provision.runtimeMode.modes.${id}.title`) }}
        </p>
        <p class="mt-1 text-xs text-[var(--lp-muted)]">
          {{ t(`provision.runtimeMode.modes.${id}.desc`) }}
        </p>
      </button>
    </div>

    <div
      v-if="showAttachFields"
      class="space-y-3 rounded-xl border border-[var(--lp-line)] p-4"
    >
      <p class="text-sm font-medium text-[var(--lp-text)]">
        {{ t('provision.runtimeMode.attach.title') }}
      </p>
      <p v-if="serverlessAvailable" class="text-xs text-[var(--lp-muted)]">
        {{ t('provision.runtimeMode.attach.serverlessHint') }}
      </p>
      <template v-else>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('provision.runtimeMode.attach.kind') }}</span>
          <select
            v-model="runningInstance.kind"
            class="lp-input"
            :disabled="disabled"
          >
            <option value="kube_context">
              {{ t('provision.runtimeMode.attach.kinds.kube_context') }}
            </option>
            <option value="endpoint">
              {{ t('provision.runtimeMode.attach.kinds.endpoint') }}
            </option>
          </select>
        </label>
        <label
          v-if="runningInstance.kind === 'kube_context'"
          class="block space-y-2"
        >
          <span class="lp-label">{{ t('provision.fields.kubectlContext') }}</span>
          <input
            v-model="runningInstance.kube_context"
            class="lp-input"
            placeholder="kind-launchpad"
            :disabled="disabled"
          >
        </label>
        <label
          v-else
          class="block space-y-2"
        >
          <span class="lp-label">{{ t('provision.runtimeMode.attach.endpointUrl') }}</span>
          <input
            v-model="runningInstance.endpoint_url"
            class="lp-input"
            placeholder="https://app.example.com"
            :disabled="disabled"
          >
        </label>
      </template>
    </div>
  </div>
</template>

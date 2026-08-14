<script setup lang="ts">
import type {
  CloudProvider,
  RunningInstanceConfig,
  WorkspaceRuntimeMode,
} from '~/types/provisioning'
import {
  applyInstanceComputeTarget,
  computeTargetDisplay,
  instanceComputeTargetsForProvider,
  resolveProcessStrategy,
  resolveSelectedInstanceComputeTarget,
} from '~/utils/instanceComputeTargets'
import { runtimeModesForProvider } from '~/utils/workspaceRuntimeMode'

const mode = defineModel<WorkspaceRuntimeMode>('mode', { required: true })
const runningInstance = defineModel<RunningInstanceConfig>('runningInstance', { required: true })

const props = defineProps<{
  provider: CloudProvider
  /** Mutable provider resources (toggles sync when a compute target is chosen). */
  resources?: Record<string, unknown>
  disabled?: boolean
}>()

const { t } = useI18n()

const availableModes = computed(() => runtimeModesForProvider(props.provider))
const showInstanceFields = computed(() => mode.value === 'running_instance')
const computeTargets = computed(() => instanceComputeTargetsForProvider(props.provider))
const selectedTargetId = computed(
  () =>
    resolveSelectedInstanceComputeTarget(
      props.provider,
      runningInstance.value,
      props.resources ?? {},
    )?.id ?? null,
)

const activeStrategy = computed(() => resolveProcessStrategy(runningInstance.value))

function targetCard(target: (typeof computeTargets.value)[number]) {
  return computeTargetDisplay(target, activeStrategy.value)
}

function ensureResources(): Record<string, unknown> {
  return props.resources ?? {}
}

function selectComputeTarget(targetId: string) {
  runningInstance.value = applyInstanceComputeTarget({
    provider: props.provider,
    targetId,
    runningInstance: runningInstance.value,
    resources: ensureResources(),
  })
}

function syncDefaultComputeTarget() {
  if (mode.value !== 'running_instance') return
  const targets = computeTargets.value
  if (!targets.length) return
  const current = resolveSelectedInstanceComputeTarget(
    props.provider,
    runningInstance.value,
    props.resources ?? {},
  )
  const preferred = current?.id ?? targets[0]?.id
  if (preferred) selectComputeTarget(preferred)
}

watch(
  () => props.provider,
  () => {
    if (!availableModes.value.includes(mode.value)) {
      mode.value = availableModes.value[0] ?? 'kubernetes'
    }
    if (props.provider !== 'local' && mode.value === 'docker_compose') {
      mode.value = 'kubernetes'
    }
    syncDefaultComputeTarget()
  },
)

watch(mode, (next) => {
  if (next === 'running_instance') syncDefaultComputeTarget()
})
watch(
  () => runningInstance.value.kind,
  (kind) => {
    if (kind === 'serverless') {
      if (
        runningInstance.value.process_strategy === 'docker'
        && (runningInstance.value.reverse_proxy || 'none') === 'none'
      ) {
        return
      }
      runningInstance.value = {
        ...runningInstance.value,
        process_strategy: 'docker',
        reverse_proxy: 'none',
      }
      return
    }
    const process_strategy = runningInstance.value.process_strategy || 'docker'
    const reverse_proxy = runningInstance.value.reverse_proxy || 'none'
    if (
      runningInstance.value.process_strategy === process_strategy
      && (runningInstance.value.reverse_proxy || 'none') === reverse_proxy
    ) {
      return
    }
    runningInstance.value = {
      ...runningInstance.value,
      process_strategy,
      reverse_proxy,
    }
  },
  { immediate: true },
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
                : 'dns'
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
      v-if="showInstanceFields"
      class="space-y-3 rounded-xl border border-[var(--lp-line)] p-4"
    >
      <p class="text-sm font-medium text-[var(--lp-text)]">
        {{ t('provision.runtimeMode.attach.title') }}
      </p>
      <p class="text-xs text-[var(--lp-muted)]">
        {{ t('provision.runtimeMode.attach.blurb') }}
      </p>
      <p
        v-if="provider === 'cloudflare'"
        class="text-xs text-[var(--lp-muted)]"
      >
        {{ t('provision.runtimeMode.attach.cloudflareNote') }}
      </p>
      <p class="text-xs text-[var(--lp-muted)]">
        {{ t('provision.runtimeMode.attach.dockerNote') }}
      </p>

      <div
        v-if="runningInstance.kind !== 'serverless'"
        class="grid gap-3 sm:grid-cols-2"
      >
        <label class="block space-y-2">
          <span class="lp-label">{{ t('provision.runtimeMode.attach.processStrategy') }}</span>
          <select
            v-model="runningInstance.process_strategy"
            class="lp-input"
            :disabled="disabled"
          >
            <option value="docker">{{ t('provision.runtimeMode.attach.strategies.docker') }}</option>
            <option value="systemd">{{ t('provision.runtimeMode.attach.strategies.systemd') }}</option>
            <option value="pm2">{{ t('provision.runtimeMode.attach.strategies.pm2') }}</option>
          </select>
          <p class="text-[11px] text-[var(--lp-muted)]">
            {{ t(`provision.runtimeMode.attach.strategyHints.${runningInstance.process_strategy || 'docker'}`) }}
          </p>
        </label>
        <label
          v-if="runningInstance.process_strategy !== 'docker'"
          class="block space-y-2"
        >
          <span class="lp-label">{{ t('provision.runtimeMode.attach.codeSource') }}</span>
          <select
            v-model="runningInstance.code_source"
            class="lp-input"
            :disabled="disabled"
          >
            <option value="ssh">{{ t('provision.runtimeMode.attach.codeSources.ssh') }}</option>
            <option value="github">{{ t('provision.runtimeMode.attach.codeSources.github') }}</option>
          </select>
          <p class="text-[11px] text-[var(--lp-muted)]">
            {{ t(`provision.runtimeMode.attach.codeSourceHints.${runningInstance.code_source || 'ssh'}`) }}
          </p>
        </label>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('provision.runtimeMode.attach.reverseProxy') }}</span>
          <select
            v-model="runningInstance.reverse_proxy"
            class="lp-input"
            :disabled="disabled"
          >
            <option value="none">{{ t('provision.runtimeMode.attach.proxies.none') }}</option>
            <option value="nginx">{{ t('provision.runtimeMode.attach.proxies.nginx') }}</option>
            <option value="caddy">{{ t('provision.runtimeMode.attach.proxies.caddy') }}</option>
          </select>
          <p class="text-[11px] text-[var(--lp-muted)]">
            {{ t('provision.runtimeMode.attach.reverseProxyHint') }}
          </p>
        </label>
      </div>

      <div class="grid gap-3 sm:grid-cols-2">
        <button
          v-for="target in computeTargets"
          :key="target.id"
          type="button"
          class="rounded-xl border p-3 text-left transition active:scale-[0.98]"
          :class="
            selectedTargetId === target.id
              ? 'border-2 border-[var(--lp-accent)] bg-[var(--lp-panel-2)]'
              : 'border-[var(--lp-line)] bg-[var(--lp-panel)]/60 hover:border-[var(--lp-accent)]/50'
          "
          :disabled="disabled"
          @click="selectComputeTarget(target.id)"
        >
          <span
            class="material-symbols-outlined text-xl"
            :class="
              selectedTargetId === target.id
                ? 'text-[var(--lp-accent)]'
                : 'text-[var(--lp-muted)]'
            "
          >
            {{ targetCard(target).icon }}
          </span>
          <p class="mt-1.5 text-sm font-medium text-[var(--lp-text)]">
            {{ t(targetCard(target).titleKey) }}
          </p>
          <p class="mt-1 text-xs text-[var(--lp-muted)]">
            {{ t(targetCard(target).descKey) }}
          </p>
          <p
            class="mt-2 text-[10px] uppercase tracking-wide text-[var(--lp-muted)]"
          >
            {{ t(targetCard(target).badgeKey) }}
          </p>
        </button>
      </div>

      <template v-if="runningInstance.kind === 'serverless'">
        <p class="text-xs text-[var(--lp-muted)]">
          {{ t('provision.runtimeMode.attach.serverlessHint') }}
        </p>
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="block space-y-2">
            <span class="lp-label">{{ t('provision.runtimeMode.attach.serviceName') }}</span>
            <input
              v-model="runningInstance.service_name"
              class="lp-input"
              placeholder="my-preview"
              :disabled="disabled"
            >
          </label>
          <label class="block space-y-2">
            <span class="lp-label">{{ t('provision.runtimeMode.attach.region') }}</span>
            <input
              v-model="runningInstance.region"
              class="lp-input"
              :placeholder="provider === 'aws' ? 'us-east-1' : provider === 'azure' ? 'eastus' : 'us-central1'"
              :disabled="disabled"
            >
          </label>
        </div>
        <p class="text-xs text-[var(--lp-muted)]">
          {{ t('provision.runtimeMode.attach.serverlessConfigHint') }}
        </p>
      </template>

      <template v-else-if="runningInstance.kind === 'vm'">
        <p class="text-xs text-[var(--lp-muted)]">
          {{ t('provision.runtimeMode.attach.vmConfigHint') }}
        </p>
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="block space-y-2 sm:col-span-2">
            <span class="lp-label">{{ t('provision.runtimeMode.attach.host') }}</span>
            <input
              v-model="runningInstance.host"
              class="lp-input"
              placeholder="ec2-xx.compute.amazonaws.com or 203.0.113.10"
              :disabled="disabled"
            >
          </label>
          <label class="block space-y-2">
            <span class="lp-label">{{ t('provision.runtimeMode.attach.sshUser') }}</span>
            <input
              v-model="runningInstance.ssh_user"
              class="lp-input"
              placeholder="ubuntu"
              :disabled="disabled"
            >
          </label>
          <label class="block space-y-2">
            <span class="lp-label">{{ t('provision.runtimeMode.attach.sshPort') }}</span>
            <input
              v-model.number="runningInstance.ssh_port"
              type="number"
              min="1"
              max="65535"
              class="lp-input"
              :disabled="disabled"
            >
          </label>
          <label class="block space-y-2 sm:col-span-2">
            <span class="lp-label">{{ t('provision.runtimeMode.attach.sshKeyPath') }}</span>
            <input
              v-model="runningInstance.ssh_key_path"
              class="lp-input"
              placeholder="~/.ssh/id_ed25519"
              :disabled="disabled"
            >
          </label>
          <label class="block space-y-2">
            <span class="lp-label">{{ t('provision.runtimeMode.attach.listenPort') }}</span>
            <input
              v-model.number="runningInstance.listen_port"
              type="number"
              min="1"
              max="65535"
              class="lp-input"
              :disabled="disabled"
            >
          </label>
        </div>
      </template>

      <template v-else>
        <p class="text-xs text-[var(--lp-muted)]">
          {{
            t(
              `provision.runtimeMode.attach.strategyHints.${runningInstance.process_strategy || 'docker'}`,
            )
          }}
        </p>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('provision.runtimeMode.attach.listenPort') }}</span>
          <input
            v-model.number="runningInstance.listen_port"
            type="number"
            min="1"
            max="65535"
            class="lp-input"
            :disabled="disabled"
          >
        </label>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { NodeInstallInstructions, NodeRead, NodeStatus } from '~/types/nodes'
import { toastError } from '~/composables/useToast'

const props = withDefaults(
  defineProps<{
    modelValue?: string | null
    pollMs?: number
  }>(),
  { modelValue: null, pollMs: 5000 },
)

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
}>()

const { t } = useI18n()
const toast = useToast()
const { nodes, loading, error, refresh, enroll, revoke } = useNodes()

const showEnroll = ref(false)
const newNodeName = ref('')
const enrolling = ref(false)
const instructions = ref<NodeInstallInstructions | null>(null)
const copied = ref(false)

let timer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  try {
    await refresh()
  } catch {
    // surfaced via `error`
  }
  timer = setInterval(() => {
    void refresh().catch(() => {})
  }, Math.max(props.pollMs, 2000))
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

const selectedId = computed({
  get: () => props.modelValue,
  set: (value: string | null) => emit('update:modelValue', value),
})

function statusLabel(status: NodeStatus): string {
  return t(`hybrid.nodes.${status.toLowerCase()}`)
}

function statusTone(node: NodeRead): string {
  if (node.status === 'ONLINE' && node.online) return 'var(--lp-ok)'
  if (node.status === 'REVOKED') return 'var(--lp-danger)'
  if (node.status === 'PENDING') return 'var(--lp-warn)'
  return 'var(--lp-muted)'
}

function pct(value: number | null): number {
  return Math.round(Math.min(Math.max(value ?? 0, 0), 100))
}

function barColor(value: number | null): string {
  const v = value ?? 0
  if (v >= 85) return 'var(--lp-danger)'
  if (v >= 60) return 'var(--lp-warn)'
  return 'var(--lp-accent)'
}

function lastSeen(node: NodeRead): string {
  if (!node.last_heartbeat_at) return t('hybrid.nodes.never')
  const d = new Date(node.last_heartbeat_at)
  return d.toLocaleString()
}

async function submitEnroll() {
  if (!newNodeName.value.trim()) return
  enrolling.value = true
  try {
    instructions.value = await enroll({ name: newNodeName.value.trim() })
    showEnroll.value = false
    newNodeName.value = ''
  } catch (err) {
    toast.error(toastError(err, 'Enrollment failed'))
  } finally {
    enrolling.value = false
  }
}

async function copyInstall() {
  if (!instructions.value) return
  try {
    await navigator.clipboard.writeText(instructions.value.install_command)
    copied.value = true
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    // clipboard blocked; user can select manually
  }
}

async function onRevoke(node: NodeRead) {
  if (!window.confirm(t('hybrid.nodes.revokeConfirm'))) return
  try {
    await revoke(node.id)
    if (selectedId.value === node.id) selectedId.value = null
    toast.success(t('hybrid.nodes.revoke'))
  } catch (err) {
    toast.error(toastError(err, 'Revoke failed'))
  }
}

function selectNode(node: NodeRead) {
  selectedId.value = selectedId.value === node.id ? null : node.id
}
</script>

<template>
  <section class="lp-panel space-y-4 p-5 sm:p-6">
    <header class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0 space-y-1">
        <p class="lp-label">{{ t('hybrid.nodes.title') }}</p>
        <p class="text-sm leading-relaxed text-[var(--lp-muted)]">{{ t('hybrid.nodes.subtitle') }}</p>
      </div>
      <button type="button" class="lp-btn-primary shrink-0" @click="showEnroll = !showEnroll">
        {{ t('hybrid.nodes.enroll') }}
      </button>
    </header>

    <!-- Enroll form -->
    <div v-if="showEnroll" class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)] p-4">
      <p class="lp-label mb-2">{{ t('hybrid.nodes.enrollTitle') }}</p>
      <div class="flex flex-wrap items-end gap-3">
        <label class="flex-1 min-w-[12rem]">
          <span class="lp-label mb-1 block">{{ t('hybrid.nodes.nameLabel') }}</span>
          <input
            v-model="newNodeName"
            class="lp-input w-full"
            :placeholder="t('hybrid.nodes.namePlaceholder')"
            @keyup.enter="submitEnroll"
          >
        </label>
        <button
          type="button"
          class="lp-btn-primary"
          :disabled="enrolling || !newNodeName.trim()"
          @click="submitEnroll"
        >
          {{ enrolling ? t('common.loading') : t('hybrid.nodes.create') }}
        </button>
      </div>
    </div>

    <!-- Install instructions (token shown once) -->
    <div
      v-if="instructions"
      class="rounded-lg border border-[var(--lp-accent)]/40 bg-[var(--lp-accent)]/10 p-4"
    >
      <div class="flex items-center justify-between gap-3">
        <p class="lp-label">{{ t('hybrid.nodes.installTitle') }}</p>
        <button type="button" class="lp-btn-ghost text-xs" @click="instructions = null">
          {{ t('hybrid.nodes.done') }}
        </button>
      </div>
      <p class="mb-2 mt-1 text-xs text-[var(--lp-muted)]">{{ t('hybrid.nodes.installHint') }}</p>
      <div class="lp-console overflow-x-auto">
        <code class="lp-console-line lp-console-line-accent whitespace-pre">{{ instructions.install_command }}</code>
      </div>
      <div class="mt-2 flex items-center gap-3">
        <button type="button" class="lp-btn-ghost text-xs" @click="copyInstall">
          {{ copied ? t('hybrid.nodes.copied') : t('hybrid.nodes.copy') }}
        </button>
        <span class="text-xs text-[var(--lp-warn)]">{{ t('hybrid.nodes.tokenOnce') }}</span>
      </div>
    </div>

    <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
    <p v-if="!nodes.length && !loading" class="text-sm text-[var(--lp-muted)]">
      {{ t('hybrid.nodes.empty') }}
    </p>

    <!-- Node cards -->
    <ul class="space-y-3">
      <li
        v-for="node in nodes"
        :key="node.id"
        class="cursor-pointer rounded-xl border p-4 transition"
        :class="selectedId === node.id
          ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 shadow-[0_0_0_1px_color-mix(in_srgb,var(--lp-accent)_35%,transparent)]'
          : 'border-[var(--lp-line)] bg-[var(--lp-panel-2)]/80 hover:border-[var(--lp-accent)]/40'"
        @click="selectNode(node)"
      >
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <span
              class="inline-block h-2.5 w-2.5 rounded-full"
              :style="{ backgroundColor: statusTone(node) }"
              aria-hidden="true"
            />
            <span class="font-semibold text-[var(--lp-text)]">{{ node.name }}</span>
            <span class="text-xs uppercase tracking-wide text-[var(--lp-muted)]">
              {{ statusLabel(node.status) }}
            </span>
          </div>
          <button
            type="button"
            class="lp-btn-danger text-xs"
            @click.stop="onRevoke(node)"
          >
            {{ t('hybrid.nodes.revoke') }}
          </button>
        </div>

        <div class="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--lp-muted)]">
          <span v-if="node.platform">{{ t('hybrid.nodes.platform') }}: {{ node.platform }}</span>
          <span v-if="node.agent_version">{{ t('hybrid.nodes.version') }} {{ node.agent_version }}</span>
          <span>{{ t('hybrid.nodes.docker') }}: {{ node.docker_status ?? '-' }}</span>
          <span>{{ t('hybrid.nodes.lastSeen') }}: {{ lastSeen(node) }}</span>
        </div>

        <!-- Telemetry bars -->
        <div class="mt-3 grid grid-cols-3 gap-3">
          <div v-for="metric in [
            { key: 'cpu', label: t('hybrid.nodes.cpu'), value: node.cpu_percent },
            { key: 'mem', label: t('hybrid.nodes.mem'), value: node.mem_percent },
            { key: 'disk', label: t('hybrid.nodes.disk'), value: node.disk_percent },
          ]" :key="metric.key">
            <div class="mb-1 flex items-center justify-between text-[11px] text-[var(--lp-muted)]">
              <span>{{ metric.label }}</span>
              <span>{{ pct(metric.value) }}%</span>
            </div>
            <div class="h-1.5 w-full overflow-hidden rounded-full bg-[var(--lp-line)]">
              <div
                class="h-full rounded-full transition-all"
                :style="{ width: pct(metric.value) + '%', backgroundColor: barColor(metric.value) }"
              />
            </div>
          </div>
        </div>

        <div class="mt-2 text-xs text-[var(--lp-muted)]">
          {{ t('hybrid.nodes.containers') }}: {{ node.containers.length }}
          <span v-if="node.containers.length" class="text-[var(--lp-text)]">
            ({{ node.containers.map((c) => c.name).slice(0, 4).join(', ') }})
          </span>
        </div>
      </li>
    </ul>
  </section>
</template>

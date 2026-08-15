<script setup lang="ts">
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import type { TerminalServerMessage } from '~/types/provisioning'
import type { EnvironmentMetrics, EnvironmentHealthPing } from '~/types/observability'

const props = defineProps<{
  environmentId: string
  deployMode?: string | null
  canShell?: boolean
}>()

const { t } = useI18n()
const { shellWsPath, fetchMetrics, pingHealth } = useEnvironmentObservability()
const { connected, error, connect, sendInput, resize, disconnect } = useTerminalSession()

type ConsoleMode = 'shell' | 'logs'

const mode = ref<ConsoleMode>('shell')
const host = ref<HTMLElement | null>(null)
const term = shallowRef<Terminal | null>(null)
const fitAddon = shallowRef<FitAddon | null>(null)
const statusLabel = ref(t('envConsole.status.idle'))
const metrics = ref<EnvironmentMetrics | null>(null)
const health = ref<EnvironmentHealthPing | null>(null)
const refreshing = ref(false)
let logSource: EventSource | null = null

const preferSsh = computed(() => (props.deployMode || '').toLowerCase() === 'attach')

function xtermTheme() {
  return {
    background: '#0b1219',
    foreground: '#e8eef5',
    cursor: '#2dd4bf',
    selectionBackground: '#2dd4bf55',
  }
}

function ensureTerminal() {
  if (!host.value || term.value) return
  const instance = new Terminal({
    cursorBlink: true,
    convertEol: true,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 13,
    theme: xtermTheme(),
  })
  const fit = new FitAddon()
  instance.loadAddon(fit)
  instance.loadAddon(new WebLinksAddon())
  instance.open(host.value)
  fit.fit()
  instance.onData((data) => {
    if (mode.value === 'shell') sendInput(data)
  })
  term.value = instance
  fitAddon.value = fit
  window.addEventListener('resize', onWindowResize)
}

function onWindowResize() {
  if (!term.value || !fitAddon.value) return
  fitAddon.value.fit()
  if (mode.value === 'shell') resize(term.value.cols, term.value.rows)
}

function handleServerMessage(msg: TerminalServerMessage) {
  if (!term.value) return
  if (msg.type === 'output') {
    term.value.write(msg.data)
  } else if (msg.type === 'ready') {
    statusLabel.value = t('envConsole.status.connected', {
      mode: msg.mode,
      target: msg.target || '-',
    })
    fitAddon.value?.fit()
    if (term.value) resize(term.value.cols, term.value.rows)
    term.value.focus()
  } else if (msg.type === 'error') {
    term.value.writeln(`\r\n\x1b[31m[error] ${msg.message}\x1b[0m`)
    statusLabel.value = t('envConsole.status.error')
  } else if (msg.type === 'status') {
    statusLabel.value =
      msg.status === 'killed' ? t('envConsole.status.disconnected') : String(msg.status)
  }
}

function stopLogs() {
  if (logSource) {
    logSource.close()
    logSource = null
  }
}

function startShell() {
  stopLogs()
  disconnect()
  ensureTerminal()
  term.value?.reset()
  term.value?.writeln(t('envConsole.shellBanner'))
  const path = shellWsPath(props.environmentId, preferSsh.value ? 'ssh' : undefined)
  statusLabel.value = t('envConsole.status.connecting')
  connect(path, handleServerMessage)
}

function startLogs() {
  disconnect()
  stopLogs()
  ensureTerminal()
  term.value?.reset()
  term.value?.writeln(t('envConsole.logsBanner'))
  statusLabel.value = t('envConsole.status.streamingLogs')
  const { token } = useAuth()
  const config = useRuntimeConfig()
  const base = String(config.public.apiBase || '/api/v1').replace(/\/$/, '')
  const url = `${base}/environments/${props.environmentId}/logs/stream`
  // EventSource cannot set Authorization; use query token when present.
  const withAuth = token.value
    ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token.value)}`
    : url
  logSource = new EventSource(withAuth, { withCredentials: true })
  logSource.addEventListener('log', (event) => {
    try {
      const payload = JSON.parse((event as MessageEvent).data) as {
        message?: string
        log_level?: string
        timestamp?: string
      }
      const line = payload.message || ''
      const ts = payload.timestamp ? payload.timestamp.slice(11, 19) : ''
      term.value?.writeln(`\x1b[90m${ts}\x1b[0m ${line}`)
    } catch {
      term.value?.writeln(String((event as MessageEvent).data))
    }
  })
  logSource.addEventListener('done', () => {
    statusLabel.value = t('envConsole.status.logsDone')
    stopLogs()
  })
  logSource.onerror = () => {
    statusLabel.value = t('envConsole.status.logsError')
  }
}

function setMode(next: ConsoleMode) {
  if (mode.value === next) return
  mode.value = next
  if (next === 'shell') startShell()
  else startLogs()
}

async function refreshObservability() {
  refreshing.value = true
  try {
    const [m, h] = await Promise.all([
      fetchMetrics(props.environmentId),
      pingHealth(props.environmentId),
    ])
    metrics.value = m
    health.value = h
  } catch {
    // surfaced via empty cards
  } finally {
    refreshing.value = false
  }
}

function barWidth(pct: number | null | undefined): string {
  const v = Math.max(0, Math.min(100, pct ?? 0))
  return `${v}%`
}

function barColor(pct: number | null | undefined): string {
  const v = pct ?? 0
  if (v >= 85) return 'var(--lp-danger)'
  if (v >= 60) return 'var(--lp-warn)'
  return 'var(--lp-accent)'
}

onMounted(async () => {
  ensureTerminal()
  await refreshObservability()
  if (props.canShell !== false) startShell()
  else {
    mode.value = 'logs'
    startLogs()
  }
})

onUnmounted(() => {
  stopLogs()
  disconnect()
  window.removeEventListener('resize', onWindowResize)
  term.value?.dispose()
  term.value = null
})

watch(
  () => props.environmentId,
  async () => {
    await refreshObservability()
    if (mode.value === 'shell') startShell()
    else startLogs()
  },
)
</script>

<template>
  <section class="lp-glass space-y-4 overflow-hidden rounded-xl p-4 sm:p-5">
    <header class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="text-lg font-semibold tracking-tight">{{ t('envConsole.title') }}</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">{{ t('envConsole.blurb') }}</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="lp-btn-ghost text-sm"
          :class="mode === 'shell' ? 'border-[var(--lp-accent)] text-[var(--lp-accent)]' : ''"
          :disabled="canShell === false"
          @click="setMode('shell')"
        >
          <span class="material-symbols-outlined text-base">terminal</span>
          {{ t('envConsole.shell') }}
        </button>
        <button
          type="button"
          class="lp-btn-ghost text-sm"
          :class="mode === 'logs' ? 'border-[var(--lp-accent)] text-[var(--lp-accent)]' : ''"
          @click="setMode('logs')"
        >
          <span class="material-symbols-outlined text-base">receipt_long</span>
          {{ t('envConsole.logs') }}
        </button>
        <button
          type="button"
          class="lp-btn-ghost text-sm"
          :disabled="refreshing"
          @click="refreshObservability"
        >
          <span class="material-symbols-outlined text-base">refresh</span>
          {{ t('envConsole.refresh') }}
        </button>
      </div>
    </header>

    <div class="grid gap-3 sm:grid-cols-3">
      <div class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel)] p-3">
        <p class="lp-label">{{ t('envConsole.cpu') }}</p>
        <p class="mt-1 font-mono text-lg">
          {{ metrics?.available ? metrics.cpu_cores.toFixed(2) : '-' }}
          <span class="text-xs text-[var(--lp-muted)]">cores</span>
        </p>
        <div class="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--lp-line)]">
          <div
            class="h-full rounded-full transition-all"
            :style="{ width: barWidth(metrics?.cpu_percent), background: barColor(metrics?.cpu_percent) }"
          />
        </div>
      </div>
      <div class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel)] p-3">
        <p class="lp-label">{{ t('envConsole.memory') }}</p>
        <p class="mt-1 font-mono text-lg">
          {{ metrics?.available ? metrics.memory_gib.toFixed(2) : '-' }}
          <span class="text-xs text-[var(--lp-muted)]">GiB</span>
        </p>
        <div class="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--lp-line)]">
          <div
            class="h-full rounded-full transition-all"
            :style="{ width: barWidth(metrics?.memory_percent), background: barColor(metrics?.memory_percent) }"
          />
        </div>
      </div>
      <div class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel)] p-3">
        <p class="lp-label">{{ t('envConsole.healthPing') }}</p>
        <p class="mt-1 text-lg font-medium" :class="health?.ok ? 'text-[var(--lp-ok)]' : 'text-[var(--lp-danger)]'">
          {{
            health
              ? health.ok
                ? t('envConsole.healthy')
                : t('envConsole.unhealthy')
              : '-'
          }}
        </p>
        <p class="mt-1 font-mono text-xs text-[var(--lp-muted)]">
          {{ health?.latency_ms != null ? `${health.latency_ms} ms` : health?.message || '-' }}
        </p>
      </div>
    </div>

    <div class="flex items-center justify-between gap-2 text-xs text-[var(--lp-muted)]">
      <span>{{ statusLabel }}</span>
      <span v-if="error" class="text-[var(--lp-danger)]">{{ error }}</span>
      <span v-else-if="connected && mode === 'shell'">{{ t('envConsole.live') }}</span>
    </div>

    <div
      ref="host"
      class="h-72 w-full overflow-hidden rounded-lg border border-[var(--lp-line)] bg-[#0b1219] p-2"
    />
  </section>
</template>

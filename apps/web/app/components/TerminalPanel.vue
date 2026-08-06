<script setup lang="ts">
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import type { TerminalServerMessage } from '~/types/provisioning'

const props = defineProps<{
  wsPath: string | null
}>()

const { t } = useI18n()

const emit = defineEmits<{
  ready: [sessionId: string]
  exit: []
}>()

const host = ref<HTMLElement | null>(null)
const term = shallowRef<Terminal | null>(null)
const fitAddon = shallowRef<FitAddon | null>(null)
const statusLabel = ref(t('terminal.status.idle'))
const startedForPath = ref<string | null>(null)

const { connected, error, connect, sendInput, resize, kill, disconnect } = useTerminalSession()
const { ingestTerminalOutput, setConnected } = useGuardedTerminalCommand()

watch(
  connected,
  (isConnected) => {
    setConnected(isConnected)
  },
  { immediate: true },
)

function xtermTheme() {
  // Keep the sandbox terminal dark in both themes for readable ANSI output.
  return {
    background: '#0b1219',
    foreground: '#e8eef5',
    cursor: '#2dd4bf',
    selectionBackground: '#2dd4bf55',
    black: '#0b1219',
    red: '#f87171',
    green: '#34d399',
    yellow: '#fbbf24',
    blue: '#7dd3fc',
    magenta: '#c4b5fd',
    cyan: '#2dd4bf',
    white: '#e8eef5',
    brightBlack: '#8fa3b8',
    brightRed: '#fca5a5',
    brightGreen: '#6ee7b7',
    brightYellow: '#fcd34d',
    brightBlue: '#bae6fd',
    brightMagenta: '#ddd6fe',
    brightCyan: '#5eead4',
    brightWhite: '#ffffff',
  }
}

function handleServerMessage(msg: TerminalServerMessage) {
  if (!term.value) return
  if (msg.type === 'output') {
    term.value.write(msg.data)
    ingestTerminalOutput(msg.data)
  } else if (msg.type === 'ready') {
    statusLabel.value = t('terminal.status.connected', { mode: msg.mode })
    setConnected(true)
    emit('ready', msg.session_id)
    focusTerminal()
    if (term.value && fitAddon.value) {
      fitAddon.value.fit()
      resize(term.value.cols, term.value.rows)
    }
  } else if (msg.type === 'error') {
    term.value.writeln(`\r\n\x1b[31m[error] ${msg.message}\x1b[0m`)
    statusLabel.value = t('terminal.status.error')
    ingestTerminalOutput(`\n[error] ${msg.message}\n__LP_EXIT_CODE:1__\n`)
  } else if (msg.type === 'status') {
    statusLabel.value = msg.status === 'killed' ? t('terminal.status.killed') : msg.status
    if (msg.status === 'killed') {
      setConnected(false)
      emit('exit')
    }
  }
}

function focusTerminal() {
  nextTick(() => {
    term.value?.focus()
  })
}

function onWindowResize() {
  if (!term.value || !fitAddon.value) return
  fitAddon.value.fit()
  resize(term.value.cols, term.value.rows)
}

function bootTerminal() {
  if (!host.value || term.value) return

  const instance = new Terminal({
    cursorBlink: true,
    convertEol: true,
    disableStdin: false,
    allowProposedApi: true,
    fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
    fontSize: 13,
    scrollback: 5000,
    theme: xtermTheme(),
  })
  const fit = new FitAddon()
  instance.loadAddon(fit)
  instance.loadAddon(new WebLinksAddon())
  instance.open(host.value)
  fit.fit()

  instance.onData((data) => {
    sendInput(data)
  })

  // Keep focus when the user clicks the host area.
  instance.attachCustomKeyEventHandler(() => true)

  term.value = instance
  fitAddon.value = fit
  window.addEventListener('resize', onWindowResize)
  focusTerminal()
}

function clearTerminal() {
  term.value?.clear()
  focusTerminal()
}

function killSession() {
  kill()
  statusLabel.value = t('terminal.status.killed')
  startedForPath.value = null
  emit('exit')
}

function startSession(path: string) {
  bootTerminal()
  if (!term.value) return
  connect(path, handleServerMessage)
  startedForPath.value = path
  statusLabel.value = t('terminal.status.connecting')
  nextTick(() => {
    if (term.value && fitAddon.value) {
      fitAddon.value.fit()
      resize(term.value.cols, term.value.rows)
      focusTerminal()
    }
  })
}

function restartSession() {
  if (!props.wsPath) return
  disconnect()
  term.value?.clear()
  term.value?.writeln(`\r\n\x1b[33m[launchpad] ${t('terminal.restarting')}\x1b[0m\r\n`)
  startedForPath.value = null
  startSession(props.wsPath)
}

function runCommand(command: string) {
  if (!connected.value) {
    term.value?.writeln(`\r\n\x1b[31m[launchpad] ${t('terminal.notConnected')}\x1b[0m`)
    return
  }
  // Send the command followed by Enter so the shell executes it.
  sendInput(`${command}\n`)
  focusTerminal()
}

const commandQueue = useState<string[]>('lp-terminal-cmd-queue', () => [])

function flushCommandQueue() {
  if (!connected.value || !commandQueue.value.length) return
  const [next, ...rest] = commandQueue.value
  commandQueue.value = rest
  if (next) runCommand(next)
}

watch(
  commandQueue,
  () => {
    flushCommandQueue()
  },
  { deep: true },
)

watch(connected, (isConnected) => {
  if (isConnected) flushCommandQueue()
})

defineExpose({ runCommand, focusTerminal, restartSession })

watch(
  [() => props.wsPath, host],
  ([path, el]) => {
    if (!path || !el) return
    if (startedForPath.value === path && term.value) {
      focusTerminal()
      return
    }
    startSession(path)
  },
  { immediate: true, flush: 'post' },
)

onUnmounted(() => {
  window.removeEventListener('resize', onWindowResize)
  setConnected(false)
  disconnect()
  term.value?.dispose()
  term.value = null
  startedForPath.value = null
})
</script>

<template>
  <section class="overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]">
    <div class="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-4 py-3">
      <div>
        <h2 class="flex items-center gap-2 text-sm font-medium tracking-wide text-[var(--lp-accent)]">
          <span class="material-symbols-outlined text-base">terminal</span>
          {{ t('terminal.title') }}
        </h2>
        <p class="font-mono text-xs" :class="error ? 'text-[var(--lp-danger)]' : 'text-[var(--lp-muted)]'">
          {{ error || statusLabel }}
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button type="button" class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide" @click="clearTerminal">
          {{ t('terminal.clear') }}
        </button>
        <button type="button" class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide" @click="restartSession">
          {{ t('terminal.restart') }}
        </button>
        <button type="button" class="lp-btn-danger py-1.5 text-xs uppercase tracking-wide" @click="killSession">
          {{ t('terminal.kill') }}
        </button>
      </div>
    </div>
    <div
      ref="host"
      class="lp-console h-[420px] w-full cursor-text p-2 outline-none"
      tabindex="0"
      role="application"
      :aria-label="t('terminal.ariaLabel')"
      @click="focusTerminal"
      @focus="focusTerminal"
    />
  </section>
</template>

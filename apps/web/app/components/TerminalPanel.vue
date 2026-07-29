<script setup lang="ts">
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import type { TerminalServerMessage } from '~/types/provisioning'

const props = defineProps<{
  wsPath: string | null
}>()

const emit = defineEmits<{
  ready: [sessionId: string]
  exit: []
}>()

const host = ref<HTMLElement | null>(null)
const term = shallowRef<Terminal | null>(null)
const fitAddon = shallowRef<FitAddon | null>(null)
const statusLabel = ref('idle')
const startedForPath = ref<string | null>(null)

const { connected, error, connect, sendInput, resize, kill, disconnect } = useTerminalSession()

function handleServerMessage(msg: TerminalServerMessage) {
  if (!term.value) return
  if (msg.type === 'output') {
    term.value.write(msg.data)
  } else if (msg.type === 'ready') {
    statusLabel.value = `connected (${msg.mode}) — type to interact`
    emit('ready', msg.session_id)
    focusTerminal()
    if (term.value && fitAddon.value) {
      fitAddon.value.fit()
      resize(term.value.cols, term.value.rows)
    }
  } else if (msg.type === 'error') {
    term.value.writeln(`\r\n\x1b[31m[error] ${msg.message}\x1b[0m`)
    statusLabel.value = 'error'
  } else if (msg.type === 'status') {
    statusLabel.value = msg.status
    if (msg.status === 'killed') {
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
    theme: {
      background: '#0b1219',
      foreground: '#e8eef5',
      cursor: '#2dd4bf',
      selectionBackground: '#2dd4bf55',
    },
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
  statusLabel.value = 'killed'
  startedForPath.value = null
  emit('exit')
}

function startSession(path: string) {
  bootTerminal()
  if (!term.value) return
  connect(path, handleServerMessage)
  startedForPath.value = path
  statusLabel.value = 'connecting'
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
  term.value?.writeln('\r\n\x1b[33m[launchpad] restarting session…\x1b[0m\r\n')
  startedForPath.value = null
  startSession(props.wsPath)
}

function runCommand(command: string) {
  if (!connected.value) {
    term.value?.writeln(`\r\n\x1b[31m[launchpad] terminal not connected\x1b[0m`)
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
          Sandbox terminal
        </h2>
        <p class="font-mono text-xs" :class="error ? 'text-[var(--lp-danger)]' : 'text-[var(--lp-muted)]'">
          {{ error || (connected ? statusLabel : statusLabel) }}
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button type="button" class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide" @click="clearTerminal">
          Clear
        </button>
        <button type="button" class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide" @click="restartSession">
          Restart
        </button>
        <button type="button" class="lp-btn-danger py-1.5 text-xs uppercase tracking-wide" @click="killSession">
          Kill
        </button>
      </div>
    </div>
    <div
      ref="host"
      class="h-[420px] w-full cursor-text bg-[#0b1219] p-2 outline-none"
      tabindex="0"
      role="application"
      aria-label="Interactive sandbox terminal"
      @click="focusTerminal"
      @focus="focusTerminal"
    />
  </section>
</template>

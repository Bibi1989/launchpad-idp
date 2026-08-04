<script setup lang="ts">
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import type { K8sResource } from '~/types/k8s'

const props = defineProps<{
  open: boolean
  resource: K8sResource | null
  workspaceId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const { getExecWsUrl } = useKubernetesSuite()

const terminalContainer = ref<HTMLElement | null>(null)
const term = shallowRef<Terminal | null>(null)
const fitAddon = shallowRef<FitAddon | null>(null)
let socket: WebSocket | null = null
const connected = ref(false)

function initTerminal() {
  if (!terminalContainer.value || !props.open || !props.resource) return

  if (term.value) {
    term.value.dispose()
    term.value = null
  }

  const t = new Terminal({
    cursorBlink: true,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 13,
    theme: {
      background: '#09090b',
      foreground: '#f4f4f5',
      cursor: '#38bdf8',
      selectionBackground: 'rgba(56, 189, 248, 0.3)',
      black: '#09090b',
      red: '#f87171',
      green: '#4ade80',
      yellow: '#facc15',
      blue: '#60a5fa',
      magenta: '#c084fc',
      cyan: '#38bdf8',
      white: '#f4f4f5',
    },
  })

  const fit = new FitAddon()
  t.loadAddon(fit)
  t.loadAddon(new WebLinksAddon())
  t.open(terminalContainer.value)
  fit.fit()

  term.value = t
  fitAddon.value = fit

  // WebSocket Connection - only real Pods can be exec'd
  if (props.resource.kind !== 'Pod') {
    t.write(
      '\r\n\x1b[31m[Exec Error] Shell requires a Pod. Select a Pod from the grid (not a Deployment).\x1b[0m\r\n',
    )
    connected.value = false
    term.value = t
    fitAddon.value = fit
    return
  }

  const url = getExecWsUrl(
    props.workspaceId,
    props.resource.name,
    undefined,
    props.resource.namespace,
  )
  socket = new WebSocket(url)

  socket.onopen = () => {
    connected.value = true
    t.write('\r\n\x1b[32m[K8s Exec Terminal Connected]\x1b[0m\r\n')
  }

  socket.onmessage = (evt) => {
    if (typeof evt.data === 'string') {
      try {
        const parsed = JSON.parse(evt.data)
        if (parsed.type === 'ready') {
          t.write(`\x1b[36mInteractive container shell ready on pod ${parsed.pod}\x1b[0m\r\n`)
        } else if (parsed.message) {
          t.write(`\r\n${parsed.message}\r\n`)
        }
      } catch {
        t.write(evt.data)
      }
    } else {
      t.write(new Uint8Array(evt.data))
    }
  }

  socket.onerror = () => {
    t.write('\r\n\x1b[31m[Exec Stream Error] Could not connect to container shell.\x1b[0m\r\n')
  }

  socket.onclose = () => {
    connected.value = false
    t.write('\r\n\x1b[33m[Exec Connection Closed]\x1b[0m\r\n')
  }

  t.onData((data) => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'input', data }))
    }
  })
}

watch(
  () => props.open,
  (val) => {
    if (val) {
      nextTick(() => initTerminal())
    } else {
      if (socket) {
        socket.close()
        socket = null
      }
      if (term.value) {
        term.value.dispose()
        term.value = null
      }
    }
  },
)

onBeforeUnmount(() => {
  if (socket) socket.close()
  if (term.value) term.value.dispose()
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-md"
      @click.self="emit('close')"
    >
      <div class="flex h-[80vh] w-full max-w-4xl flex-col rounded-2xl border border-[var(--lp-line)] bg-[var(--lp-ink)] shadow-2xl overflow-hidden font-mono text-xs animate-fade-up">
        <!-- Top Bar -->
        <div class="flex items-center justify-between border-b border-zinc-800 bg-zinc-900 px-5 py-3 text-zinc-300">
          <div class="flex items-center gap-3">
            <span class="material-symbols-outlined text-xl text-[var(--lp-accent)]">terminal</span>
            <div>
              <h3 class="font-bold text-sm text-zinc-100">
                Container Shell: {{ resource?.name }}
              </h3>
              <p class="text-[11px] text-zinc-400">
                Interactive PTY WebSocket (`kubectl exec -it`)
              </p>
            </div>
          </div>

          <div class="flex items-center gap-3">
            <span class="flex items-center gap-1 text-[11px]" :class="connected ? 'text-emerald-400' : 'text-amber-400'">
              <span class="h-2 w-2 rounded-full" :class="connected ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'" />
              {{ connected ? 'Connected' : 'Connecting…' }}
            </span>
            <button
              type="button"
              class="rounded-lg p-1.5 text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-100"
              @click="emit('close')"
            >
              <span class="material-symbols-outlined text-xl">close</span>
            </button>
          </div>
        </div>

        <!-- Terminal Host -->
        <div class="flex-1 bg-[#09090b] p-3 overflow-hidden">
          <div ref="terminalContainer" class="h-full w-full" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

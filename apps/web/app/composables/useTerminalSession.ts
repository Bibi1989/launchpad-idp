import type { TerminalServerMessage } from '~/types/provisioning'

export function useTerminalSession() {
  const socket = shallowRef<WebSocket | null>(null)
  const connected = ref(false)
  const error = ref<string | null>(null)
  const sessionId = ref<string | null>(null)

  function wsUrl(path: string): string {
    const config = useRuntimeConfig()
    const { token } = useAuth()
    const authQuery = token.value ? `token=${encodeURIComponent(token.value)}` : ''
    const join = path.includes('?') ? '&' : '?'
    const withAuth = authQuery ? `${path}${join}${authQuery}` : path

    // Prefer direct API WebSocket — Nuxt/Nitro HTTP proxies often break WS upgrades.
    const configured = String(config.public.wsBase || '').replace(/\/$/, '')
    if (configured) {
      return `${configured}${withAuth.startsWith('/') ? withAuth : `/${withAuth}`}`
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    if (withAuth.startsWith('/')) {
      return `${protocol}//${window.location.host}${withAuth}`
    }
    return withAuth
  }

  function connect(path: string, onMessage: (msg: TerminalServerMessage) => void) {
    disconnect()
    error.value = null
    const url = wsUrl(path)
    let ws: WebSocket
    try {
      ws = new WebSocket(url)
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to open WebSocket'
      connected.value = false
      return
    }
    socket.value = ws

    ws.onopen = () => {
      connected.value = true
      error.value = null
    }
    ws.onclose = (event) => {
      connected.value = false
      socket.value = null
      if (!event.wasClean && event.code !== 1000) {
        error.value =
          error.value ??
          `WebSocket closed (${event.code}). Is the API running on :8000 and NUXT_PUBLIC_WS_BASE set?`
      }
    }
    ws.onerror = () => {
      error.value =
        'WebSocket connection failed — ensure API is on :8000 and NUXT_PUBLIC_WS_BASE=ws://localhost:8000'
      connected.value = false
    }
    ws.onmessage = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as TerminalServerMessage
        if (payload.type === 'ready') {
          sessionId.value = payload.session_id
        }
        if (payload.type === 'error') {
          error.value = payload.message
        }
        onMessage(payload)
      } catch {
        error.value = 'Invalid terminal message'
      }
    }
  }

  function send(payload: Record<string, unknown>) {
    if (!socket.value || socket.value.readyState !== WebSocket.OPEN) {
      return
    }
    socket.value.send(JSON.stringify(payload))
  }

  function sendInput(data: string) {
    send({ type: 'input', data })
  }

  function resize(cols: number, rows: number) {
    send({ type: 'resize', cols, rows })
  }

  function kill() {
    send({ type: 'kill' })
  }

  function disconnect() {
    if (socket.value) {
      socket.value.onopen = null
      socket.value.onclose = null
      socket.value.onerror = null
      socket.value.onmessage = null
      if (
        socket.value.readyState === WebSocket.OPEN ||
        socket.value.readyState === WebSocket.CONNECTING
      ) {
        socket.value.close(1000, 'client disconnect')
      }
    }
    socket.value = null
    connected.value = false
  }

  onUnmounted(() => {
    disconnect()
  })

  return {
    socket,
    connected,
    error,
    sessionId,
    connect,
    sendInput,
    resize,
    kill,
    disconnect,
  }
}

export function useEnvironmentLogStream(
  environmentId: Ref<string | null> | ComputedRef<string | null>,
) {
  const lines = ref<string[]>([])
  const done = ref(false)
  const connected = ref(false)
  const terminalStatus = ref<string | null>(null)
  const source = shallowRef<EventSource | null>(null)
  const { token } = useAuth()

  function disconnect() {
    source.value?.close()
    source.value = null
    connected.value = false
  }

  function connect(id: string) {
    if (!import.meta.client || typeof EventSource === 'undefined') {
      return
    }
    disconnect()
    lines.value = []
    done.value = false
    terminalStatus.value = null

    const config = useRuntimeConfig()
    const authQuery = token.value ? `?token=${encodeURIComponent(token.value)}` : ''
    const es = new EventSource(
      `${config.public.apiBase}/environments/${id}/logs/stream${authQuery}`,
    )
    source.value = es
    connected.value = true

    es.addEventListener('log', (event: Event) => {
      const message = event as MessageEvent<string>
      try {
        const payload = JSON.parse(message.data) as {
          log_level: string
          message: string
          timestamp: string
        }
        lines.value = [
          ...lines.value,
          `[${payload.timestamp}] ${payload.log_level} ${payload.message}`,
        ]
      } catch {
        lines.value = [...lines.value, message.data]
      }
    })

    es.addEventListener('done', (event: Event) => {
      const message = event as MessageEvent<string>
      try {
        const payload = JSON.parse(message.data) as { status: string }
        terminalStatus.value = payload.status
        // RUNNING is success for the console banner; keep FAILED/DESTROYED as-is.
        const banner =
          payload.status === 'RUNNING' ? 'SUCCEEDED' : payload.status
        lines.value = [...lines.value, `--- ${banner} ---`]
      } catch {
        lines.value = [...lines.value, `--- ${message.data} ---`]
      }
      done.value = true
      disconnect()
    })

    es.onerror = () => {
      connected.value = false
      if (!done.value) {
        lines.value = [...lines.value, '[stream disconnected]']
      }
      disconnect()
    }
  }

  watch(
    environmentId,
    (id) => {
      if (id) {
        connect(id)
      } else {
        disconnect()
      }
    },
    { immediate: true },
  )

  onUnmounted(() => {
    disconnect()
  })

  return { lines, done, connected, terminalStatus, disconnect, connect }
}

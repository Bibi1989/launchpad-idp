import type { EnvironmentStatus, EnvStreamEvent } from '~/types/environment'

export function useEnvironmentLiveStream(
  environmentId: Ref<string | null> | ComputedRef<string | null>,
  options: {
    onEvent?: (event: EnvStreamEvent) => void
  } = {},
) {
  const status = ref<EnvironmentStatus | null>(null)
  const commitSha = ref<string | null>(null)
  const logLines = ref<string[]>([])
  const connected = ref(false)
  const source = shallowRef<EventSource | null>(null)
  const { token } = useAuth()

  function disconnect() {
    source.value?.close()
    source.value = null
    connected.value = false
  }

  function handlePayload(payload: EnvStreamEvent) {
    if (payload.status) {
      status.value = payload.status as EnvironmentStatus
    }
    if (payload.commit_sha) {
      commitSha.value = payload.commit_sha
    }
    if (payload.type === 'LOG' && payload.message) {
      const level = payload.log_level ?? 'INFO'
      logLines.value = [...logLines.value.slice(-199), `${level} ${payload.message}`]
    }
    options.onEvent?.(payload)
  }

  function connect(id: string) {
    if (!import.meta.client || typeof EventSource === 'undefined') {
      return
    }
    disconnect()
    logLines.value = []
    connected.value = false

    const config = useRuntimeConfig()
    const authQuery = token.value ? `?token=${encodeURIComponent(token.value)}` : ''
    const es = new EventSource(`${config.public.apiBase}/environments/${id}/stream${authQuery}`)
    source.value = es
    connected.value = true

    const onMessage = (event: Event) => {
      const message = event as MessageEvent<string>
      try {
        const payload = JSON.parse(message.data) as EnvStreamEvent
        handlePayload(payload)
      } catch {
        logLines.value = [...logLines.value, message.data]
      }
    }

    es.addEventListener('message', onMessage)
    es.onerror = () => {
      connected.value = false
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

  return {
    status,
    commitSha,
    logLines,
    connected,
    disconnect,
    connect,
  }
}

<script setup lang="ts">
import type { K8sResource } from '~/types/k8s'

const props = defineProps<{
  open: boolean
  resource: K8sResource | null
  workspaceId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const { t } = useI18n()

const logs = ref<string[]>([])
const searchFilter = ref('')
const tailing = ref(true)
const loading = ref(false)
const selectedContainer = ref('app')

const filteredLogs = computed(() => {
  if (!searchFilter.value.trim()) return logs.value
  const query = searchFilter.value.toLowerCase()
  return logs.value.filter((l) => l.toLowerCase().includes(query))
})

const logContainerRef = ref<HTMLDivElement | null>(null)

function scrollToBottom() {
  if (tailing.value && logContainerRef.value) {
    nextTick(() => {
      if (logContainerRef.value) {
        logContainerRef.value.scrollTop = logContainerRef.value.scrollHeight
      }
    })
  }
}

async function fetchLogs() {
  if (!props.resource || !props.open) return
  loading.value = true
  logs.value = []
  try {
    const config = useRuntimeConfig()
    const apiBase = config.public.apiBase || 'http://localhost:8000/api/v1'
    const tokenState = useState<string | null>('auth-token')
    const token = tokenState.value || (typeof window !== 'undefined' ? localStorage.getItem('launchpad_access_token') : '')
    const activeOrgState = useState<string | null>('active-org-id')
    const activeOrgId = activeOrgState.value || (typeof window !== 'undefined' ? localStorage.getItem('launchpad_active_org_id') : '')
    const url = `${apiBase}/workspaces/${props.workspaceId}/k8s/logs?pod_name=${encodeURIComponent(props.resource.name)}&namespace=${encodeURIComponent(props.resource.namespace)}&container_name=${selectedContainer.value}`
    
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    if (activeOrgId) headers['X-Org-ID'] = activeOrgId

    const res = await fetch(url, {
      headers,
    })

    if (!res.ok || !res.body) {
      logs.value = ['[error] Failed to attach log stream to pod container shell.']
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (props.open) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            if (data.line) {
              logs.value.push(data.line)
              scrollToBottom()
            }
          } catch {
            // ignore malformed SSE payloads
          }
        }
      }
    }
  } catch (err) {
    logs.value.push(`[error] Log stream disconnected: ${err instanceof Error ? err.message : String(err)}`)
  } finally {
    loading.value = false
  }
}

watch(
  () => props.open,
  (val) => {
    if (val) void fetchLogs()
  },
)

function downloadLogs() {
  const blob = new Blob([logs.value.join('\n')], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `k8s-logs-${props.resource?.name || 'pod'}.log`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-md"
      @click.self="emit('close')"
    >
      <div class="flex h-[85vh] w-full max-w-5xl flex-col rounded-2xl border border-[var(--lp-line)] bg-zinc-950 shadow-2xl overflow-hidden font-mono text-xs animate-fade-up">
        <!-- Modal Top Bar -->
        <div class="flex items-center justify-between border-b border-zinc-800 bg-zinc-900/80 px-5 py-3 text-zinc-300">
          <div class="flex items-center gap-3">
            <span class="material-symbols-outlined text-xl text-emerald-400">article</span>
            <div>
              <h3 class="font-bold text-sm text-zinc-100">
                {{ t('k8s.logs.title', { name: resource?.name }) }}
              </h3>
              <p class="text-[11px] text-zinc-400">
                {{ t('k8s.logs.meta', { namespace: resource?.namespace, container: selectedContainer }) }}
              </p>
            </div>
          </div>

          <div class="flex items-center gap-3">
            <!-- Search Input -->
            <div class="relative">
              <span class="material-symbols-outlined absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-zinc-500">search</span>
              <input
                v-model="searchFilter"
                type="text"
                :placeholder="t('k8s.logs.filterPlaceholder')"
                class="rounded-lg border border-zinc-800 bg-zinc-900 pl-8 pr-3 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:border-[var(--lp-accent)] focus:outline-none"
              >
            </div>

            <!-- Auto-scroll tailing toggle -->
            <label class="flex items-center gap-1.5 cursor-pointer text-xs text-zinc-400 hover:text-zinc-200">
              <input v-model="tailing" type="checkbox" class="accent-[var(--lp-accent)]">
              {{ t('k8s.logs.autoScroll') }}
            </label>

            <!-- Download -->
            <button
              type="button"
              class="flex items-center gap-1 rounded bg-zinc-800 px-2.5 py-1 text-xs text-zinc-300 transition hover:bg-zinc-700"
              @click="downloadLogs"
            >
              <span class="material-symbols-outlined text-sm">download</span>
              {{ t('k8s.logs.export') }}
            </button>

            <!-- Close -->
            <button
              type="button"
              class="rounded-lg p-1.5 text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-100"
              @click="emit('close')"
            >
              <span class="material-symbols-outlined text-xl">close</span>
            </button>
          </div>
        </div>

        <!-- Log Content View -->
        <div ref="logContainerRef" class="flex-1 overflow-y-auto p-4 font-mono text-[12px] leading-relaxed text-zinc-300 space-y-1 selection:bg-[var(--lp-accent)]/30">
          <div v-if="filteredLogs.length === 0" class="flex h-32 items-center justify-center text-zinc-500">
            {{ loading ? t('k8s.logs.connecting') : t('k8s.logs.noMatches') }}
          </div>
          <div
            v-for="(line, idx) in filteredLogs"
            :key="idx"
            class="hover:bg-zinc-900/60 rounded px-1.5 py-0.5 whitespace-pre-wrap break-all"
            :class="[
              line.includes('[error]') || line.includes('ERROR') ? 'text-rose-400 bg-rose-950/20' :
              line.includes('[warn]') || line.includes('WARN') ? 'text-amber-300 bg-amber-950/20' :
              line.includes('[info]') ? 'text-emerald-300' : 'text-zinc-300'
            ]"
          >
            {{ line }}
          </div>
        </div>

        <!-- Modal Footer -->
        <div class="border-t border-zinc-800 bg-zinc-900/80 px-5 py-2 flex items-center justify-between text-[11px] text-zinc-500">
          <span>{{ t('k8s.logs.showingLines', { count: filteredLogs.length }) }}</span>
          <span class="flex items-center gap-1">
            <span class="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
            {{ t('k8s.logs.liveConnected') }}
          </span>
        </div>
      </div>
    </div>
  </Teleport>
</template>

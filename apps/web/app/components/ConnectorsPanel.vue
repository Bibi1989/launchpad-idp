<script setup lang="ts">
import type {
  ConnectorKind,
  ServiceConnection,
  ServiceGraphResponse,
} from '~/types/serviceGraph'
import { toastError } from '~/composables/useToast'

const props = defineProps<{ workspaceId: string }>()

const { t } = useI18n()
const toast = useToast()
const { getServiceGraph, updateServiceConnections } = useProvisioning()

const graph = ref<ServiceGraphResponse | null>(null)
const loading = ref(false)
const saving = ref(false)

// Add-connector form state.
const newKind = ref<ConnectorKind>('service')
const newSource = ref('')
const newTarget = ref('')
const newExposeAs = ref('')
const newCorsOrigin = ref('')
const newApiPath = ref('')

const serviceNodes = computed(() =>
  (graph.value?.nodes ?? []).filter((n) => n.type === 'service'),
)
const hasServices = computed(() => serviceNodes.value.length > 0)
const connectors = computed<ServiceConnection[]>(() => graph.value?.connectors ?? [])

// Auto FE->BE URL wiring is inferred: any frontend framework node connects to backends.
const frontendFrameworks = new Set(['nextjs', 'nuxtjs', 'react_vite', 'vuejs', 'svelte', 'angular'])
const autoConnectors = computed(() => {
  const nodes = serviceNodes.value
  const frontends = nodes.filter((n) => frontendFrameworks.has((n.framework || '').toLowerCase()))
  const backends = nodes.filter((n) => !frontendFrameworks.has((n.framework || '').toLowerCase()))
  const out: { source: string; target: string }[] = []
  for (const fe of frontends) {
    for (const be of backends) out.push({ source: fe.label, target: be.label })
  }
  return out
})

onMounted(load)

async function load() {
  loading.value = true
  try {
    graph.value = await getServiceGraph(props.workspaceId)
  } catch {
    graph.value = null
  } finally {
    loading.value = false
  }
}

async function persist(next: ServiceConnection[]) {
  saving.value = true
  try {
    graph.value = await updateServiceConnections(props.workspaceId, next)
  } catch (err) {
    toast.error(toastError(err, t('connectors.saveFailed')))
  } finally {
    saving.value = false
  }
}

function sameConnector(a: ServiceConnection, b: ServiceConnection): boolean {
  return a.source === b.source && a.target === b.target && (a.kind || 'service') === (b.kind || 'service')
}

async function addConnector() {
  if (!newSource.value || !newTarget.value || newSource.value === newTarget.value) return
  const connector: ServiceConnection = {
    source: newSource.value,
    target: newTarget.value,
    protocol: 'http',
    kind: newKind.value,
    expose_as: newKind.value === 'service' ? newExposeAs.value.trim() || null : null,
    cors_origin: newKind.value === 'cors' ? newCorsOrigin.value.trim() || null : null,
    api_path: newKind.value === 'service' ? newApiPath.value.trim() || null : null,
  }
  const next = [...connectors.value.filter((c) => !sameConnector(c, connector)), connector]
  await persist(next)
  newSource.value = ''
  newTarget.value = ''
  newExposeAs.value = ''
  newCorsOrigin.value = ''
  newApiPath.value = ''
}

async function removeConnector(connector: ServiceConnection) {
  await persist(connectors.value.filter((c) => !sameConnector(c, connector)))
}
</script>

<template>
  <section v-if="hasServices" class="lp-panel space-y-4 p-5">
    <header class="space-y-1">
      <p class="lp-label">{{ t('connectors.title') }}</p>
      <p class="text-sm text-[var(--lp-muted)]">{{ t('connectors.subtitle') }}</p>
    </header>

    <!-- Auto-wired FE->BE connectors (informational) -->
    <div v-if="autoConnectors.length" class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)] p-3">
      <p class="lp-label mb-2">{{ t('connectors.autoTitle') }}</p>
      <ul class="flex flex-wrap gap-2">
        <li
          v-for="(a, i) in autoConnectors"
          :key="`auto-${i}`"
          class="rounded-full bg-[var(--lp-accent)]/10 px-3 py-1 text-xs text-[var(--lp-accent)]"
        >
          {{ a.source }} <span class="opacity-70">-URL-&gt;</span> {{ a.target }}
        </li>
      </ul>
      <p class="mt-2 text-[11px] text-[var(--lp-muted)]">{{ t('connectors.autoHint') }}</p>
    </div>

    <!-- Configured connectors -->
    <div>
      <p class="lp-label mb-2">{{ t('connectors.configured') }}</p>
      <p v-if="!connectors.length" class="text-sm text-[var(--lp-muted)]">{{ t('connectors.none') }}</p>
      <ul v-else class="space-y-1">
        <li
          v-for="(c, i) in connectors"
          :key="`c-${i}`"
          class="flex items-center justify-between gap-3 rounded-md border border-[var(--lp-line)] px-3 py-1.5 text-sm"
        >
          <span class="text-[var(--lp-text)]">
            <span
              class="mr-2 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide"
              :class="(c.kind || 'service') === 'cors'
                ? 'bg-amber-500/15 text-amber-300'
                : 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]'"
            >
              {{ (c.kind || 'service') === 'cors' ? t('connectors.kindCors') : t('connectors.kindService') }}
            </span>
            {{ c.source }} <span class="mx-1 text-[var(--lp-muted)]">-&gt;</span> {{ c.target }}
            <span v-if="c.api_path" class="ml-2 font-mono text-[11px] text-[var(--lp-muted)]">{{ c.api_path }}</span>
            <span v-if="c.expose_as" class="ml-2 font-mono text-[11px] text-[var(--lp-muted)]">{{ c.expose_as }}</span>
            <span v-if="c.cors_origin" class="ml-2 font-mono text-[11px] text-[var(--lp-muted)]">{{ c.cors_origin }}</span>
          </span>
          <button type="button" class="lp-btn-ghost text-xs" :disabled="saving" @click="removeConnector(c)">
            {{ t('connectors.remove') }}
          </button>
        </li>
      </ul>
    </div>

    <!-- Add connector -->
    <div class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)] p-4">
      <p class="lp-label mb-2">{{ t('connectors.add') }}</p>
      <div class="mb-3 flex gap-2">
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-xs transition"
          :class="newKind === 'service'
            ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
            : 'border-[var(--lp-line)] text-[var(--lp-muted)]'"
          @click="newKind = 'service'"
        >
          {{ t('connectors.kindService') }}
        </button>
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-xs transition"
          :class="newKind === 'cors'
            ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
            : 'border-[var(--lp-line)] text-[var(--lp-muted)]'"
          @click="newKind = 'cors'"
        >
          {{ t('connectors.kindCors') }}
        </button>
      </div>
      <div class="flex flex-wrap items-end gap-3">
        <label class="min-w-[9rem] flex-1">
          <span class="lp-label mb-1 block">
            {{ newKind === 'cors' ? t('connectors.frontend') : t('connectors.source') }}
          </span>
          <select v-model="newSource" class="lp-input w-full">
            <option value="">-</option>
            <option v-for="n in serviceNodes" :key="n.id" :value="n.label">{{ n.label }}</option>
          </select>
        </label>
        <label class="min-w-[9rem] flex-1">
          <span class="lp-label mb-1 block">
            {{ newKind === 'cors' ? t('connectors.backend') : t('connectors.target') }}
          </span>
          <select v-model="newTarget" class="lp-input w-full">
            <option value="">-</option>
            <option v-for="n in serviceNodes" :key="n.id" :value="n.label">{{ n.label }}</option>
          </select>
        </label>
        <label v-if="newKind === 'service'" class="min-w-[9rem] flex-1">
          <span class="lp-label mb-1 block">{{ t('connectors.apiPath') }}</span>
          <input v-model="newApiPath" class="lp-input w-full font-mono text-xs" placeholder="(base URL)">
        </label>
        <label v-if="newKind === 'service'" class="min-w-[9rem] flex-1">
          <span class="lp-label mb-1 block">{{ t('connectors.exposeAs') }}</span>
          <input v-model="newExposeAs" class="lp-input w-full font-mono text-xs" placeholder="NEXT_PUBLIC_API_URL">
        </label>
        <label v-else class="min-w-[10rem] flex-1">
          <span class="lp-label mb-1 block">{{ t('connectors.corsOrigin') }}</span>
          <input v-model="newCorsOrigin" class="lp-input w-full font-mono text-xs" placeholder="https://app.example.com">
        </label>
        <button
          type="button"
          class="lp-btn-primary"
          :disabled="saving || !newSource || !newTarget || newSource === newTarget"
          @click="addConnector"
        >
          {{ saving ? t('connectors.saving') : t('connectors.addButton') }}
        </button>
      </div>
      <p class="mt-2 text-[11px] text-[var(--lp-muted)]">
        {{ newKind === 'cors' ? t('connectors.corsHint') : t('connectors.serviceHint') }}
      </p>
    </div>
  </section>
</template>

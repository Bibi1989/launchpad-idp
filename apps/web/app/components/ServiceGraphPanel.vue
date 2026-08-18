<script setup lang="ts">
import type { ServiceConnection, ServiceGraphEdge, ServiceGraphResponse } from '~/types/serviceGraph'
import { toastError } from '~/composables/useToast'

const props = defineProps<{ workspaceId: string }>()

const { t } = useI18n()
const toast = useToast()
const { getServiceGraph, updateServiceConnections } = useProvisioning()

const graph = ref<ServiceGraphResponse | null>(null)
const loading = ref(false)
const saving = ref(false)

const newSource = ref('')
const newTarget = ref('')
const newProtocol = ref<ServiceConnection['protocol']>('http')

const protocols: ServiceConnection['protocol'][] = [
  'http', 'grpc', 'kafka', 'rabbitmq', 'redis', 'postgres', 'mysql', 'mariadb', 'mongodb',
]

const serviceNodes = computed(() => (graph.value?.nodes ?? []).filter((n) => n.type === 'service'))
// Targets can be a service OR an infra node (broker / database / cache).
const targetNodes = computed(() => graph.value?.nodes ?? [])
const targetNode = computed(() => targetNodes.value.find((n) => n.id === newTarget.value) ?? null)
// When wiring to a database/broker/cache, the protocol IS the datastore kind - detect
// it automatically and lock the field so it can't be mismatched.
const isInfraTarget = computed(() => Boolean(targetNode.value && targetNode.value.type !== 'service'))

watch(targetNode, (node) => {
  if (node && node.type !== 'service' && protocols.includes(node.label as ServiceConnection['protocol'])) {
    newProtocol.value = node.label as ServiceConnection['protocol']
  }
})
// Only worth showing once there is something to connect: multiple services,
// or a service plus a broker/datastore. A lone single-service node is noise.
const hasGraph = computed(() => (graph.value?.nodes?.length ?? 0) > 1)
const configuredEdges = computed(() => (graph.value?.edges ?? []).filter((e) => e.configured))

onMounted(load)

async function load() {
  loading.value = true
  try {
    graph.value = await getServiceGraph(props.workspaceId)
  } catch {
    graph.value = null // no graph for this workspace (single service / not multi-repo)
  } finally {
    loading.value = false
  }
}

function currentConnections(): ServiceConnection[] {
  return configuredEdges.value.map((e) => ({
    source: e.source,
    target: e.target,
    protocol: e.protocol,
  }))
}

async function persist(connections: ServiceConnection[]) {
  saving.value = true
  try {
    graph.value = await updateServiceConnections(props.workspaceId, connections)
  } catch (err) {
    toast.error(toastError(err, 'Failed to save connections'))
  } finally {
    saving.value = false
  }
}

async function addConnection() {
  if (!newSource.value || !newTarget.value || newSource.value === newTarget.value) return
  await persist([
    ...currentConnections(),
    { source: newSource.value, target: newTarget.value, protocol: newProtocol.value },
  ])
  newSource.value = ''
  newTarget.value = ''
}

async function removeConnection(edge: ServiceGraphEdge) {
  await persist(
    currentConnections().filter(
      (c) => !(c.source === edge.source && c.target === edge.target && c.protocol === edge.protocol),
    ),
  )
}

function nodeLabel(id: string): string {
  return graph.value?.nodes.find((n) => n.id === id)?.label ?? id
}
</script>

<template>
  <section v-if="hasGraph" class="lp-panel space-y-4 p-5">
    <header class="space-y-1">
      <p class="lp-label">{{ t('serviceGraph.title') }}</p>
      <p class="text-sm text-[var(--lp-muted)]">{{ t('serviceGraph.subtitle') }}</p>
    </header>

    <!-- Graph -->
    <div class="graph-canvas overflow-auto rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)] p-3">
      <MermaidDiagram v-if="graph?.mermaid" :code="graph.mermaid" />
    </div>
    <p class="text-xs text-[var(--lp-muted)]">
      {{ t('serviceGraph.legend') }}
    </p>

    <!-- Connections list -->
    <div>
      <p class="lp-label mb-2">{{ t('serviceGraph.connections') }}</p>
      <p v-if="!graph?.edges.length" class="text-sm text-[var(--lp-muted)]">
        {{ t('serviceGraph.none') }}
      </p>
      <ul v-else class="space-y-1">
        <li
          v-for="(edge, i) in graph.edges"
          :key="i"
          class="flex items-center justify-between rounded-md border border-[var(--lp-line)] px-3 py-1.5 text-sm"
        >
          <span class="text-[var(--lp-text)]">
            {{ nodeLabel(edge.source) }}
            <span class="mx-1 text-[var(--lp-muted)]">-{{ edge.protocol }}-&gt;</span>
            {{ nodeLabel(edge.target) }}
            <span
              class="ml-2 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide"
              :class="edge.configured
                ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]'
                : 'bg-[var(--lp-line)] text-[var(--lp-muted)]'"
            >
              {{ edge.configured ? t('serviceGraph.configured') : t('serviceGraph.auto') }}
            </span>
          </span>
          <button
            v-if="edge.configured"
            type="button"
            class="lp-btn-ghost text-xs"
            :disabled="saving"
            @click="removeConnection(edge)"
          >
            {{ t('serviceGraph.remove') }}
          </button>
        </li>
      </ul>
    </div>

    <!-- Add connection -->
    <div class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)] p-4">
      <p class="lp-label mb-2">{{ t('serviceGraph.addConnection') }}</p>
      <div class="flex flex-wrap items-end gap-3">
        <label class="min-w-[9rem] flex-1">
          <span class="lp-label mb-1 block">{{ t('serviceGraph.source') }}</span>
          <select v-model="newSource" class="lp-input w-full">
            <option value="">-</option>
            <option v-for="n in serviceNodes" :key="n.id" :value="n.id">{{ n.label }}</option>
          </select>
        </label>
        <label class="min-w-[7rem]">
          <span class="lp-label mb-1 block">{{ t('serviceGraph.protocol') }}</span>
          <select v-model="newProtocol" class="lp-input w-full" :disabled="isInfraTarget" :title="isInfraTarget ? t('serviceGraph.protocolAuto') : ''">
            <option v-for="p in protocols" :key="p" :value="p">{{ p }}</option>
          </select>
        </label>
        <label class="min-w-[9rem] flex-1">
          <span class="lp-label mb-1 block">{{ t('serviceGraph.target') }}</span>
          <select v-model="newTarget" class="lp-input w-full">
            <option value="">-</option>
            <option v-for="n in targetNodes" :key="n.id" :value="n.id">
              {{ n.label }}<template v-if="n.type !== 'service'"> ({{ n.type }})</template>
            </option>
          </select>
        </label>
        <button
          type="button"
          class="lp-btn-primary"
          :disabled="saving || !newSource || !newTarget || newSource === newTarget"
          @click="addConnection"
        >
          {{ saving ? t('serviceGraph.saving') : t('serviceGraph.add') }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* MermaidDiagram forces the SVG to width:100%, which balloons small graphs. Pin the
   SVG to a modest height (width follows aspect ratio) so the diagram stays compact;
   the canvas scrolls if a large graph overflows. */
.graph-canvas {
  max-height: 17rem;
}
.graph-canvas :deep(figure) {
  border: 0;
  background: transparent;
}
.graph-canvas :deep(figure > div) {
  padding: 0.25rem;
}
.graph-canvas :deep(.technical-mermaid) {
  min-height: 0;
}
.graph-canvas :deep(svg) {
  height: 14rem !important;
  width: auto !important;
  max-width: 100%;
  margin: 0 auto;
  overflow: visible;
}

/* --- Modern node cards: rounded, subtle depth, per-type accent (from classDef) --- */
.graph-canvas :deep(.node) {
  transition: filter 0.15s ease;
}
.graph-canvas :deep(.node rect),
.graph-canvas :deep(.node polygon),
.graph-canvas :deep(.node path) {
  rx: 16px;
  ry: 16px;
  filter: drop-shadow(0 6px 18px rgba(0, 0, 0, 0.5));
}
.graph-canvas :deep(.node:hover) {
  filter: brightness(1.12);
}
.graph-canvas :deep(.nodeLabel),
.graph-canvas :deep(.node .label foreignObject div),
.graph-canvas :deep(.node foreignObject div) {
  font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif !important;
  font-weight: 650 !important;
  font-size: 14px !important;
  letter-spacing: 0.2px;
  line-height: 1.25;
}
.graph-canvas :deep(.node small),
.graph-canvas :deep(.nodeLabel small) {
  display: block;
  margin-top: 3px;
  font-family: 'IBM Plex Mono', ui-monospace, monospace !important;
  font-weight: 500;
  font-size: 9px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  opacity: 0.55;
}

/* --- Connectors: soft neutral lines + matching arrowheads --- */
.graph-canvas :deep(.edgePath path),
.graph-canvas :deep(.flowchart-link) {
  stroke: #46606c !important;
  stroke-width: 1.6px !important;
}
.graph-canvas :deep(.marker),
.graph-canvas :deep(marker path) {
  fill: #46606c !important;
  stroke: #46606c !important;
}

/* --- Protocol labels rendered as accent pills --- */
.graph-canvas :deep(.edgeLabel rect),
.graph-canvas :deep(.edgeLabel .background) {
  fill: transparent !important;
}
.graph-canvas :deep(.edgeLabel foreignObject div),
.graph-canvas :deep(.edgeLabel .label),
.graph-canvas :deep(.edgeLabel p) {
  background: rgba(45, 212, 191, 0.14) !important;
  color: #93e6db !important;
  padding: 1px 9px !important;
  border-radius: 999px !important;
  font-family: 'IBM Plex Mono', ui-monospace, monospace !important;
  font-size: 10px !important;
  letter-spacing: 0.08em;
}
</style>

<script setup lang="ts">
/**
 * Renders a Mermaid diagram on the client only.
 * Intentionally NOT a `.client.vue` file — Nuxt's client-only wrapper SSR-stubs as an
 * empty <div> and races onMounted before the real template/refs exist.
 * SVG is kept in `svgHtml` (v-html) so a status re-render cannot wipe imperative innerHTML.
 */
const props = defineProps<{
  code: string
  title?: string
}>()

const host = ref<HTMLElement | null>(null)
const svgHtml = ref('')
const error = ref<string | null>(null)
const status = ref<'pending' | 'ready' | 'failed'>('pending')

let mermaidReady: Promise<typeof import('mermaid').default> | null = null
let initialized = false

function normalizeDiagram(raw: string): string {
  const lines = raw.replace(/\r\n/g, '\n').split('\n')
  const nonEmpty = lines.filter((line) => line.trim().length > 0)
  if (!nonEmpty.length) return ''
  const indent = Math.min(
    ...nonEmpty.map((line) => line.match(/^(\s*)/)?.[1].length ?? 0),
  )
  return lines
    .map((line) => line.slice(indent))
    .join('\n')
    .trim()
}

async function getMermaid() {
  if (!import.meta.client) {
    throw new Error('Mermaid is browser-only')
  }
  if (!mermaidReady) {
    mermaidReady = import('mermaid').then((mod) => mod.default)
  }
  const mermaid = await mermaidReady
  if (!initialized) {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'dark',
      securityLevel: 'loose',
      fontFamily: 'IBM Plex Mono, ui-monospace, monospace',
      themeVariables: {
        primaryColor: '#1f2a30',
        primaryTextColor: '#e8eef2',
        primaryBorderColor: '#2dd4bf',
        lineColor: '#7a8b94',
        secondaryColor: '#162026',
        tertiaryColor: '#0f161a',
        background: 'transparent',
      },
    })
    initialized = true
  }
  return mermaid
}

function decorateSvg(raw: string): string {
  // Make the SVG fill the panel without a fixed height attribute.
  return raw
    .replace(/\sheight="[^"]*"/, '')
    .replace(/\swidth="[^"]*"/, ' width="100%"')
    .replace('<svg ', '<svg style="max-width:100%;height:auto;display:block;margin:0 auto" ')
}

async function renderDiagram() {
  if (!import.meta.client) return

  const definition = normalizeDiagram(props.code)
  if (!definition) {
    error.value = 'Empty diagram definition'
    status.value = 'failed'
    svgHtml.value = ''
    return
  }

  status.value = 'pending'
  error.value = null

  try {
    const mermaid = await getMermaid()
    const id = `mmd-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
    const { svg, bindFunctions } = await mermaid.render(id, definition)
    svgHtml.value = decorateSvg(svg)
    status.value = 'ready'
    await nextTick()
    if (host.value) {
      bindFunctions?.(host.value)
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Diagram failed to render'
    status.value = 'failed'
    svgHtml.value = ''
  }
}

onMounted(() => {
  void renderDiagram()
})

watch(
  () => props.code,
  () => {
    void renderDiagram()
  },
)
</script>

<template>
  <figure class="lp-glass overflow-hidden rounded-xl">
    <figcaption
      v-if="title"
      class="border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)] px-4 py-2 font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--lp-muted)]"
    >
      {{ title }}
    </figcaption>
    <div class="overflow-x-auto p-4">
      <p
        v-if="status === 'pending'"
        class="mb-2 font-mono text-xs text-[var(--lp-muted)]"
      >
        Rendering diagram…
      </p>
      <p v-if="error" class="mb-3 text-sm text-[var(--lp-danger)]">{{ error }}</p>
      <div
        ref="host"
        class="technical-mermaid min-h-[12rem] w-full text-[var(--lp-text)]"
        aria-hidden="true"
        v-html="svgHtml"
      />
      <pre
        v-if="status === 'failed'"
        class="mt-3 overflow-x-auto rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 p-3 font-mono text-xs leading-5 text-[var(--lp-muted)]"
      >{{ normalizeDiagram(code) }}</pre>
    </div>
  </figure>
</template>

<script setup lang="ts">
import type { WorkspaceFileAnalysisReport } from '~/utils/workspaceFileAnalysis'
import {
  analysisKindLabel,
  detectWorkspaceFileKind,
} from '~/utils/workspaceFileAnalysis'

const props = defineProps<{
  open: boolean
  workspaceId: string
  path: string | null
  content: string
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  apply: [content: string]
  error: [message: string]
}>()

const { analyzeWorkspaceFile } = useProvisioning()

const analyzing = ref(false)
const report = ref<WorkspaceFileAnalysisReport | null>(null)
const kindOverride = ref<'auto' | 'cicd' | 'docker' | 'iac' | 'kubernetes'>('auto')

const detectedKind = computed(() => detectWorkspaceFileKind(props.path))

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      report.value = null
      kindOverride.value = 'auto'
      void runAnalysis()
    }
  },
)

async function runAnalysis() {
  if (!props.path || !props.content.trim()) {
    emit('error', 'Select a file with content to analyze')
    return
  }
  analyzing.value = true
  report.value = null
  try {
    report.value = await analyzeWorkspaceFile(props.workspaceId, {
      path: props.path,
      content: props.content,
      kind: kindOverride.value,
    })
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'AI analysis failed')
    emit('update:open', false)
  } finally {
    analyzing.value = false
  }
}

function close() {
  emit('update:open', false)
}

function applyImproved() {
  if (report.value?.improvedContent) {
    emit('apply', report.value.improvedContent)
  }
}

function severityClass(severity: string): string {
  if (severity === 'critical') return 'text-[var(--lp-danger)]'
  if (severity === 'warning') return 'text-amber-400'
  return 'text-[var(--lp-muted)]'
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[90] flex justify-end bg-black/45"
      @click.self="close"
    >
      <aside class="flex h-full w-full max-w-lg flex-col border-l border-[var(--lp-line)] bg-[var(--lp-panel)] shadow-2xl">
        <header class="flex items-start justify-between gap-3 border-b border-[var(--lp-line)] px-5 py-4">
          <div>
            <p class="text-[11px] uppercase tracking-wide text-[var(--lp-muted)]">AI analysis</p>
            <h3 class="text-base font-semibold text-[var(--lp-text)]">
              {{ path ? path.split('/').pop() : 'Workspace file' }}
            </h3>
            <p class="mt-1 text-[12px] text-[var(--lp-muted)]">
              Detected: {{ analysisKindLabel(detectedKind) }}
              <span v-if="report"> · source {{ report.analysisSource }}</span>
            </p>
          </div>
          <button type="button" class="lp-btn-ghost px-2 py-1 text-[12px]" @click="close">
            Close
          </button>
        </header>

        <div class="flex items-center gap-2 border-b border-[var(--lp-line)] px-5 py-3">
          <select v-model="kindOverride" class="lp-input flex-1 text-xs">
            <option value="auto">Auto-detect domain</option>
            <option value="cicd">CI/CD</option>
            <option value="docker">Docker</option>
            <option value="iac">IaC</option>
            <option value="kubernetes">Kubernetes</option>
          </select>
          <button
            type="button"
            class="lp-btn-primary px-3 py-1.5 text-[12px] disabled:opacity-40"
            :disabled="analyzing || !path || !content.trim()"
            @click="runAnalysis"
          >
            {{ analyzing ? 'Analyzing…' : 'Re-run' }}
          </button>
        </div>

        <div class="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <p v-if="analyzing" class="text-sm text-[var(--lp-muted)]">
            Reviewing {{ analysisKindLabel(kindOverride === 'auto' ? detectedKind : kindOverride) }}…
          </p>

          <template v-else-if="report">
            <section class="space-y-2">
              <h4 class="text-[11px] font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
                Summary
              </h4>
              <p class="text-sm text-[var(--lp-text)]">{{ report.summary }}</p>
            </section>

            <section v-if="report.issues.length" class="space-y-2">
              <h4 class="text-[11px] font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
                Issues
              </h4>
              <ul class="space-y-2">
                <li
                  v-for="(issue, idx) in report.issues"
                  :key="`${issue.title}-${idx}`"
                  class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/50 px-3 py-2"
                >
                  <p class="text-[13px] font-medium text-[var(--lp-text)]">
                    <span :class="severityClass(issue.severity)" class="mr-1 text-[11px] uppercase">
                      {{ issue.severity }}
                    </span>
                    {{ issue.title }}
                  </p>
                  <p class="mt-1 text-[12px] text-[var(--lp-muted)]">{{ issue.description }}</p>
                </li>
              </ul>
            </section>

            <section v-if="report.suggestions.length" class="space-y-2">
              <h4 class="text-[11px] font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
                Suggestions
              </h4>
              <ul class="list-disc space-y-1 pl-5 text-[13px] text-[var(--lp-text)]">
                <li v-for="(tip, idx) in report.suggestions" :key="idx">
                  {{ tip }}
                </li>
              </ul>
            </section>

            <section v-if="report.improvedContent" class="space-y-2">
              <div class="flex items-center justify-between gap-2">
                <h4 class="text-[11px] font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
                  Suggested rewrite
                </h4>
                <button
                  type="button"
                  class="lp-btn-primary px-3 py-1.5 text-[12px]"
                  @click="applyImproved"
                >
                  Apply suggestion
                </button>
              </div>
              <pre class="max-h-64 overflow-auto rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/70 p-3 font-mono text-[11px] text-[var(--lp-text)]">{{ report.improvedContent }}</pre>
            </section>
          </template>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

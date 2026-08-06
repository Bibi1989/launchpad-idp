<script setup lang="ts">
import type { WorkspaceFileAnalysisReport } from '~/utils/workspaceFileAnalysis'
import { analysisKindLabel } from '~/utils/workspaceFileAnalysis'

export interface WorkspaceAnalysisTarget {
  path: string
  content: string
}

export interface WorkspaceFileAnalysisEntry {
  path: string
  content: string
  report: WorkspaceFileAnalysisReport | null
  error: string | null
}

const props = defineProps<{
  open: boolean
  workspaceId: string
  /** Single-file mode (legacy / init wizard). */
  path?: string | null
  content?: string
  /** Multi-file / folder mode - preferred when set. */
  targets?: WorkspaceAnalysisTarget[]
  /** Optional sandbox/CLI failure text for targeted AI fixes. */
  errorContext?: string | null
  /** Force analysis domain (init wizard uses iac). */
  defaultKind?: 'auto' | 'cicd' | 'docker' | 'iac' | 'kubernetes'
  /** Persist rewrite into the workspace; awaited so UI only clears on success. */
  persistFix?: (payload: { path: string; content: string }) => Promise<void> | void
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  apply: [payload: { path: string; content: string }]
  error: [message: string]
}>()

const { analyzeWorkspaceFile } = useProvisioning()
const { t } = useI18n()

const analyzing = ref(false)
const entries = ref<WorkspaceFileAnalysisEntry[]>([])
const kindOverride = ref<'auto' | 'cicd' | 'docker' | 'iac' | 'kubernetes'>('auto')
const activePath = ref<string | null>(null)
const applyingPath = ref<string | null>(null)

const resolvedTargets = computed<WorkspaceAnalysisTarget[]>(() => {
  if (props.targets && props.targets.length > 0) {
    return props.targets.filter((t) => t.path && t.content.trim())
  }
  if (props.path && props.content?.trim()) {
    return [{ path: props.path, content: props.content }]
  }
  return []
})

const isMulti = computed(() => resolvedTargets.value.length > 1)

const activeEntry = computed(() => {
  const path = activePath.value
  if (!path) return entries.value[0] ?? null
  return entries.value.find((e) => e.path === path) ?? entries.value[0] ?? null
})

const issueCount = computed(() =>
  entries.value.reduce((n, e) => n + (e.report?.issues.length ?? 0), 0),
)

const fixableCount = computed(() =>
  entries.value.filter((e) => e.report?.improvedContent).length,
)

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      entries.value = []
      activePath.value = null
      kindOverride.value = props.defaultKind ?? 'auto'
      void runAnalysis()
    }
  },
)

async function runAnalysis() {
  const targets = resolvedTargets.value
  if (!targets.length) {
    emit('error', 'Select a file or folder with analyzable content')
    return
  }
  analyzing.value = true
  entries.value = targets.map((t) => ({
    path: t.path,
    content: t.content,
    report: null,
    error: null,
  }))
  activePath.value = targets[0]?.path ?? null
  try {
    for (let i = 0; i < targets.length; i++) {
      const target = targets[i]
      if (!target) continue
      try {
        const report = await analyzeWorkspaceFile(props.workspaceId, {
          path: target.path,
          content: target.content,
          kind: kindOverride.value,
          error_context: props.errorContext ?? null,
        })
        const entry = entries.value[i]
        if (entry) {
          entry.report = report
          entry.error = null
        }
      } catch (err) {
        const entry = entries.value[i]
        if (entry) {
          entry.error = err instanceof Error ? err.message : 'AI analysis failed'
        }
      }
    }
    const firstWithIssues = entries.value.find(
      (e) => (e.report?.issues.length ?? 0) > 0 || e.report?.improvedContent,
    )
    if (firstWithIssues) activePath.value = firstWithIssues.path
  } finally {
    analyzing.value = false
  }
}

function close() {
  emit('update:open', false)
}

async function applyImproved(entry: WorkspaceFileAnalysisEntry) {
  if (!entry.report?.improvedContent || applyingPath.value) return
  const content = entry.report.improvedContent
  applyingPath.value = entry.path
  try {
    const payload = { path: entry.path, content }
    if (props.persistFix) {
      await props.persistFix(payload)
    } else {
      emit('apply', payload)
    }
    entry.content = content
    entry.report = {
      ...entry.report,
      improvedContent: null,
      summary: `${entry.report.summary} (fix applied to workspace)`,
    }
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'Failed to apply AI fix')
  } finally {
    applyingPath.value = null
  }
}

async function applyAllImproved() {
  const fixable = entries.value.filter((e) => e.report?.improvedContent)
  for (const entry of fixable) {
    await applyImproved(entry)
  }
}

function severityClass(severity: string): string {
  if (severity === 'critical') return 'text-[var(--lp-danger)]'
  if (severity === 'warning') return 'text-amber-400'
  return 'text-[var(--lp-muted)]'
}

function issueCountLabel(count: number): string {
  return count === 1
    ? t('analyzer.workspace.issueCountOne', { count })
    : t('analyzer.workspace.issueCountMany', { count })
}

function reviewingLabel(count: number): string {
  return count === 1
    ? t('analyzer.workspace.reviewingOne', { count })
    : t('analyzer.workspace.reviewingMany', { count })
}

function fixableFooterLabel(count: number): string {
  return count === 1
    ? t('analyzer.workspace.fixableFooterOne', { count })
    : t('analyzer.workspace.fixableFooterMany', { count })
}

function shortName(path: string): string {
  return path.split('/').pop() || path
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[110] flex justify-end bg-black/45"
      @click.self="close"
    >
      <aside class="flex h-full w-full max-w-xl flex-col border-l border-[var(--lp-line)] bg-[var(--lp-panel)] shadow-2xl">
        <header class="flex items-start justify-between gap-3 border-b border-[var(--lp-line)] px-5 py-4">
          <div>
            <p class="text-[11px] uppercase tracking-wide text-[var(--lp-muted)]">{{ t('analyzer.workspace.eyebrow') }}</p>
            <h3 class="text-base font-semibold text-[var(--lp-text)]">
              <template v-if="isMulti">
                {{ t('analyzer.workspace.filesCount', { count: resolvedTargets.length }) }}
              </template>
              <template v-else>
                {{ path ? path.split('/').pop() : (resolvedTargets[0]?.path.split('/').pop() || t('analyzer.workspace.workspaceFile')) }}
              </template>
            </h3>
            <p class="mt-1 text-[12px] text-[var(--lp-muted)]">
              <template v-if="analyzing">{{ t('analyzer.workspace.scanning') }}</template>
              <template v-else-if="entries.length">
                {{ issueCountLabel(issueCount) }}
                <span v-if="fixableCount">{{ t('analyzer.workspace.fixable', { count: fixableCount }) }}</span>
              </template>
            </p>
            <p v-if="errorContext" class="mt-2 text-[11px] text-[var(--lp-warn)]">
              {{ t('analyzer.workspace.sandboxContext') }}
            </p>
          </div>
          <button type="button" class="lp-btn-ghost px-2 py-1 text-[12px]" @click="close">
            {{ t('common.close') }}
          </button>
        </header>

        <div class="flex items-center gap-2 border-b border-[var(--lp-line)] px-5 py-3">
          <select v-model="kindOverride" class="lp-input flex-1 text-xs">
            <option value="auto">{{ t('analyzer.workspace.autoDetect') }}</option>
            <option value="cicd">{{ t('analyzer.workspace.cicd') }}</option>
            <option value="docker">{{ t('analyzer.workspace.docker') }}</option>
            <option value="iac">{{ t('analyzer.workspace.iac') }}</option>
            <option value="kubernetes">{{ t('analyzer.workspace.kubernetes') }}</option>
          </select>
          <button
            type="button"
            class="lp-btn-primary px-3 py-1.5 text-[12px] disabled:opacity-40"
            :disabled="analyzing || !resolvedTargets.length"
            @click="runAnalysis"
          >
            {{ analyzing ? t('analyzer.workspace.analyzing') : t('analyzer.workspace.rerun') }}
          </button>
        </div>

        <div v-if="isMulti" class="max-h-40 shrink-0 overflow-y-auto border-b border-[var(--lp-line)]">
          <button
            v-for="entry in entries"
            :key="entry.path"
            type="button"
            class="flex w-full items-center gap-2 border-b border-[var(--lp-line)]/60 px-4 py-2 text-left text-[12px] transition last:border-b-0 hover:bg-[var(--lp-panel-2)]"
            :class="activePath === entry.path ? 'bg-[var(--lp-panel-2)]' : ''"
            @click="activePath = entry.path"
          >
            <span
              class="h-1.5 w-1.5 shrink-0 rounded-full"
              :class="{
                'bg-[var(--lp-muted)]': analyzing && !entry.report && !entry.error,
                'bg-[var(--lp-danger)]': (entry.report?.issues.some(i => i.severity === 'critical') || !!entry.error),
                'bg-amber-400': !entry.error && entry.report?.issues.some(i => i.severity === 'warning') && !entry.report?.issues.some(i => i.severity === 'critical'),
                'bg-emerald-500': !entry.error && entry.report && !entry.report.issues.length,
              }"
            />
            <span class="min-w-0 flex-1 truncate font-mono text-[var(--lp-text)]" :title="entry.path">
              {{ entry.path }}
            </span>
            <span
              v-if="entry.report?.improvedContent"
              class="shrink-0 text-[10px] uppercase tracking-wide text-[var(--lp-accent)]"
            >
              {{ t('analyzer.workspace.fix') }}
            </span>
            <span v-else-if="entry.report" class="shrink-0 text-[10px] text-[var(--lp-muted)]">
              {{ entry.report.issues.length }}
            </span>
          </button>
        </div>

        <div class="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <p v-if="analyzing && !activeEntry?.report" class="text-sm text-[var(--lp-muted)]">
            {{ reviewingLabel(resolvedTargets.length) }}
          </p>

          <template v-else-if="activeEntry">
            <p v-if="activeEntry.error" class="text-sm text-[var(--lp-danger)]">
              {{ activeEntry.error }}
            </p>

            <template v-else-if="activeEntry.report">
              <section class="space-y-2">
                <div class="flex items-center justify-between gap-2">
                  <h4 class="text-[11px] font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
                    {{ shortName(activeEntry.path) }}
                  </h4>
                  <p class="text-[11px] text-[var(--lp-muted)]">
                    {{ analysisKindLabel(activeEntry.report.kind) }}
                    · {{ activeEntry.report.analysisSource }}
                  </p>
                </div>
                <p class="text-sm text-[var(--lp-text)]">{{ activeEntry.report.summary }}</p>
              </section>

              <section v-if="activeEntry.report.issues.length" class="space-y-2">
                <h4 class="text-[11px] font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
                  {{ t('analyzer.workspace.issues') }}
                </h4>
                <ul class="space-y-2">
                  <li
                    v-for="(issue, idx) in activeEntry.report.issues"
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

              <section v-if="activeEntry.report.suggestions.length" class="space-y-2">
                <h4 class="text-[11px] font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
                  {{ t('analyzer.workspace.suggestions') }}
                </h4>
                <ul class="list-disc space-y-1 pl-5 text-[13px] text-[var(--lp-text)]">
                  <li v-for="(tip, idx) in activeEntry.report.suggestions" :key="idx">
                    {{ tip }}
                  </li>
                </ul>
              </section>

              <section v-if="activeEntry.report.improvedContent" class="space-y-2">
                <div class="flex items-center justify-between gap-2">
                  <h4 class="text-[11px] font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
                    {{ t('analyzer.workspace.suggestedRewrite') }}
                  </h4>
                  <button
                    type="button"
                    class="lp-btn-primary px-3 py-1.5 text-[12px] disabled:opacity-40"
                    :disabled="applyingPath === activeEntry.path"
                    @click="void applyImproved(activeEntry)"
                  >
                    {{ applyingPath === activeEntry.path ? t('common.working') : t('analyzer.workspace.applyFixWorkspace') }}
                  </button>
                </div>
                <pre class="max-h-64 overflow-auto rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/70 p-3 font-mono text-[11px] text-[var(--lp-text)]">{{ activeEntry.report.improvedContent }}</pre>
              </section>

              <div
                v-else
                class="mt-3 flex items-center justify-between gap-3 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 p-3"
              >
                <div class="min-w-0">
                  <p class="truncate text-xs font-semibold text-[var(--lp-text)]">
                    {{ t('analyzer.workspace.targetFile') }} <code class="text-[var(--lp-accent)]">{{ activeEntry.path }}</code>
                  </p>
                  <p class="text-[11px] text-[var(--lp-muted)]">
                    {{ t('analyzer.workspace.targetBlurb') }}
                  </p>
                </div>
                <button
                  type="button"
                  class="lp-btn-primary shrink-0 px-3 py-1.5 text-[12px] disabled:opacity-40"
                  :disabled="applyingPath === activeEntry.path"
                  @click="void applyImproved({ ...activeEntry, report: { ...activeEntry.report, improvedContent: activeEntry.content } as any })"
                >
                  {{ applyingPath === activeEntry.path ? t('common.working') : t('analyzer.workspace.applyFixFile') }}
                </button>
              </div>
            </template>
          </template>
        </div>

        <footer
          v-if="fixableCount > 0 && !analyzing"
          class="flex items-center justify-between gap-3 border-t border-[var(--lp-line)] px-5 py-3"
        >
          <p class="text-[12px] text-[var(--lp-muted)]">
            {{ fixableFooterLabel(fixableCount) }}
          </p>
          <button
            type="button"
            class="lp-btn-primary px-3 py-1.5 text-[12px]"
            :disabled="Boolean(applyingPath)"
            @click="void applyAllImproved()"
          >
            {{ applyingPath ? t('common.working') : t('analyzer.workspace.applyAllFixes') }}
          </button>
        </footer>
      </aside>
    </div>
  </Teleport>
</template>

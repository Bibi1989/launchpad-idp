<script setup lang="ts">
import type { DiagnosticCategory, DiagnosticSeverity } from '~/types/diagnostic'

const props = defineProps<{
  modelValue: boolean
  environmentName?: string | null
  workspaceId?: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  applied: [targetFile: string]
  rejected: []
}>()

const {
  report,
  loading,
  error,
  telemetrySummary,
  clear,
  patchDecision,
  patchBusy,
  patchMessage,
  applyPatch,
  rejectPatch,
} = usePreviewAnalyzer()

const { t } = useI18n()

const patchCopied = ref(false)
const applyError = ref<string | null>(null)

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const canDecidePatch = computed(
  () => Boolean(report.value?.patch) && patchDecision.value === 'pending',
)

const canApplyToWorkspace = computed(() => Boolean(props.workspaceId))

function close() {
  visible.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && visible.value) {
    close()
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  document.body.classList.remove('overflow-hidden')
})

watch(visible, (isOpen) => {
  if (isOpen) {
    document.body.classList.add('overflow-hidden')
  } else {
    document.body.classList.remove('overflow-hidden')
  }
})

function categoryLabel(category: DiagnosticCategory): string {
  switch (category) {
    case 'CONTAINER_VULNERABILITY':
      return t('analyzer.preview.categories.containerVuln')
    case 'SAST_CODE_SECURITY':
      return t('analyzer.preview.categories.sast')
    case 'RUNTIME_CRASH':
      return t('analyzer.preview.categories.runtimeCrash')
    case 'CONFIGURATION_ERROR':
      return t('analyzer.preview.categories.configError')
    default:
      return category
  }
}

function severityClass(severity: DiagnosticSeverity): string {
  switch (severity) {
    case 'CRITICAL':
      return 'border-[var(--lp-danger)]/50 bg-[var(--lp-danger)]/15 text-[var(--lp-danger)]'
    case 'HIGH':
      return 'border-[var(--lp-warn)]/50 bg-[var(--lp-warn)]/15 text-[var(--lp-warn)]'
    case 'MEDIUM':
      return 'border-[var(--lp-accent)]/40 bg-[var(--lp-accent)]/10 text-[var(--lp-accent)]'
    default:
      return 'border-[var(--lp-line)] bg-[var(--lp-panel-2)] text-[var(--lp-muted)]'
  }
}

const patchDiff = computed(() => {
  if (!report.value?.patch) return null
  const { targetFile, originalContent, suggestedContent } = report.value.patch
  return { targetFile, originalContent, suggestedContent }
})

async function copySuggestedPatch() {
  if (!patchDiff.value) return
  const text = [
    `# ${patchDiff.value.targetFile}`,
    '--- original',
    patchDiff.value.originalContent,
    '+++ suggested',
    patchDiff.value.suggestedContent,
  ].join('\n')
  try {
    await navigator.clipboard.writeText(text)
    patchCopied.value = true
    setTimeout(() => {
      patchCopied.value = false
    }, 2000)
  } catch {
    // ignore clipboard failures
  }
}

async function onApplyFix() {
  applyError.value = null
  if (!props.workspaceId) {
    applyError.value = 'Link a workspace to this preview to apply the fix to files.'
    return
  }
  if (!patchDiff.value) return
  try {
    await applyPatch(props.workspaceId)
    emit('applied', patchDiff.value.targetFile)
  } catch (err) {
    applyError.value = err instanceof Error ? err.message : 'Failed to apply fix'
  }
}

function onRejectFix() {
  applyError.value = null
  rejectPatch()
  emit('rejected')
}

function onClear() {
  clear()
  applyError.value = null
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed inset-0 z-50 flex justify-end"
      role="dialog"
      aria-modal="true"
      aria-labelledby="preview-analyzer-title"
    >
      <button
        type="button"
        class="absolute inset-0 bg-black/50"
        :aria-label="t('analyzer.preview.closeLabel')"
        @click="close"
      />
      <aside
        class="relative flex h-full w-full max-w-xl flex-col border-l border-[var(--lp-line)] bg-[var(--lp-panel)] shadow-2xl animate-fade-up"
      >
        <header class="flex items-start justify-between gap-3 border-b border-[var(--lp-line)] px-5 py-4">
          <div class="space-y-1">
            <p class="font-mono text-[10px] uppercase tracking-wider text-[var(--lp-accent)]">
              {{ t('analyzer.preview.eyebrow') }}
            </p>
            <h2 id="preview-analyzer-title" class="text-lg font-semibold tracking-tight">
              {{ t('analyzer.preview.title') }}
            </h2>
            <p v-if="environmentName" class="font-mono text-xs text-[var(--lp-muted)]">
              {{ environmentName }}
            </p>
          </div>
          <button type="button" class="lp-btn-ghost px-2 py-1" @click="close">
            <span class="material-symbols-outlined text-base">close</span>
          </button>
        </header>

        <div class="flex-1 space-y-5 overflow-y-auto px-5 py-5">
          <p v-if="loading" class="text-sm text-[var(--lp-muted)]">
            {{ t('analyzer.preview.loading') }}
          </p>
          <p v-else-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>

          <template v-else-if="report">
            <div class="flex flex-wrap items-center gap-2">
              <span
                class="rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide"
                :class="severityClass(report.severity)"
              >
                {{ report.severity }}
              </span>
              <span class="rounded-md border border-[var(--lp-line)] px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
                {{ categoryLabel(report.category) }}
              </span>
              <span
                v-if="report.analysisSource"
                class="rounded-md border border-[var(--lp-line)] px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]"
              >
                {{ report.analysisSource }}
              </span>
            </div>

            <section class="space-y-2">
              <h3 class="lp-label">{{ t('analyzer.preview.summary') }}</h3>
              <p class="text-sm leading-relaxed text-[var(--lp-text)]">{{ report.summary }}</p>
            </section>

            <section
              v-if="report.securityDetails"
              class="space-y-3 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 p-4"
            >
              <h3 class="lp-label">{{ t('analyzer.preview.securityDetails') }}</h3>
              <dl class="grid gap-3 text-sm">
                <div>
                  <dt class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">{{ t('analyzer.preview.cveRule') }}</dt>
                  <dd class="mt-0.5 font-mono text-xs text-[var(--lp-accent)]">
                    {{ report.securityDetails.cveOrRuleId }}
                  </dd>
                </div>
                <div>
                  <dt class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">{{ t('analyzer.preview.affected') }}</dt>
                  <dd class="mt-0.5 break-all font-mono text-xs">
                    {{ report.securityDetails.affectedComponent }}
                  </dd>
                </div>
                <div>
                  <dt class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">{{ t('analyzer.preview.recommendedFix') }}</dt>
                  <dd class="mt-0.5 text-sm">{{ report.securityDetails.recommendedUpgrade }}</dd>
                </div>
              </dl>
            </section>

            <section class="space-y-2">
              <h3 class="lp-label">{{ t('analyzer.preview.rootCause') }}</h3>
              <p class="text-sm leading-relaxed text-[var(--lp-muted)]">
                {{ report.rootCauseAnalysis }}
              </p>
            </section>

            <section class="space-y-2">
              <h3 class="lp-label">{{ t('analyzer.preview.actionableSteps') }}</h3>
              <ol class="list-decimal space-y-2 pl-5 text-sm text-[var(--lp-text)]">
                <li v-for="(step, idx) in report.actionableSteps" :key="idx">
                  {{ step }}
                </li>
              </ol>
            </section>

            <section v-if="patchDiff" class="space-y-3">
              <div class="flex items-center justify-between gap-2">
                <h3 class="lp-label mb-0">{{ t('analyzer.preview.proposedFix') }}</h3>
                <button type="button" class="lp-btn-ghost py-1 text-xs" @click="copySuggestedPatch">
                  <span class="material-symbols-outlined text-sm">content_copy</span>
                  {{ patchCopied ? t('common.copied') : t('analyzer.preview.copyDiff') }}
                </button>
              </div>
              <p class="font-mono text-xs text-[var(--lp-accent)]">{{ patchDiff.targetFile }}</p>
              <div class="overflow-hidden rounded-xl border border-[var(--lp-line)]">
                <pre class="max-h-40 overflow-auto border-b border-[var(--lp-line)] bg-[var(--lp-danger)]/10 p-3 font-mono text-[11px] leading-5 text-[var(--lp-danger)]"><code>- {{ patchDiff.originalContent }}</code></pre>
                <pre class="max-h-40 overflow-auto bg-[var(--lp-ok)]/10 p-3 font-mono text-[11px] leading-5 text-[var(--lp-ok)]"><code>+ {{ patchDiff.suggestedContent }}</code></pre>
              </div>

              <div
                v-if="canDecidePatch"
                class="space-y-3 rounded-xl border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/5 p-4"
              >
                <p class="text-sm text-[var(--lp-text)]">
                  {{ t('analyzer.preview.applyPrompt') }}
                </p>
                <p v-if="!canApplyToWorkspace" class="text-xs text-[var(--lp-muted)]">
                  {{ t('analyzer.preview.noWorkspace') }}
                </p>
                <div class="flex flex-wrap gap-2">
                  <button
                    type="button"
                    class="lp-btn-primary text-xs"
                    :disabled="patchBusy || !canApplyToWorkspace"
                    @click="onApplyFix"
                  >
                    <span class="material-symbols-outlined text-sm">check</span>
                    {{ patchBusy ? t('common.working') : t('analyzer.preview.applyFix') }}
                  </button>
                  <button
                    type="button"
                    class="lp-btn-ghost text-xs"
                    :disabled="patchBusy"
                    @click="onRejectFix"
                  >
                    <span class="material-symbols-outlined text-sm">close</span>
                    {{ t('analyzer.preview.reject') }}
                  </button>
                </div>
              </div>

              <p
                v-if="patchDecision === 'applied'"
                class="rounded-lg border border-[var(--lp-ok)]/40 bg-[var(--lp-ok)]/10 px-3 py-2 text-sm text-[var(--lp-ok)]"
              >
                {{ patchMessage || t('analyzer.preview.fixApplied') }}
                <NuxtLink
                  v-if="workspaceId"
                  :to="`/workspaces/${workspaceId}`"
                  class="ml-1 underline"
                >
                  {{ t('analyzer.preview.openWorkspace') }}
                </NuxtLink>
              </p>
              <p
                v-else-if="patchDecision === 'rejected'"
                class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 px-3 py-2 text-sm text-[var(--lp-muted)]"
              >
                {{ patchMessage || t('analyzer.preview.fixRejected') }}
              </p>
              <p v-if="applyError" class="text-sm text-[var(--lp-danger)]">{{ applyError }}</p>
            </section>

            <section
              v-if="Object.keys(telemetrySummary).length"
              class="space-y-2 border-t border-[var(--lp-line)] pt-4"
            >
              <h3 class="lp-label">{{ t('analyzer.preview.telemetry') }}</h3>
              <p class="font-mono text-[11px] text-[var(--lp-muted)]">
                sources={{ Array.isArray(telemetrySummary.sourceKinds) ? telemetrySummary.sourceKinds.join(', ') : '-' }}
                · trivy={{ telemetrySummary.trivyCount ?? 0 }}
                · sast={{ telemetrySummary.sastCount ?? 0 }}
                · runtime={{ telemetrySummary.runtimeSignalCount ?? 0 }}
              </p>
            </section>
          </template>

          <p v-else class="text-sm text-[var(--lp-muted)]">
            {{ t('analyzer.preview.empty') }}
          </p>
        </div>

        <footer class="flex items-center justify-between gap-2 border-t border-[var(--lp-line)] px-5 py-3">
          <button type="button" class="lp-btn-ghost text-xs" :disabled="!report" @click="onClear">
            {{ t('common.clear') }}
          </button>
          <button type="button" class="lp-btn-primary text-xs" @click="close">
            {{ t('workspaceIde.initModal.done') }}
          </button>
        </footer>
      </aside>
    </div>
  </Teleport>
</template>

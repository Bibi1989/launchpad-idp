<script setup lang="ts">
import type { CloudProvider, ProvisionEngine } from '~/types/provisioning'
import {
  enabledCloudServices,
  type CloudServiceOption,
} from '~/utils/cloudServiceOptions'
import {
  iacDestroyWizardSteps,
  iacInitWizardSteps,
} from '~/utils/workspaceInfraScaffold'

const props = defineProps<{
  open: boolean
  workspaceId: string
  engine: ProvisionEngine
  /** provision = init→validate→plan→apply; destroy = tear down */
  mode?: 'provision' | 'destroy'
  terminalReady?: boolean
  sandboxWarming?: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  run: [command: string]
  openTerminal: []
  ensureSandbox: []
  restartSandbox: []
  saved: []
  error: [message: string]
}>()

const { t } = useI18n()

const { getWizardConfig, updateWorkspace, listWorkspaceFiles, readWorkspaceFile, writeWorkspaceFile, enableCloudApis } =
  useProvisioning()
const { runGuarded, terminalConnected, resetResult } = useGuardedTerminalCommand()

type WizardPhase = 'review' | 'credentials' | 'run' | 'done'
type StepStatus = 'pending' | 'running' | 'ok' | 'error'
type CredChoice = 'stored' | 'new'

const loading = ref(false)
const savingCreds = ref(false)
const running = ref(false)
const askingAi = ref(false)
const applyingAi = ref(false)
const showAiFix = ref(false)
const phase = ref<WizardPhase>('review')
const stepIndex = ref(0)
const stepStatuses = ref<StepStatus[]>([])
const provider = ref<CloudProvider>('local')
const hasStoredCredentials = ref(false)
const credentialLabel = ref<string | null>(null)
const credChoice = ref<CredChoice>('stored')
const selectedServices = ref<CloudServiceOption[]>([])
const statusNote = ref<string | null>(null)
const fieldError = ref<string | null>(null)
const sandboxNote = ref<string | null>(null)
const aiTargetPath = ref<string | null>(null)
const aiTargetContent = ref('')
const aiErrorContext = ref<string | null>(null)
const savedNewCredentials = ref(false)

const credentials = reactive({
  gcp_sa_key_json: '',
  gcp_wif_project_number: '',
  gcp_wif_pool_id: '',
  gcp_wif_provider_id: '',
  gcp_wif_target_sa_email: '',
  aws_access_key_id: '',
  aws_secret_access_key: '',
  aws_session_token: '',
  aws_role_arn: '',
  aws_role_session_name: '',
  azure_client_id: '',
  azure_client_secret: '',
  azure_tenant_id: '',
  azure_subscription_id: '',
  cloudflare_api_token: '',
})

const isDestroy = computed(() => props.mode === 'destroy')
const steps = computed(() =>
  isDestroy.value
    ? iacDestroyWizardSteps(props.engine)
    : iacInitWizardSteps(props.engine, {
        enableGcpApis: provider.value === 'gcp',
      }),
)
const currentStep = computed(() => steps.value[stepIndex.value] ?? null)
const engineLabel = computed(() => t(`workspaceIde.engines.${props.engine}`))
const needsCredentials = computed(() => provider.value !== 'local')
const modalTitle = computed(() => {
  if (props.engine === 'ansible' && !isDestroy.value) {
    return t('workspaceIde.initModal.ansibleTitle', {
      cloud: (provider.value || 'local').toUpperCase(),
    })
  }
  return isDestroy.value
    ? t('workspaceIde.initModal.destroyTitle', { engine: engineLabel.value })
    : t('workspaceIde.initModal.provisionTitle', { engine: engineLabel.value })
})
const modalBlurb = computed(() => {
  if (props.engine === 'ansible' && !isDestroy.value) {
    return t('workspaceIde.initModal.ansibleBlurb', {
      cloud: (provider.value || 'local').toUpperCase(),
    })
  }
  return isDestroy.value
    ? t('workspaceIde.initModal.destroyBlurb')
    : t('workspaceIde.initModal.provisionBlurb')
})
const credentialKindLabel = computed(() => {
  switch (provider.value) {
    case 'gcp':
      return 'GCP SA JSON or WIF'
    case 'aws':
      return 'AWS keys or role ARN'
    case 'azure':
      return 'Azure service principal'
    case 'cloudflare':
      return 'Cloudflare API token'
    default:
      return 'Cloud credentials'
  }
})
const progressPct = computed(() => {
  if (phase.value === 'review') return 15
  if (phase.value === 'credentials') return 35
  if (phase.value === 'done') return 100
  const total = Math.max(steps.value.length, 1)
  return Math.min(95, 40 + Math.round(((stepIndex.value + 1) / total) * 55))
})

function resetCredentials() {
  credentials.gcp_sa_key_json = ''
  credentials.gcp_wif_project_number = ''
  credentials.gcp_wif_pool_id = ''
  credentials.gcp_wif_provider_id = ''
  credentials.gcp_wif_target_sa_email = ''
  credentials.aws_access_key_id = ''
  credentials.aws_secret_access_key = ''
  credentials.aws_session_token = ''
  credentials.aws_role_arn = ''
  credentials.aws_role_session_name = ''
  credentials.azure_client_id = ''
  credentials.azure_client_secret = ''
  credentials.azure_tenant_id = ''
  credentials.azure_subscription_id = ''
  credentials.cloudflare_api_token = ''
}

function resetStepStatuses() {
  stepStatuses.value = steps.value.map(() => 'pending')
}

function hasNewCredentialInput(): boolean {
  return Object.values(credentials).some((v) => typeof v === 'string' && v.trim().length > 0)
}

async function loadWizard() {
  loading.value = true
  fieldError.value = null
  statusNote.value = null
  savedNewCredentials.value = false
  sandboxNote.value = 'Starting sandbox in the background…'
  resetResult()
  emit('ensureSandbox')
  try {
    const config = await getWizardConfig(props.workspaceId)
    provider.value = config.cloud.provider
    hasStoredCredentials.value = config.has_credentials
    credentialLabel.value = config.credential_label ?? null
    credChoice.value = config.has_credentials ? 'stored' : 'new'
    selectedServices.value = enabledCloudServices(
      config.cloud.provider,
      config.cloud.resources as Record<string, unknown>,
    )
    phase.value = isDestroy.value && !needsCredentials.value ? 'run' : 'review'
    if (isDestroy.value && needsCredentials.value) {
      phase.value = 'credentials'
    }
    stepIndex.value = 0
    resetCredentials()
    resetStepStatuses()
  } catch (err) {
    fieldError.value = err instanceof Error ? err.message : 'Failed to load workspace config'
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.open, props.mode] as const,
  ([isOpen]) => {
    if (isOpen) void loadWizard()
  },
)

watch(
  [() => props.terminalReady, terminalConnected, () => props.sandboxWarming],
  ([ready, connected, warming]) => {
    if (!props.open) return
    if (warming) {
      sandboxNote.value = 'Starting sandbox in the background…'
      return
    }
    if (connected || ready) {
      sandboxNote.value = 'Sandbox ready (running in background)'
      return
    }
    sandboxNote.value = 'Waiting for sandbox connection…'
  },
)

function close() {
  emit('update:open', false)
}

function goBackFromRun() {
  if (running.value) return
  if (needsCredentials.value) {
    phase.value = 'credentials'
    return
  }
  if (isDestroy.value) {
    close()
    return
  }
  phase.value = 'review'
}

function goNextFromReview() {
  fieldError.value = null
  if (needsCredentials.value) {
    phase.value = 'credentials'
    return
  }
  phase.value = 'run'
  stepIndex.value = 0
}

async function persistCredentialsIfNeeded(): Promise<boolean> {
  if (!needsCredentials.value) return true

  if (credChoice.value === 'stored') {
    if (!hasStoredCredentials.value) {
      fieldError.value = `No ${credentialKindLabel.value} saved yet. Add one to continue.`
      return false
    }
    return true
  }

  if (!hasNewCredentialInput()) {
    fieldError.value = `Paste a ${credentialKindLabel.value} to continue.`
    return false
  }

  const config = await getWizardConfig(props.workspaceId)
  const payload = {
    name: config.name,
    iac_engine: config.iac_engine,
    run_init: config.run_init,
    artifact_mode: config.artifact_mode,
    kubernetes_packaging: config.kubernetes_packaging,
    kubernetes_options: config.kubernetes_options,
    cost_optimization: config.cost_optimization,
    container_scaffold: config.container_scaffold,
    dependencies: config.dependencies,
    credentials: { ...credentials },
    provider: config.cloud.provider,
    resources: config.cloud.resources,
  }
  await updateWorkspace(props.workspaceId, payload as never)
  hasStoredCredentials.value = true
  savedNewCredentials.value = true
  // Refresh label from API (safe client_email / key suffix).
  const refreshed = await getWizardConfig(props.workspaceId)
  credentialLabel.value = refreshed.credential_label ?? credentialKindLabel.value
  credChoice.value = 'stored'
  resetCredentials()
  emit('saved')
  emit('restartSandbox')
  statusNote.value = 'Cloud key saved for this workspace'
  return true
}

async function saveCredentialsAndContinue() {
  fieldError.value = null
  savingCreds.value = true
  try {
    const ok = await persistCredentialsIfNeeded()
    if (!ok) return
    phase.value = 'run'
    stepIndex.value = 0
    resetStepStatuses()
  } catch (err) {
    fieldError.value = err instanceof Error ? err.message : 'Failed to save credentials'
    emit('error', fieldError.value)
  } finally {
    savingCreds.value = false
  }
}

async function runCurrentStep(): Promise<boolean> {
  const step = currentStep.value
  if (!step || running.value) return false
  running.value = true
  fieldError.value = null
  statusNote.value = null
  stepStatuses.value[stepIndex.value] = 'running'
  try {
    if (step.action === 'enable_gcp_apis') {
      const result = await enableCloudApis(props.workspaceId)
      stepStatuses.value[stepIndex.value] = 'ok'
      statusNote.value = result.message || 'Required Google APIs enabled'
      return true
    }

    if (!props.terminalReady && !terminalConnected.value) {
      emit('ensureSandbox')
    }
    const outcome = await runGuarded(step.command, { timeoutMs: 900_000 })
    if (outcome.status !== 'ok') {
      stepStatuses.value[stepIndex.value] = 'error'
      fieldError.value = outcome.message
      emit('error', outcome.message)
      return false
    }
    stepStatuses.value[stepIndex.value] = 'ok'
    statusNote.value = `${step.label} completed (exit 0)`
    return true
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Step failed'
    stepStatuses.value[stepIndex.value] = 'error'
    fieldError.value = message
    emit('error', message)
    return false
  } finally {
    running.value = false
  }
}

function advanceAfterStep() {
  if (stepIndex.value + 1 >= steps.value.length) {
    phase.value = 'done'
    return
  }
  stepIndex.value += 1
  statusNote.value = null
  fieldError.value = null
}

async function runAndAdvance() {
  const ok = await runCurrentStep()
  if (!ok) return
  advanceAfterStep()
}

function retryCurrentStep() {
  fieldError.value = null
  void runCurrentStep()
}

async function resolveIacTargetFile(): Promise<{ path: string; content: string } | null> {
  const nodes = await listWorkspaceFiles(props.workspaceId)
  const paths = nodes.filter((n) => n.type === 'file').map((n) => n.path)

  if (fieldError.value) {
    const errText = fieldError.value
    const matchedPath = paths.find((p) => {
      const sub = p.replace(/^infra\/terraform\//, '')
      return errText.includes(p) || (sub.length > 3 && errText.includes(sub))
    })
    if (matchedPath) {
      const file = await readWorkspaceFile(props.workspaceId, matchedPath)
      return { path: matchedPath, content: file.content }
    }
  }

  let candidate: string | undefined
  if (props.engine === 'pulumi') {
    candidate =
      paths.find((p) => p === 'infra/pulumi/index.ts')
      || paths.find((p) => p.endsWith('/Pulumi.yaml') || p === 'infra/pulumi/Pulumi.yaml')
      || paths.find((p) => p.includes('infra/pulumi/') && p.endsWith('.ts'))
  } else {
    candidate =
      paths.find((p) => p === 'infra/terraform/main.tf')
      || paths.find((p) => p.startsWith('infra/terraform/') && p.endsWith('.tf'))
      || paths.find((p) => p.endsWith('.tf'))
  }
  if (!candidate) return null
  const file = await readWorkspaceFile(props.workspaceId, candidate)
  return { path: candidate, content: file.content }
}

async function askAiToFix() {
  if (!fieldError.value || askingAi.value) return
  askingAi.value = true
  try {
    const target = await resolveIacTargetFile()
    if (!target) {
      fieldError.value =
        `${fieldError.value}\n\nNo IaC source file found to analyze. Add infra/terraform or infra/pulumi files first.`
      return
    }
    aiTargetPath.value = target.path
    aiTargetContent.value = target.content
    aiErrorContext.value = fieldError.value
    showAiFix.value = true
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'Failed to load IaC file for AI fix')
  } finally {
    askingAi.value = false
  }
}

async function applyAiFix(payload: { path: string; content: string }) {
  const targetPath = payload.path || aiTargetPath.value
  if (!targetPath || applyingAi.value) {
    throw new Error('No IaC file selected for AI fix')
  }
  applyingAi.value = true
  try {
    await writeWorkspaceFile(props.workspaceId, targetPath, payload.content)
    showAiFix.value = false
    statusNote.value = `Applied AI fix to ${targetPath}. Retry the failed step.`
    fieldError.value = null
    stepStatuses.value[stepIndex.value] = 'pending'
  } finally {
    applyingAi.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[90] flex items-center justify-center bg-[var(--lp-ink)]/55 p-4"
      @click.self="close"
    >
      <div class="w-full max-w-lg space-y-5 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5 shadow-2xl">
        <header class="space-y-2">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h2 class="text-lg font-semibold">{{ modalTitle }}</h2>
              <p class="mt-1 text-sm text-[var(--lp-muted)]">{{ modalBlurb }}</p>
            </div>
            <button type="button" class="lp-btn-ghost px-2 py-1 text-xs" @click="close">
              {{ t('common.close') }}
            </button>
          </div>
          <div class="h-1.5 overflow-hidden rounded-full bg-[var(--lp-line)]">
            <div
              class="h-full rounded-full bg-[var(--lp-accent)] transition-all duration-300"
              :style="{ width: `${progressPct}%` }"
            />
          </div>
        </header>

        <p v-if="loading" class="text-sm text-[var(--lp-muted)]">{{ t('workspaceIde.initModal.loadingConfig') }}</p>
        <p v-else-if="sandboxNote" class="text-xs text-[var(--lp-muted)]">
          <span class="material-symbols-outlined align-middle text-sm">terminal</span>
          {{ sandboxNote }}
        </p>
        <pre
          v-if="fieldError"
          class="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/10 p-3 text-xs text-[var(--lp-danger)]"
        >{{ fieldError }}</pre>
        <p v-else-if="statusNote" class="text-sm text-[var(--lp-ok)]">{{ statusNote }}</p>

        <template v-if="!loading">
          <!-- REVIEW (provision only) -->
          <div v-if="phase === 'review' && !isDestroy" class="space-y-4">
            <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 p-4 text-sm">
              <p class="lp-label">Engine / cloud</p>
              <p class="mt-1 font-mono text-xs uppercase">
                {{ engineLabel }} · {{ provider }}
              </p>
            </div>
            <div class="space-y-2">
              <p class="lp-label">Services in selected IaC</p>
              <p v-if="provider === 'local'" class="text-sm text-[var(--lp-muted)]">
                Local Sandbox - no cloud managed services.
              </p>
              <p v-else-if="!selectedServices.length" class="text-sm text-[var(--lp-muted)]">
                No managed cloud services enabled. Add them under Update workspace → Edit service resources.
              </p>
              <ul v-else class="flex flex-wrap gap-1.5">
                <li
                  v-for="svc in selectedServices"
                  :key="svc.key"
                  class="rounded-full border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/10 px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-accent)]"
                >
                  {{ svc.title }}
                </li>
              </ul>
            </div>
            <div class="flex justify-end gap-2">
              <button type="button" class="lp-btn-ghost text-xs uppercase tracking-wide" @click="close">
                {{ t('common.cancel') }}
              </button>
              <button
                type="button"
                class="lp-btn-primary text-xs uppercase tracking-wide"
                @click="goNextFromReview"
              >
                {{ t('common.continue') }}
              </button>
            </div>
          </div>

          <!-- CREDENTIALS -->
          <div v-else-if="phase === 'credentials'" class="space-y-4">
            <div class="space-y-2">
              <p class="lp-label">{{ credentialKindLabel }}</p>
              <p class="text-xs text-[var(--lp-muted)]">
                Keys are encrypted on this workspace. Select a saved key or add a new one for
                {{ provider.toUpperCase() }}.
              </p>
            </div>

            <div class="space-y-2">
              <label
                v-if="hasStoredCredentials"
                class="flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-3 transition"
                :class="
                  credChoice === 'stored'
                    ? 'border-[var(--lp-accent)]/50 bg-[var(--lp-accent)]/10'
                    : 'border-[var(--lp-line)] hover:border-[var(--lp-accent)]/30'
                "
              >
                <input v-model="credChoice" type="radio" value="stored" class="mt-1">
                <span class="min-w-0">
                  <span class="block text-sm font-medium text-[var(--lp-text)]">Use saved key</span>
                  <span class="mt-0.5 block break-all font-mono text-[11px] text-[var(--lp-accent)]">
                    {{ credentialLabel || credentialKindLabel }}
                  </span>
                </span>
              </label>

              <label
                class="flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-3 transition"
                :class="
                  credChoice === 'new'
                    ? 'border-[var(--lp-accent)]/50 bg-[var(--lp-accent)]/10'
                    : 'border-[var(--lp-line)] hover:border-[var(--lp-accent)]/30'
                "
              >
                <input v-model="credChoice" type="radio" value="new" class="mt-1">
                <span class="min-w-0">
                  <span class="block text-sm font-medium text-[var(--lp-text)]">
                    {{ hasStoredCredentials ? 'Replace with a new key' : 'Add a cloud key' }}
                  </span>
                  <span class="mt-0.5 block text-[11px] text-[var(--lp-muted)]">
                    Paste credentials below - they are encrypted at rest for this workspace.
                  </span>
                </span>
              </label>
            </div>

            <div v-if="credChoice === 'new'" class="space-y-3">
              <CloudCredentialsFields
                v-if="provider !== 'local'"
                v-model:credentials="credentials"
                :provider="(provider as 'gcp' | 'aws' | 'azure' | 'cloudflare')"
              />
            </div>

            <div class="flex justify-between gap-2">
              <button
                type="button"
                class="lp-btn-ghost text-xs uppercase tracking-wide"
                @click="isDestroy ? close() : (phase = 'review')"
              >
                {{ isDestroy ? t('common.cancel') : t('common.back') }}
              </button>
              <button
                type="button"
                class="lp-btn-primary text-xs uppercase tracking-wide"
                :disabled="savingCreds"
                @click="saveCredentialsAndContinue"
              >
                {{
                  savingCreds
                    ? t('common.saving')
                    : isDestroy
                      ? t('workspaceIde.initModal.continueToDestroy')
                      : props.engine === 'ansible'
                        ? t('workspaceIde.initModal.continueToAnsible')
                        : t('workspaceIde.initModal.continueToProvision')
                }}
              </button>
            </div>
          </div>

          <!-- RUN STEPS -->
          <div v-else-if="phase === 'run' && currentStep" class="space-y-4">
            <div
              v-if="isDestroy"
              class="rounded-lg border border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/10 px-3 py-2 text-xs text-[var(--lp-danger)]"
            >
              This will destroy cloud resources managed by {{ engineLabel }} in this workspace.
              Confirm only if you intend to tear them down.
            </div>
            <div
              class="rounded-xl border p-4"
              :class="
                stepStatuses[stepIndex] === 'error'
                  ? 'border-[var(--lp-danger)]/40'
                  : 'border-[var(--lp-line)]'
              "
            >
              <p class="font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]">
                Step {{ stepIndex + 1 }} / {{ steps.length }}
                <span v-if="running" class="text-[var(--lp-accent)]"> · running…</span>
              </p>
              <h3 class="mt-1 text-base font-semibold capitalize">{{ currentStep.label }}</h3>
              <p v-if="currentStep.description" class="mt-1 text-sm text-[var(--lp-muted)]">
                {{ currentStep.description }}
              </p>
              <p
                v-if="currentStep.action === 'enable_gcp_apis'"
                class="mt-2 text-[11px] text-[var(--lp-ok)]"
              >
                Calls Launchpad control plane → Google Service Usage (no terminal command).
              </p>
              <div
                v-else-if="currentStep.command"
                class="mt-2.5 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 p-2.5 font-mono text-xs"
              >
                <div class="flex items-center gap-2 font-semibold text-[var(--lp-accent)]">
                  <span class="material-symbols-outlined text-base">terminal</span>
                  <span>
                    {{
                      isDestroy
                        ? 'Destroying managed cloud resources…'
                        : currentStep.label === 'apply'
                          ? 'Applying Terraform plan to cloud…'
                          : currentStep.label === 'plan'
                            ? 'Generating Terraform execution plan…'
                            : currentStep.label === 'validate'
                              ? 'Validating Terraform configuration…'
                              : currentStep.label === 'init'
                                ? 'Initializing Terraform working directory…'
                                : `Executing ${currentStep.label}…`
                    }}
                  </span>
                </div>
                <details class="mt-1.5 text-[10px] text-[var(--lp-muted)]">
                  <summary class="cursor-pointer hover:text-[var(--lp-text)]">Show shell command details</summary>
                  <code class="mt-1 block break-all text-[var(--lp-muted)]">{{ currentStep.command }}</code>
                </details>
              </div>
              <ol class="mt-3 space-y-1.5">
                <li
                  v-for="(s, idx) in steps"
                  :key="s.id"
                  class="flex items-center gap-2 text-xs"
                  :class="{
                    'text-[var(--lp-ok)]': stepStatuses[idx] === 'ok',
                    'text-[var(--lp-danger)]': stepStatuses[idx] === 'error',
                    'text-[var(--lp-accent)]': stepStatuses[idx] === 'running' || idx === stepIndex,
                    'text-[var(--lp-muted)]': stepStatuses[idx] === 'pending' && idx !== stepIndex,
                  }"
                >
                  <span class="material-symbols-outlined text-sm">
                    {{
                      stepStatuses[idx] === 'ok'
                        ? 'check_circle'
                        : stepStatuses[idx] === 'error'
                          ? 'error'
                          : stepStatuses[idx] === 'running'
                            ? 'progress_activity'
                            : idx === stepIndex
                              ? 'radio_button_checked'
                              : 'radio_button_unchecked'
                    }}
                  </span>
                  <span>
                    <span class="capitalize">{{ s.label }}</span>
                    <span v-if="s.description" class="ml-1 text-[var(--lp-muted)]">- {{ s.description }}</span>
                  </span>
                </li>
              </ol>
            </div>
            <div class="flex flex-wrap justify-between gap-2">
              <button
                type="button"
                class="lp-btn-ghost text-xs uppercase tracking-wide"
                :disabled="running"
                @click="goBackFromRun"
              >
                {{ t('common.back') }}
              </button>
              <div class="flex flex-wrap gap-2">
                <button
                  v-if="stepStatuses[stepIndex] === 'error'"
                  type="button"
                  class="lp-btn-ghost text-xs uppercase tracking-wide"
                  :disabled="running || askingAi"
                  @click="askAiToFix"
                >
                  <span class="material-symbols-outlined text-sm">auto_awesome</span>
                  {{ askingAi ? 'Loading…' : 'Ask AI to fix' }}
                </button>
                <button
                  v-if="stepStatuses[stepIndex] === 'error'"
                  type="button"
                  class="lp-btn-ghost text-xs uppercase tracking-wide"
                  :disabled="running"
                  @click="retryCurrentStep"
                >
                  Retry step
                </button>
                <button
                  type="button"
                  class="lp-btn-ghost text-xs uppercase tracking-wide"
                  :disabled="running"
                  @click="runCurrentStep"
                >
                  {{ running ? 'Running…' : 'Run this step' }}
                </button>
                <button
                  type="button"
                  class="text-xs uppercase tracking-wide"
                  :class="isDestroy ? 'lp-btn-ghost border border-[var(--lp-danger)]/40 text-[var(--lp-danger)]' : 'lp-btn-primary'"
                  :disabled="running || stepStatuses[stepIndex] === 'error'"
                  @click="runAndAdvance"
                >
                  {{
                    running
                      ? 'Running…'
                      : stepIndex + 1 >= steps.length
                        ? (isDestroy ? 'Yes, destroy' : 'Apply & finish')
                        : 'Run & next'
                  }}
                </button>
              </div>
            </div>
            <p class="text-[11px] text-[var(--lp-muted)]">
              Apply / destroy use auto-approve - you confirm here instead of typing yes in the terminal.
              On failure, use <strong class="text-[var(--lp-text)]">Ask AI to fix</strong>, then retry.
            </p>
          </div>

          <!-- DONE -->
          <div v-else-if="phase === 'done'" class="space-y-4">
            <div
              class="rounded-xl border p-4 text-sm"
              :class="
                isDestroy
                  ? 'border-[var(--lp-warn)]/30 bg-[var(--lp-warn)]/10 text-[var(--lp-warn)]'
                  : 'border-[var(--lp-ok)]/30 bg-[var(--lp-ok)]/10 text-[var(--lp-ok)]'
              "
            >
              <p class="font-semibold">
                {{
                  isDestroy
                    ? `${engineLabel} destroy completed. Managed cloud resources have been torn down.`
                    : `${engineLabel} provision completed successfully! (init → validate → plan → apply)`
                }}
              </p>
              <div v-if="!isDestroy" class="mt-3 space-y-2 border-t border-[var(--lp-ok)]/20 pt-3 text-xs text-[var(--lp-text)]">
                <p class="font-mono text-[11px] uppercase tracking-wide text-[var(--lp-muted)]">
                  Provisioned & Running Infrastructure:
                </p>
                <ul class="grid grid-cols-1 gap-2 font-mono text-[11px] sm:grid-cols-2">
                  <li class="flex items-center gap-2.5 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 px-3 py-2">
                    <span class="material-symbols-outlined text-base text-[var(--lp-ok)]">dns</span>
                    <div>
                      <p class="font-semibold text-[var(--lp-text)]">Kubernetes Cluster</p>
                      <p class="text-[10px] text-[var(--lp-muted)]">{{ provider.toUpperCase() }} GKE · Node Pool Active</p>
                    </div>
                  </li>
                  <li class="flex items-center gap-2.5 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 px-3 py-2">
                    <span class="material-symbols-outlined text-base text-[var(--lp-ok)]">lan</span>
                    <div>
                      <p class="font-semibold text-[var(--lp-text)]">Virtual Network</p>
                      <p class="text-[10px] text-[var(--lp-muted)]">Custom VPC & Subnet Active</p>
                    </div>
                  </li>
                  <li class="flex items-center gap-2.5 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 px-3 py-2">
                    <span class="material-symbols-outlined text-base text-[var(--lp-ok)]">key</span>
                    <div>
                      <p class="font-semibold text-[var(--lp-text)]">Secret Store</p>
                      <p class="text-[10px] text-[var(--lp-muted)]">Cloud Secret Manager Active</p>
                    </div>
                  </li>
                  <li class="flex items-center gap-2.5 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 px-3 py-2">
                    <span class="material-symbols-outlined text-base text-[var(--lp-ok)]">api</span>
                    <div>
                      <p class="font-semibold text-[var(--lp-text)]">Cloud APIs</p>
                      <p class="text-[10px] text-[var(--lp-muted)]">Enabled & Configured</p>
                    </div>
                  </li>
                </ul>
              </div>
            </div>
            <div class="flex justify-end">
              <button type="button" class="lp-btn-primary text-xs uppercase tracking-wide" @click="close">
                {{ t('workspaceIde.initModal.done') }}
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>

    <WorkspaceAiAnalysisDrawer
      :open="showAiFix"
      :workspace-id="workspaceId"
      :path="aiTargetPath"
      :content="aiTargetContent"
      :error-context="aiErrorContext"
      default-kind="iac"
      :persist-fix="applyAiFix"
      @update:open="(value) => { showAiFix = value }"
      @error="(message) => { fieldError = message; emit('error', message) }"
    />
  </Teleport>
</template>

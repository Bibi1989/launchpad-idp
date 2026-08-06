<script setup lang="ts">
import type {
  CicdPlatform,
  CicdSecurityConfig,
  ContainerScaffoldConfig,
  ContainerScanToolId,
  FrameworkOption,
  InfraGenerationConfig,
  K8sScaffoldMode,
  ProjectStackOption,
  ProvisionEngine,
  SastLanguage,
  SastToolId,
  ScanFindingAction,
  ScanSeverityThreshold,
} from '~/types/provisioning'
import { defaultCicdSecurityConfig, renderCicdWorkflow } from '~/utils/cicdWorkflowGenerator'
import {
  containerScanToolsForPlatform,
  normalizeContainerScanToolId,
  normalizeSastToolId,
  sastToolsForPlatform,
} from '~/utils/cicdSecurityTools'
import { copyTextToClipboard, downloadTextFile } from '~/utils/clipboardFile'
import { dockerfileContentForStack } from '~/utils/workspaceInfraScaffold'

const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    mode?: 'selection' | 'execution'
    disabled?: boolean
    provisionDisabled?: boolean
    kubernetesDisabled?: boolean
    showRunActions?: boolean
    busy?: boolean
  }>(),
  {
    mode: 'execution',
    disabled: false,
    provisionDisabled: false,
    kubernetesDisabled: false,
    showRunActions: true,
    busy: false,
  },
)

const config = defineModel<InfraGenerationConfig>('config', { required: true })
const containerScaffold = defineModel<ContainerScaffoldConfig>('containerScaffold', {
  default: () => ({
    enabled: false,
    generate_dockerfile: true,
    generate_docker_compose: true,
    stack: 'node',
    frameworks: [],
    app_name: 'app',
    listen_port: 8080,
  }),
})

const emit = defineEmits<{
  createProvision: []
  runProvision: []
  createKubernetes: []
  runKubernetes: []
  createCiCd: []
  createDocker: []
}>()

function toggleDocker(enabled: boolean) {
  containerScaffold.value = {
    ...containerScaffold.value,
    enabled,
  }
}

function setDockerFrameworks(frameworks: FrameworkOption[]) {
  const primaryStack = frameworks[0] ?? 'node'
  containerScaffold.value = {
    ...containerScaffold.value,
    frameworks,
    stack: primaryStack,
  }
}

function setDockerStack(stack: ProjectStackOption) {
  containerScaffold.value = {
    ...containerScaffold.value,
    stack,
  }
}

const selectedDockerFrameworks = computed({
  get: () => containerScaffold.value.frameworks || (containerScaffold.value.stack ? [containerScaffold.value.stack as FrameworkOption] : []),
  set: (val: FrameworkOption[]) => setDockerFrameworks(val),
})

const selectedCicdFrameworks = computed({
  get: () => config.value.cicd.frameworks ?? [],
  set: (val: FrameworkOption[]) => {
    config.value = {
      ...config.value,
      cicd: {
        ...ensureCicd(),
        frameworks: val,
      },
    }
  },
})

const copiedDockerfile = ref(false)
let copiedDockerfileTimer: ReturnType<typeof setTimeout> | null = null

function generateDockerScaffoldPreview(cfg: ContainerScaffoldConfig): string {
  const stack = cfg.frameworks?.[0] || cfg.stack || 'node'
  const name = cfg.app_name || 'app'
  const port = cfg.listen_port || 8080
  return dockerfileContentForStack(stack, name, port)
}

async function copyDockerfileContent() {
  const content = generateDockerScaffoldPreview(containerScaffold.value)
  const ok = await copyTextToClipboard(content)
  if (!ok) return
  copiedDockerfile.value = true
  if (copiedDockerfileTimer) clearTimeout(copiedDockerfileTimer)
  copiedDockerfileTimer = setTimeout(() => {
    copiedDockerfile.value = false
  }, 2000)
}

function downloadDockerfileFile() {
  const content = generateDockerScaffoldPreview(containerScaffold.value)
  downloadTextFile('Dockerfile', content)
}

const sastLanguageOptions: { value: SastLanguage; label: string }[] = [
  { value: 'javascript-typescript', label: 'JavaScript / TypeScript' },
  { value: 'python', label: 'Python' },
  { value: 'go', label: 'Go' },
  { value: 'java-kotlin', label: 'Java / Kotlin' },
  { value: 'csharp', label: 'C#' },
  { value: 'ruby', label: 'Ruby' },
]

function ensureSecurity(): CicdSecurityConfig {
  const platform = ensureCicd().platform
  return config.value.cicd?.security ?? defaultCicdSecurityConfig(platform)
}

function ensureCicd(): InfraGenerationConfig['cicd'] {
  const cicd = config.value.cicd
  const platform = cicd?.platform ?? 'github'
  return {
    enabled: cicd?.enabled ?? false,
    platform,
    security: cicd?.security ?? defaultCicdSecurityConfig(platform),
    frameworks: cicd?.frameworks ?? [],
  }
}

function patchSecurity(next: CicdSecurityConfig) {
  config.value = {
    ...config.value,
    cicd: {
      ...ensureCicd(),
      security: next,
    },
  }
}

function toggleProvision(enabled: boolean) {
  if (props.provisionDisabled) return
  config.value = {
    ...config.value,
    provision: { ...config.value.provision, enabled },
  }
}

function toggleKubernetes(enabled: boolean) {
  if (props.kubernetesDisabled) return
  config.value = {
    ...config.value,
    kubernetes: { ...config.value.kubernetes, enabled },
  }
}

function toggleCiCd(enabled: boolean) {
  config.value = {
    ...config.value,
    cicd: {
      ...ensureCicd(),
      enabled,
      security: ensureSecurity(),
    },
  }
}

function setProvisionEngine(engine: ProvisionEngine) {
  config.value = {
    ...config.value,
    provision: { ...config.value.provision, engine },
  }
}

function setK8sMode(mode: K8sScaffoldMode) {
  config.value = {
    ...config.value,
    kubernetes: { ...config.value.kubernetes, mode },
  }
}

function setCiCdPlatform(platform: CicdPlatform) {
  const security = ensureSecurity()
  config.value = {
    ...config.value,
    cicd: {
      ...ensureCicd(),
      platform,
      security: {
        ...security,
        containerScan: {
          ...security.containerScan,
          tool: normalizeContainerScanToolId(security.containerScan.tool, platform),
        },
        sastGuardrails: {
          ...security.sastGuardrails,
          sastTool: normalizeSastToolId(security.sastGuardrails.sastTool, platform),
        },
      },
    },
  }
}

function toggleContainerScan(enabled: boolean) {
  const security = ensureSecurity()
  patchSecurity({
    ...security,
    containerScan: { ...security.containerScan, enabled },
  })
}

function setSeverityThreshold(severityThreshold: ScanSeverityThreshold) {
  const security = ensureSecurity()
  patchSecurity({
    ...security,
    containerScan: { ...security.containerScan, severityThreshold },
  })
}

function setFindingAction(onFinding: ScanFindingAction) {
  const security = ensureSecurity()
  patchSecurity({
    ...security,
    containerScan: { ...security.containerScan, onFinding },
  })
}

function setContainerScanTool(tool: ContainerScanToolId) {
  const security = ensureSecurity()
  patchSecurity({
    ...security,
    containerScan: { ...security.containerScan, tool },
  })
}

function setSastTool(sastTool: SastToolId) {
  const security = ensureSecurity()
  patchSecurity({
    ...security,
    sastGuardrails: { ...security.sastGuardrails, sastTool },
  })
}

function toggleSastGuardrails(enabled: boolean) {
  const security = ensureSecurity()
  patchSecurity({
    ...security,
    sastGuardrails: {
      ...security.sastGuardrails,
      enabled,
      enableSast: enabled ? security.sastGuardrails.enableSast || true : security.sastGuardrails.enableSast,
      enableHealthRollback:
        enabled
          ? security.sastGuardrails.enableHealthRollback || true
          : security.sastGuardrails.enableHealthRollback,
    },
  })
}

function toggleEnableSast(enableSast: boolean) {
  const security = ensureSecurity()
  patchSecurity({
    ...security,
    sastGuardrails: { ...security.sastGuardrails, enableSast },
  })
}

function toggleHealthRollback(enableHealthRollback: boolean) {
  const security = ensureSecurity()
  patchSecurity({
    ...security,
    sastGuardrails: { ...security.sastGuardrails, enableHealthRollback },
  })
}

function setPrimarySastLanguage(language: SastLanguage) {
  const security = ensureSecurity()
  patchSecurity({
    ...security,
    sastGuardrails: {
      ...security.sastGuardrails,
      sastLanguages: [language],
    },
  })
}

const security = computed(() => ensureSecurity())
const cicd = computed(() => ensureCicd())
const cicdPlatform = computed(() => cicd.value.platform)
const sastToolOptions = computed(() => sastToolsForPlatform(cicdPlatform.value))
const containerScanToolOptions = computed(() => containerScanToolsForPlatform(cicdPlatform.value))
const pipelineSummary = computed(() => {
  const a = security.value.containerScan.enabled
  const b = security.value.sastGuardrails.enabled
  const sast = b && security.value.sastGuardrails.enableSast
  const health = b && security.value.sastGuardrails.enableHealthRollback
  const stages: string[] = []
  if (sast) stages.push('SAST')
  stages.push('Build')
  if (a) stages.push('Image scan')
  stages.push(health ? 'Deploy + rollback' : 'Deploy')
  return stages.join(' → ')
})

const previewWorkflowYaml = computed(() =>
  renderCicdWorkflow(cicd.value.platform, ensureSecurity()),
)

const previewWorkflowFilename = computed(() =>
  cicd.value.platform === 'github' ? 'deploy.yml' : '.gitlab-ci.yml',
)

const copiedWorkflow = ref(false)
let copiedWorkflowTimer: ReturnType<typeof setTimeout> | null = null

async function copyWorkflowYaml() {
  const ok = await copyTextToClipboard(previewWorkflowYaml.value)
  if (!ok) return
  copiedWorkflow.value = true
  if (copiedWorkflowTimer) clearTimeout(copiedWorkflowTimer)
  copiedWorkflowTimer = setTimeout(() => {
    copiedWorkflow.value = false
  }, 2000)
}

function downloadWorkflowYaml() {
  downloadTextFile(previewWorkflowFilename.value, previewWorkflowYaml.value)
}</script>

<template>
  <div class="space-y-5">
    <div class="grid gap-5 md:grid-cols-2">
      <!-- 1. PROVISION CARD -->
      <article
        class="flex flex-col justify-between rounded-xl border p-5 transition shadow-sm hover:shadow-md"
        :class="
          config.provision.enabled
            ? 'border-[var(--lp-accent)]/50 bg-[var(--lp-accent)]/5'
            : 'border-[var(--lp-line)] bg-[var(--lp-panel)]/40'
        "
      >
        <div class="space-y-3">
          <div class="flex items-start justify-between gap-3">
            <div class="flex items-center gap-2.5">
              <span class="material-symbols-outlined text-xl text-[var(--lp-accent)]">cloud</span>
              <div>
                <p class="lp-label">{{ t('scaffold.infra.provision.label') }}</p>
                <h3 class="text-base font-semibold text-[var(--lp-text)]">{{ t('scaffold.infra.provision.title') }}</h3>
              </div>
            </div>
            <label
              v-if="mode === 'selection'"
              class="flex items-center gap-2 text-xs font-medium text-[var(--lp-muted)] cursor-pointer"
            >
              <input
                :checked="config.provision.enabled"
                type="checkbox"
                class="h-4 w-4 accent-[var(--lp-accent)] rounded"
                :disabled="disabled || provisionDisabled"
                @change="toggleProvision(($event.target as HTMLInputElement).checked)"
              >
              {{ t('scaffold.infra.enable') }}
            </label>
          </div>
          <p class="text-xs leading-relaxed text-[var(--lp-muted)]">
            {{ t('scaffold.infra.provision.blurb') }}
          </p>
          <p v-if="provisionDisabled" class="text-xs text-[var(--lp-muted)] italic">
            {{ t('scaffold.infra.provision.localHint') }}
          </p>
        </div>

        <div class="mt-4 space-y-3 pt-3 border-t border-[var(--lp-line)]/50">
          <select
            :value="config.provision.engine"
            class="lp-input w-full text-xs"
            :disabled="disabled || (mode === 'selection' && !config.provision.enabled)"
            @change="setProvisionEngine(($event.target as HTMLSelectElement).value as ProvisionEngine)"
          >
            <option value="terraform">{{ t('workspaceIde.engines.terraform') }}</option>
            <option value="opentofu">{{ t('workspaceIde.engines.opentofu') }}</option>
            <option value="pulumi">{{ t('workspaceIde.engines.pulumi') }}</option>
          </select>

          <div v-if="mode === 'execution'" class="flex items-center gap-2">
            <button
              type="button"
              class="lp-btn-primary text-xs px-3 py-1.5"
              :disabled="busy || disabled"
              @click="emit('createProvision')"
            >
              {{ t('scaffold.infra.createFiles') }}
            </button>
            <button
              v-if="showRunActions"
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="busy || disabled"
              @click="emit('runProvision')"
            >
              {{ t('scaffold.infra.run') }}
            </button>
          </div>
        </div>
      </article>

      <!-- 2. KUBERNETES CARD -->
      <article
        class="flex flex-col justify-between rounded-xl border p-5 transition shadow-sm hover:shadow-md"
        :class="
          config.kubernetes.enabled
            ? 'border-[var(--lp-accent)]/50 bg-[var(--lp-accent)]/5'
            : 'border-[var(--lp-line)] bg-[var(--lp-panel)]/40'
        "
      >
        <div class="space-y-3">
          <div class="flex items-start justify-between gap-3">
            <div class="flex items-center gap-2.5">
              <span class="material-symbols-outlined text-xl text-[var(--lp-accent)]">view_in_ar</span>
              <div>
                <p class="lp-label">{{ t('scaffold.infra.kubernetes.label') }}</p>
                <h3 class="text-base font-semibold text-[var(--lp-text)]">{{ t('scaffold.infra.kubernetes.title') }}</h3>
              </div>
            </div>
            <label
              v-if="mode === 'selection'"
              class="flex items-center gap-2 text-xs font-medium text-[var(--lp-muted)] cursor-pointer"
            >
              <input
                :checked="config.kubernetes.enabled"
                type="checkbox"
                class="h-4 w-4 accent-[var(--lp-accent)] rounded"
                :disabled="disabled || kubernetesDisabled"
                @change="toggleKubernetes(($event.target as HTMLInputElement).checked)"
              >
              {{ t('scaffold.infra.enable') }}
            </label>
          </div>
          <p class="text-xs leading-relaxed text-[var(--lp-muted)]">
            {{ t('scaffold.infra.kubernetes.blurb') }}
          </p>
          <p v-if="kubernetesDisabled" class="text-xs text-[var(--lp-muted)] italic">
            {{ t('scaffold.infra.kubernetes.runtimeHint') }}
          </p>
        </div>

        <div class="mt-4 space-y-3 pt-3 border-t border-[var(--lp-line)]/50">
          <select
            :value="config.kubernetes.mode"
            class="lp-input w-full text-xs"
            :disabled="disabled || kubernetesDisabled || (mode === 'selection' && !config.kubernetes.enabled)"
            @change="setK8sMode(($event.target as HTMLSelectElement).value as K8sScaffoldMode)"
          >
            <option value="k8s">{{ t('scaffold.infra.kubernetes.modeK8s') }}</option>
            <option value="helm">{{ t('scaffold.infra.kubernetes.modeHelm') }}</option>
            <option value="kustomize">{{ t('scaffold.infra.kubernetes.modeKustomize') }}</option>
          </select>

          <div v-if="mode === 'execution'" class="flex items-center gap-2">
            <button
              type="button"
              class="lp-btn-primary text-xs px-3 py-1.5"
              :disabled="busy || disabled"
              @click="emit('createKubernetes')"
            >
              {{ t('scaffold.infra.createFiles') }}
            </button>
            <button
              v-if="showRunActions"
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="busy || disabled"
              @click="emit('runKubernetes')"
            >
              {{ t('scaffold.infra.run') }}
            </button>
          </div>
        </div>
      </article>

      <!-- 3. CI/CD CARD -->
      <article
        class="flex flex-col justify-between rounded-xl border p-5 transition shadow-sm hover:shadow-md"
        :class="
          cicd.enabled
            ? 'border-[var(--lp-accent)]/50 bg-[var(--lp-accent)]/5'
            : 'border-[var(--lp-line)] bg-[var(--lp-panel)]/40'
        "
      >
        <div class="space-y-3">
          <div class="flex items-start justify-between gap-3">
            <div class="flex items-center gap-2.5">
              <span class="material-symbols-outlined text-xl text-[var(--lp-accent)]">sync_alt</span>
              <div>
                <p class="lp-label">{{ t('scaffold.infra.cicd.label') }}</p>
                <h3 class="text-base font-semibold text-[var(--lp-text)]">{{ t('scaffold.infra.cicd.title') }}</h3>
              </div>
            </div>
            <label
              v-if="mode === 'selection'"
              class="flex items-center gap-2 text-xs font-medium text-[var(--lp-muted)] cursor-pointer"
            >
              <input
                :checked="cicd.enabled"
                type="checkbox"
                class="h-4 w-4 accent-[var(--lp-accent)] rounded"
                :disabled="disabled"
                @change="toggleCiCd(($event.target as HTMLInputElement).checked)"
              >
              {{ t('scaffold.infra.enable') }}
            </label>
          </div>
          <p class="text-xs leading-relaxed text-[var(--lp-muted)]">
            {{ t('scaffold.infra.cicd.blurb') }}
          </p>
        </div>

        <div class="mt-4 space-y-3 pt-3 border-t border-[var(--lp-line)]/50">
          <div class="grid gap-2">
            <label class="block text-[11px] font-medium text-[var(--lp-muted)]">{{ t('scaffold.infra.cicd.frameworks') }}</label>
            <FrameworkMultiSelectDropdown
              v-model="selectedCicdFrameworks"
              :disabled="disabled || (mode === 'selection' && !cicd.enabled)"
              :placeholder="t('scaffold.infra.cicd.frameworksPlaceholder')"
            />
          </div>

          <div class="grid gap-2">
            <label class="block text-[11px] font-medium text-[var(--lp-muted)]">{{ t('scaffold.infra.cicd.provider') }}</label>
            <select
              :value="cicd.platform"
              class="lp-input w-full text-xs"
              :disabled="disabled || (mode === 'selection' && !cicd.enabled)"
              @change="setCiCdPlatform(($event.target as HTMLSelectElement).value as CicdPlatform)"
            >
              <option value="github">{{ t('scaffold.infra.cicd.githubOnly') }}</option>
              <option value="gitlab">{{ t('scaffold.infra.cicd.gitlabOnly') }}</option>
            </select>
          </div>

          <div class="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-2.5 py-1.5 text-[11px] text-emerald-400 flex items-center justify-between">
            <span class="flex items-center gap-1 font-medium">
              <span class="material-symbols-outlined text-sm">shield</span>
              {{ t('scaffold.infra.cicd.securityActive') }}
            </span>
            <span class="rounded bg-emerald-500/20 px-1 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-300">
              {{ t('scaffold.infra.cicd.verified') }}
            </span>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <button
              v-if="mode === 'execution'"
              type="button"
              class="lp-btn-primary text-xs px-3 py-1.5"
              :disabled="busy || disabled"
              @click="emit('createCiCd')"
            >
              {{ t('scaffold.infra.createFiles') }}
            </button>
            <button
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="disabled || (mode === 'selection' && !cicd.enabled)"
              @click="copyWorkflowYaml"
            >
              {{ copiedWorkflow ? t('common.copied') : t('scaffold.infra.copyYaml') }}
            </button>
            <button
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="disabled || (mode === 'selection' && !cicd.enabled)"
              @click="downloadWorkflowYaml"
            >
              {{ t('common.download') }}
            </button>
          </div>
        </div>
      </article>

      <!-- 4. DOCKER CARD (AT THE END!) -->
      <article
        class="flex flex-col justify-between rounded-xl border p-5 transition shadow-sm hover:shadow-md"
        :class="
          containerScaffold.enabled
            ? 'border-[var(--lp-accent)]/50 bg-[var(--lp-accent)]/5'
            : 'border-[var(--lp-line)] bg-[var(--lp-panel)]/40'
        "
      >
        <div class="space-y-3">
          <div class="flex items-start justify-between gap-3">
            <div class="flex items-center gap-2.5">
              <span class="material-symbols-outlined text-xl text-[var(--lp-accent)]">deployed_code</span>
              <div>
                <p class="lp-label">{{ t('scaffold.infra.docker.label') }}</p>
                <h3 class="text-base font-semibold text-[var(--lp-text)]">{{ t('scaffold.infra.docker.title') }}</h3>
              </div>
            </div>
            <label
              v-if="mode === 'selection'"
              class="flex items-center gap-2 text-xs font-medium text-[var(--lp-muted)] cursor-pointer"
            >
              <input
                :checked="containerScaffold.enabled"
                type="checkbox"
                class="h-4 w-4 accent-[var(--lp-accent)] rounded"
                :disabled="disabled"
                @change="toggleDocker(($event.target as HTMLInputElement).checked)"
              >
              {{ t('scaffold.infra.enable') }}
            </label>
          </div>
          <p class="text-xs leading-relaxed text-[var(--lp-muted)]">
            {{ t('scaffold.infra.docker.blurb') }}
          </p>
        </div>

        <div class="mt-4 space-y-3 pt-3 border-t border-[var(--lp-line)]/50">
          <div class="grid gap-2">
            <label class="block text-[11px] font-medium text-[var(--lp-muted)]">{{ t('scaffold.infra.docker.frameworks') }}</label>
            <FrameworkMultiSelectDropdown
              v-model="selectedDockerFrameworks"
              :disabled="disabled || (mode === 'selection' && !containerScaffold.enabled)"
              :placeholder="t('scaffold.infra.docker.frameworksPlaceholder')"
            />
          </div>

          <div class="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-2.5 py-1.5 text-[11px] text-emerald-400 flex items-center justify-between">
            <span class="flex items-center gap-1 font-medium">
              <span class="material-symbols-outlined text-sm">verified_user</span>
              {{ t('scaffold.infra.docker.hardened') }}
            </span>
            <span class="rounded bg-emerald-500/20 px-1 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-300">
              {{ t('scaffold.infra.docker.zeroRisks') }}
            </span>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <button
              v-if="mode === 'execution'"
              type="button"
              class="lp-btn-primary text-xs px-3 py-1.5"
              :disabled="busy || disabled"
              @click="emit('createDocker')"
            >
              {{ t('scaffold.infra.createFiles') }}
            </button>
            <button
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="disabled || (mode === 'selection' && !containerScaffold.enabled)"
              @click="copyDockerfileContent"
            >
              {{ copiedDockerfile ? t('common.copied') : t('scaffold.infra.copyDockerfile') }}
            </button>
            <button
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="disabled || (mode === 'selection' && !containerScaffold.enabled)"
              @click="downloadDockerfileFile"
            >
              {{ t('common.download') }}
            </button>
          </div>
        </div>
      </article>
    </div>

    <section
      v-if="cicd.enabled || mode === 'execution'"
      class="space-y-4 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/30 p-4"
      :class="{ 'opacity-50 pointer-events-none': mode === 'selection' && !cicd.enabled }"
    >
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p class="lp-label">{{ t('scaffold.infra.pipelineSecurity.label') }}</p>
          <h3 class="mt-1 text-base font-semibold">{{ t('scaffold.infra.pipelineSecurity.title') }}</h3>
          <p class="mt-1 text-sm text-[var(--lp-muted)]">
            {{ t('scaffold.infra.pipelineSecurity.blurb') }}
          </p>
        </div>
        <p class="font-mono text-[10px] uppercase tracking-wider text-[var(--lp-accent)]">
          {{ pipelineSummary }}
        </p>
      </div>

      <div class="grid gap-4 lg:grid-cols-2">
        <div class="space-y-3 rounded-lg border border-[var(--lp-line)] p-3">
          <label class="flex items-start gap-3">
            <input
              :checked="security.containerScan.enabled"
              type="checkbox"
              class="mt-1 h-4 w-4 accent-[var(--lp-accent)]"
              :disabled="disabled || (mode === 'selection' && !cicd.enabled)"
              @change="toggleContainerScan(($event.target as HTMLInputElement).checked)"
            >
            <span>
              <span class="block text-sm font-medium text-[var(--lp-text)]">
                {{ t('scaffold.infra.pipelineSecurity.solutionA') }}
              </span>
              <span class="mt-0.5 block text-xs text-[var(--lp-muted)]">
                {{ t('scaffold.infra.pipelineSecurity.solutionABlurb') }}
              </span>
            </span>
          </label>
          <div
            v-if="security.containerScan.enabled"
            class="space-y-3 border-t border-[var(--lp-line)] pt-3"
          >
            <label class="block space-y-1.5">
              <span class="lp-label">{{ t('scaffold.infra.pipelineSecurity.scanner') }}</span>
              <select
                :value="security.containerScan.tool"
                class="lp-input font-mono text-xs"
                :disabled="disabled"
                @change="setContainerScanTool(($event.target as HTMLSelectElement).value as ContainerScanToolId)"
              >
                <option
                  v-for="opt in containerScanToolOptions"
                  :key="opt.id"
                  :value="opt.id"
                >
                  {{ opt.label }}
                </option>
              </select>
              <span class="block font-mono text-[10px] text-[var(--lp-muted)]">
                {{ containerScanToolOptions.find((o) => o.id === security.containerScan.tool)?.hint }}
              </span>
            </label>
            <label class="block space-y-1.5">
              <span class="lp-label">{{ t('scaffold.infra.pipelineSecurity.severity') }}</span>
              <select
                :value="security.containerScan.severityThreshold"
                class="lp-input"
                :disabled="disabled"
                @change="setSeverityThreshold(($event.target as HTMLSelectElement).value as ScanSeverityThreshold)"
              >
                <option value="critical">{{ t('scaffold.infra.pipelineSecurity.criticalOnly') }}</option>
                <option value="critical_high">{{ t('scaffold.infra.pipelineSecurity.criticalHigh') }}</option>
              </select>
            </label>
            <label class="block space-y-1.5">
              <span class="lp-label">{{ t('scaffold.infra.pipelineSecurity.onFinding') }}</span>
              <select
                :value="security.containerScan.onFinding"
                class="lp-input"
                :disabled="disabled"
                @change="setFindingAction(($event.target as HTMLSelectElement).value as ScanFindingAction)"
              >
                <option value="block">{{ t('scaffold.infra.pipelineSecurity.blockDeploy') }}</option>
                <option value="warn">{{ t('scaffold.infra.pipelineSecurity.warnUpload') }}</option>
              </select>
            </label>
          </div>
        </div>

        <div class="space-y-3 rounded-lg border border-[var(--lp-line)] p-3">
          <label class="flex items-start gap-3">
            <input
              :checked="security.sastGuardrails.enabled"
              type="checkbox"
              class="mt-1 h-4 w-4 accent-[var(--lp-accent)]"
              :disabled="disabled || (mode === 'selection' && !cicd.enabled)"
              @change="toggleSastGuardrails(($event.target as HTMLInputElement).checked)"
            >
            <span>
              <span class="block text-sm font-medium text-[var(--lp-text)]">
                {{ t('scaffold.infra.pipelineSecurity.solutionB') }}
              </span>
              <span class="mt-0.5 block text-xs text-[var(--lp-muted)]">
                {{ t('scaffold.infra.pipelineSecurity.solutionBBlurb') }}
              </span>
            </span>
          </label>
          <div
            v-if="security.sastGuardrails.enabled"
            class="space-y-3 border-t border-[var(--lp-line)] pt-3"
          >
            <label class="flex items-center gap-2 text-sm text-[var(--lp-text)]">
              <input
                :checked="security.sastGuardrails.enableSast"
                type="checkbox"
                class="h-4 w-4 accent-[var(--lp-accent)]"
                :disabled="disabled"
                @change="toggleEnableSast(($event.target as HTMLInputElement).checked)"
              >
              {{ t('scaffold.infra.pipelineSecurity.enableSast') }}
            </label>
            <label
              v-if="security.sastGuardrails.enableSast"
              class="block space-y-1.5"
            >
              <span class="lp-label">{{ t('scaffold.infra.pipelineSecurity.sastScanner') }}</span>
              <select
                :value="security.sastGuardrails.sastTool"
                class="lp-input font-mono text-xs"
                :disabled="disabled"
                @change="setSastTool(($event.target as HTMLSelectElement).value as SastToolId)"
              >
                <option
                  v-for="opt in sastToolOptions"
                  :key="opt.id"
                  :value="opt.id"
                >
                  {{ opt.label }}
                </option>
              </select>
              <span class="block font-mono text-[10px] text-[var(--lp-muted)]">
                {{ sastToolOptions.find((o) => o.id === security.sastGuardrails.sastTool)?.hint }}
              </span>
            </label>
            <label
              v-if="security.sastGuardrails.enableSast && security.sastGuardrails.sastTool === 'codeql-v3.28.10'"
              class="block space-y-1.5"
            >
              <span class="lp-label">{{ t('scaffold.infra.pipelineSecurity.codeqlLang') }}</span>
              <select
                :value="security.sastGuardrails.sastLanguages[0] ?? 'javascript-typescript'"
                class="lp-input"
                :disabled="disabled"
                @change="setPrimarySastLanguage(($event.target as HTMLSelectElement).value as SastLanguage)"
              >
                <option
                  v-for="opt in sastLanguageOptions"
                  :key="opt.value"
                  :value="opt.value"
                >
                  {{ opt.label }}
                </option>
              </select>
            </label>
            <label class="flex items-center gap-2 text-sm text-[var(--lp-text)]">
              <input
                :checked="security.sastGuardrails.enableHealthRollback"
                type="checkbox"
                class="h-4 w-4 accent-[var(--lp-accent)]"
                :disabled="disabled"
                @change="toggleHealthRollback(($event.target as HTMLInputElement).checked)"
              >
              {{ t('scaffold.infra.pipelineSecurity.healthRollback') }}
            </label>
          </div>
        </div>
      </div>
    </section>

    <div class="rounded-xl border border-dashed border-[var(--lp-line)] px-6 py-5 text-sm text-[var(--lp-muted)]">
      <template v-if="mode === 'selection'">
        {{ t('scaffold.infra.footerSelection') }}
      </template>
      <template v-else>
        {{ t('scaffold.infra.footerExecution') }}
      </template>
    </div>
  </div>
</template>

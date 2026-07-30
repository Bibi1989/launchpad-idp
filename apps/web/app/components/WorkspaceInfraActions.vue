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
  const primaryStack = frameworks.length > 0 ? frameworks[0] : 'node'
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
  const stack = (cfg.frameworks && cfg.frameworks.length > 0)
    ? cfg.frameworks[0]
    : (cfg.stack || 'node')
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
                <p class="lp-label">Provision</p>
                <h3 class="text-base font-semibold text-[var(--lp-text)]">Create Provision</h3>
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
              Enable
            </label>
          </div>
          <p class="text-xs leading-relaxed text-[var(--lp-muted)]">
            Scaffold Terraform or Pulumi files under <code class="font-mono text-xs text-[var(--lp-accent)]">infra/</code>.
          </p>
          <p v-if="provisionDisabled" class="text-xs text-[var(--lp-muted)] italic">
            Dev (kind) workspaces use Kubernetes manifests only.
          </p>
        </div>

        <div class="mt-4 space-y-3 pt-3 border-t border-[var(--lp-line)]/50">
          <select
            :value="config.provision.engine"
            class="lp-input w-full text-xs"
            :disabled="disabled || (mode === 'selection' && !config.provision.enabled)"
            @change="setProvisionEngine(($event.target as HTMLSelectElement).value as ProvisionEngine)"
          >
            <option value="terraform">Terraform</option>
            <option value="opentofu">OpenTofu</option>
            <option value="pulumi">Pulumi</option>
          </select>

          <div v-if="mode === 'execution'" class="flex items-center gap-2">
            <button
              type="button"
              class="lp-btn-primary text-xs px-3 py-1.5"
              :disabled="busy || disabled"
              @click="emit('createProvision')"
            >
              Create files
            </button>
            <button
              v-if="showRunActions"
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="busy || disabled"
              @click="emit('runProvision')"
            >
              Run
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
                <p class="lp-label">Kubernetes</p>
                <h3 class="text-base font-semibold text-[var(--lp-text)]">Create Kubernetes</h3>
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
              Enable
            </label>
          </div>
          <p class="text-xs leading-relaxed text-[var(--lp-muted)]">
            Scaffold raw manifests, Helm chart, or Kustomize layout under <code class="font-mono text-xs text-[var(--lp-accent)]">infra/</code>.
          </p>
          <p v-if="kubernetesDisabled" class="text-xs text-[var(--lp-muted)] italic">
            Enable a Kubernetes runtime (GKE, EKS, AKS, Cloud Run, or Container Apps) first.
          </p>
        </div>

        <div class="mt-4 space-y-3 pt-3 border-t border-[var(--lp-line)]/50">
          <select
            :value="config.kubernetes.mode"
            class="lp-input w-full text-xs"
            :disabled="disabled || kubernetesDisabled || (mode === 'selection' && !config.kubernetes.enabled)"
            @change="setK8sMode(($event.target as HTMLSelectElement).value as K8sScaffoldMode)"
          >
            <option value="k8s">K8s manifests</option>
            <option value="helm">Helm chart</option>
            <option value="kustomize">Kustomize</option>
          </select>

          <div v-if="mode === 'execution'" class="flex items-center gap-2">
            <button
              type="button"
              class="lp-btn-primary text-xs px-3 py-1.5"
              :disabled="busy || disabled"
              @click="emit('createKubernetes')"
            >
              Create files
            </button>
            <button
              v-if="showRunActions"
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="busy || disabled"
              @click="emit('runKubernetes')"
            >
              Run
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
                <p class="lp-label">CI/CD</p>
                <h3 class="text-base font-semibold text-[var(--lp-text)]">Create CI/CD</h3>
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
              Enable
            </label>
          </div>
          <p class="text-xs leading-relaxed text-[var(--lp-muted)]">
            Scaffold a multi-job GitHub or GitLab pipeline with optional security stages.
          </p>
        </div>

        <div class="mt-4 space-y-3 pt-3 border-t border-[var(--lp-line)]/50">
          <div class="grid gap-2">
            <label class="block text-[11px] font-medium text-[var(--lp-muted)]">Target Frameworks / Stacks</label>
            <FrameworkMultiSelectDropdown
              v-model="selectedCicdFrameworks"
              :disabled="disabled || (mode === 'selection' && !cicd.enabled)"
              placeholder="Check Target Frameworks"
            />
          </div>

          <select
            :value="cicd.platform"
            class="lp-input w-full text-xs"
            :disabled="disabled || (mode === 'selection' && !cicd.enabled)"
            @change="setCiCdPlatform(($event.target as HTMLSelectElement).value as CicdPlatform)"
          >
            <option value="github">GitHub Workflow</option>
            <option value="gitlab">GitLab CI</option>
          </select>

          <div class="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-2.5 py-1.5 text-[11px] text-emerald-400 flex items-center justify-between">
            <span class="flex items-center gap-1 font-medium">
              <span class="material-symbols-outlined text-sm">shield</span>
              SAST &amp; Security Scans Active
            </span>
            <span class="rounded bg-emerald-500/20 px-1 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-300">
              Verified
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
              Create files
            </button>
            <button
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="disabled || (mode === 'selection' && !cicd.enabled)"
              @click="copyWorkflowYaml"
            >
              {{ copiedWorkflow ? 'Copied' : 'Copy YAML' }}
            </button>
            <button
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="disabled || (mode === 'selection' && !cicd.enabled)"
              @click="downloadWorkflowYaml"
            >
              Download
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
                <p class="lp-label">Docker</p>
                <h3 class="text-base font-semibold text-[var(--lp-text)]">Create Docker</h3>
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
              Enable
            </label>
          </div>
          <p class="text-xs leading-relaxed text-[var(--lp-muted)]">
            Scaffold multi-stage Dockerfile &amp; docker-compose under <code class="font-mono text-xs text-[var(--lp-accent)]">dockers/</code>.
          </p>
        </div>

        <div class="mt-4 space-y-3 pt-3 border-t border-[var(--lp-line)]/50">
          <div class="grid gap-2">
            <label class="block text-[11px] font-medium text-[var(--lp-muted)]">Select Frameworks / Stacks</label>
            <FrameworkMultiSelectDropdown
              v-model="selectedDockerFrameworks"
              :disabled="disabled || (mode === 'selection' && !containerScaffold.enabled)"
              placeholder="Check FastAPI, Express, React..."
            />
          </div>

          <div class="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-2.5 py-1.5 text-[11px] text-emerald-400 flex items-center justify-between">
            <span class="flex items-center gap-1 font-medium">
              <span class="material-symbols-outlined text-sm">verified_user</span>
              USER 10001 &amp; Multi-stage Hardened
            </span>
            <span class="rounded bg-emerald-500/20 px-1 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-300">
              0 Risks
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
              Create files
            </button>
            <button
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="disabled || (mode === 'selection' && !containerScaffold.enabled)"
              @click="copyDockerfileContent"
            >
              {{ copiedDockerfile ? 'Copied' : 'Copy Dockerfile' }}
            </button>
            <button
              type="button"
              class="lp-btn-ghost text-xs px-3 py-1.5"
              :disabled="disabled || (mode === 'selection' && !containerScaffold.enabled)"
              @click="downloadDockerfileFile"
            >
              Download
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
          <p class="lp-label">Pipeline security</p>
          <h3 class="mt-1 text-base font-semibold">Solutions A &amp; B</h3>
          <p class="mt-1 text-sm text-[var(--lp-muted)]">
            Toggle independently — A only, B only, both, or neither. Actions are pinned to commit
            SHAs.
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
                Container image security scanning (Solution A)
              </span>
              <span class="mt-0.5 block text-xs text-[var(--lp-muted)]">
                Trivy CVE scan after build, before deploy. Uploads SARIF to the security dashboard.
              </span>
            </span>
          </label>
          <div
            v-if="security.containerScan.enabled"
            class="space-y-3 border-t border-[var(--lp-line)] pt-3"
          >
            <label class="block space-y-1.5">
              <span class="lp-label">Security scanner</span>
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
              <span class="lp-label">Severity threshold</span>
              <select
                :value="security.containerScan.severityThreshold"
                class="lp-input"
                :disabled="disabled"
                @change="setSeverityThreshold(($event.target as HTMLSelectElement).value as ScanSeverityThreshold)"
              >
                <option value="critical">CRITICAL only</option>
                <option value="critical_high">CRITICAL + HIGH (default)</option>
              </select>
            </label>
            <label class="block space-y-1.5">
              <span class="lp-label">Action on finding</span>
              <select
                :value="security.containerScan.onFinding"
                class="lp-input"
                :disabled="disabled"
                @change="setFindingAction(($event.target as HTMLSelectElement).value as ScanFindingAction)"
              >
                <option value="block">Block deployment / fail job</option>
                <option value="warn">Warn &amp; upload report</option>
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
                SAST &amp; production protection (Solution B)
              </span>
              <span class="mt-0.5 block text-xs text-[var(--lp-muted)]">
                Pre-build static analysis plus deploy health verification with auto-rollback.
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
              Enable SAST code analysis (CodeQL / Semgrep)
            </label>
            <label
              v-if="security.sastGuardrails.enableSast"
              class="block space-y-1.5"
            >
              <span class="lp-label">SAST scanner</span>
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
              <span class="lp-label">CodeQL language pack</span>
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
              Automated health check &amp; instant rollback
            </label>
          </div>
        </div>
      </div>
    </section>

    <div class="rounded-xl border border-dashed border-[var(--lp-line)] px-6 py-5 text-sm text-[var(--lp-muted)]">
      <template v-if="mode === 'selection'">
        Select any combination of Provision, Kubernetes, and CI/CD. Security solutions A/B only
        apply when CI/CD is enabled and write into the generated pipeline under
        <code class="font-mono text-xs">ci/</code>.
      </template>
      <template v-else>
        Actions are independent. Regenerating CI/CD rewrites the workflow with the current security
        toggles (SHA-pinned actions).
      </template>
    </div>
  </div>
</template>

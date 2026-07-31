<script setup lang="ts">
import {
  provisioningWizardSchema,
  containerScaffoldSchema,
  defaultContainerScaffold,
  type ProvisioningWizardInput,
} from '~/utils/cloudValidation'
import type {
  CloudProvider,
  ContainerScaffoldConfig,
  CostOptimizationConfig,
  FrameworkOption,
  IaCEngine,
  InfraGenerationConfig,
  KubernetesPackaging,
  WorkspaceWizardConfig,
} from '~/types/provisioning'
import {
  applyCostOptimizationToWorkloadOptions,
  costOptimizationFromApi,
  defaultCostOptimizationConfig,
} from '~/utils/costOptimization'
import {
  applyDetectedWorkspaceInfra,
  artifactModeToInfraConfig,
  buildDockerScaffold,
  detectWorkspaceInfraFromPaths,
  infraConfigToArtifactMode,
  infraConfigToKubernetesPackaging,
} from '~/utils/workspaceInfraScaffold'
import { inferCicdSecurityFromContent } from '~/utils/cicdWorkflowGenerator'
import { syncWorkspaceCicdToPlatform } from '~/utils/syncWorkspaceCicd'
import { AWS_INSTANCE_TYPES, AWS_REGIONS, AZURE_LOCATIONS, AZURE_VM_SIZES, GCP_MACHINE_TYPES, GCP_REGIONS } from '~/utils/cloudRegions'
import {
  AWS_SERVICE_OPTIONS,
  AZURE_SERVICE_OPTIONS,
  CLOUDFLARE_SERVICE_OPTIONS,
  enabledCloudServices,
  GCP_SERVICE_OPTIONS,
} from '~/utils/cloudServiceOptions'

const props = defineProps<{
  workspaceId: string
}>()

const emit = defineEmits<{
  saved: []
  error: [message: string]
  cancel: []
}>()

const {
  getWizardConfig,
  updateWorkspace,
  writeWorkspaceFile,
  listWorkspaceFiles,
  readWorkspaceFile,
  deleteWorkspacePath,
} = useProvisioning()

const loading = ref(true)
const saving = ref(false)
const currentStep = ref(1)
const TOTAL_STEPS = 3
const fieldError = ref<string | null>(null)
const statusMessage = ref<string | null>(null)
const detectionSummary = ref<string[]>([])
const workspaceName = ref('')
const provider = ref<CloudProvider>('local')
const hasStoredCredentials = ref(false)

const form = reactive({
  iac_engine: 'terraform' as IaCEngine,
  run_init: true,
  kubernetes_packaging: 'none' as KubernetesPackaging,
  cost_optimization: defaultCostOptimizationConfig() as CostOptimizationConfig,
  container_scaffold: defaultContainerScaffold() as ContainerScaffoldConfig,
  local: {
    cluster_name: 'launchpad',
    context: 'kind-launchpad',
  },
  gcp: {
    vpc: true,
    subnets: true,
    network_topology: 'simple' as 'simple' | 'standard',
    gke: false,
    artifact_registry: false,
    secret_backend: 'secret_manager' as 'secret_manager' | 'native_k8s',
    cloud_run: false,
    cloud_functions: false,
    cloud_sql: false,
    cloud_storage: false,
    pubsub: false,
    memorystore: false,
    bigquery: false,
    region: 'us-central1',
    machine_type: 'e2-standard-4',
    project_id: '',
  },
  aws: {
    vpc: true,
    subnets: true,
    network_topology: 'simple' as 'simple' | 'standard',
    ec2: false,
    s3: false,
    eks: false,
    secrets_manager: true,
    rds: false,
    ecr: false,
    elasticache: false,
    lambda_fn: false,
    dynamodb: false,
    sqs: false,
    alb: false,
    region: 'us-east-1',
    instance_type: 't3.medium',
    account_alias: '',
  },
  azure: {
    vnet: true,
    subnets: true,
    network_topology: 'simple' as 'simple' | 'standard',
    aks: false,
    key_vault: true,
    container_apps: false,
    acr: false,
    storage_account: false,
    cosmos_db: false,
    redis_cache: false,
    app_service: false,
    log_analytics: false,
    location: 'eastus',
    vm_size: 'Standard_D2_v2',
    resource_group: '',
  },
  cloudflare: {
    workers: false,
    r2: false,
    dns_records: false,
    pages: false,
    kv: false,
    d1: false,
    tunnels: false,
    queues: false,
    account_id: '',
    zone_name: '',
  },
  credentials: {
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
  },
})

const infraGeneration = ref<InfraGenerationConfig>(
  artifactModeToInfraConfig('iac_only', 'terraform', 'none'),
)

const progressPct = computed(() => (currentStep.value / TOTAL_STEPS) * 100)
const isLocalProvider = computed(() => provider.value === 'local')
const hasKubernetesRuntime = computed(() => {
  if (provider.value === 'local') return true
  if (provider.value === 'gcp') return form.gcp.gke || form.gcp.cloud_run
  if (provider.value === 'aws') return form.aws.eks
  if (provider.value === 'azure') return form.azure.aks || form.azure.container_apps
  return false
})
const showsKubernetesPackaging = computed(() => {
  if (!hasKubernetesRuntime.value) return false
  if (provider.value === 'local') return true
  return infraGeneration.value.kubernetes.enabled
})

const stepTitle = computed(() => {
  if (currentStep.value === 1) return 'Choose infrastructure outputs'
  if (currentStep.value === 2) return 'Edit service resources'
  return 'Review and save workspace setup'
})

const selectedCloudServices = computed(() => {
  if (provider.value === 'gcp') {
    return enabledCloudServices('gcp', form.gcp as unknown as Record<string, unknown>)
  }
  if (provider.value === 'aws') {
    return enabledCloudServices('aws', form.aws as unknown as Record<string, unknown>)
  }
  if (provider.value === 'azure') {
    return enabledCloudServices('azure', form.azure as unknown as Record<string, unknown>)
  }
  if (provider.value === 'cloudflare') {
    return enabledCloudServices('cloudflare', form.cloudflare as unknown as Record<string, unknown>)
  }
  return []
})

function clearCredentials() {
  form.credentials.gcp_sa_key_json = ''
  form.credentials.gcp_wif_project_number = ''
  form.credentials.gcp_wif_pool_id = ''
  form.credentials.gcp_wif_provider_id = ''
  form.credentials.gcp_wif_target_sa_email = ''
  form.credentials.aws_access_key_id = ''
  form.credentials.aws_secret_access_key = ''
  form.credentials.aws_session_token = ''
  form.credentials.aws_role_arn = ''
  form.credentials.aws_role_session_name = ''
  form.credentials.azure_client_id = ''
  form.credentials.azure_client_secret = ''
  form.credentials.azure_tenant_id = ''
  form.credentials.azure_subscription_id = ''
  form.credentials.cloudflare_api_token = ''
}

function applyConfig(config: WorkspaceWizardConfig) {
  workspaceName.value = config.name
  provider.value = config.cloud.provider
  form.iac_engine = config.iac_engine
  form.run_init = config.run_init
  form.kubernetes_packaging = config.kubernetes_packaging
  form.cost_optimization = costOptimizationFromApi(
    config.cost_optimization as unknown as Record<string, unknown>,
  )
  if (config.container_scaffold) {
    Object.assign(form.container_scaffold, config.container_scaffold)
  }
  hasStoredCredentials.value = config.has_credentials
  clearCredentials()

  infraGeneration.value = artifactModeToInfraConfig(
    config.artifact_mode,
    config.iac_engine,
    config.kubernetes_packaging,
  )

  const resources = config.cloud.resources as Record<string, unknown>
  if (config.cloud.provider === 'local') {
    Object.assign(form.local, resources)
  } else if (config.cloud.provider === 'gcp') {
    Object.assign(form.gcp, resources)
  } else if (config.cloud.provider === 'aws') {
    Object.assign(form.aws, resources, {
      account_alias: ((resources.account_alias as string | null | undefined) ?? ''),
    })
  } else if (config.cloud.provider === 'azure') {
    Object.assign(form.azure, resources)
  } else {
    Object.assign(form.cloudflare, resources, {
      zone_name: ((resources.zone_name as string | null | undefined) ?? ''),
    })
  }
}

async function hydrateFromWorkspaceFiles() {
  detectionSummary.value = []
  try {
    const nodes = await listWorkspaceFiles(props.workspaceId)
    const paths = nodes.filter((n) => n.type === 'file').map((n) => n.path)
    const detected = detectWorkspaceInfraFromPaths(paths)
    if (detected.summary.length === 0) return

    let security = undefined
    if (detected.cicd.enabled && detected.cicd.samplePath) {
      try {
        const file = await readWorkspaceFile(props.workspaceId, detected.cicd.samplePath)
        security = inferCicdSecurityFromContent(file.content)
      } catch {
        // keep defaults when CI content cannot be read
      }
    }

    const merged = applyDetectedWorkspaceInfra(
      infraGeneration.value,
      form.container_scaffold,
      detected,
      security,
    )
    infraGeneration.value = merged.infra
    Object.assign(form.container_scaffold, merged.container)
    form.iac_engine = merged.infra.provision.engine
    form.kubernetes_packaging = infraConfigToKubernetesPackaging(merged.infra)
    detectionSummary.value = detected.summary
  } catch {
    // Wizard config alone is enough if the tree cannot be listed
  }
}

watch(
  infraGeneration,
  () => {
    form.iac_engine = infraGeneration.value.provision.engine
    form.kubernetes_packaging = infraConfigToKubernetesPackaging(infraGeneration.value)
    if (!showsKubernetesPackaging.value) {
      infraGeneration.value.kubernetes.enabled = false
      form.kubernetes_packaging = 'none'
    } else if (
      infraGeneration.value.kubernetes.enabled &&
      form.kubernetes_packaging === 'none'
    ) {
      form.kubernetes_packaging = 'raw_manifests'
    }
  },
  { deep: true },
)

function validateStep(): boolean {
  fieldError.value = null
  if (currentStep.value !== 2) return true
  if (provider.value === 'gcp' && form.gcp.project_id.trim().length < 3) {
    fieldError.value = 'GCP Project ID is required.'
    return false
  }
  if (provider.value === 'azure' && form.azure.resource_group.trim().length < 3) {
    fieldError.value = 'Azure resource group is required.'
    return false
  }
  if (provider.value === 'cloudflare' && form.cloudflare.account_id.trim().length < 8) {
    fieldError.value = 'Cloudflare account ID is required.'
    return false
  }
  return true
}

function nextStep() {
  if (!validateStep()) return
  if (currentStep.value < TOTAL_STEPS) currentStep.value += 1
}

function prevStep() {
  fieldError.value = null
  if (currentStep.value > 1) currentStep.value -= 1
}

function buildPayload(): ProvisioningWizardInput {
  const kubernetes_options = applyCostOptimizationToWorkloadOptions(
    {
      deployment: true,
      service: true,
      pod: false,
      job: false,
      cronjob: false,
      statefulset: false,
      daemonset: false,
      ingress: false,
      ingress_class: 'nginx' as const,
      install_ingress_nginx: false,
      config_map: false,
      secret: false,
      service_account: false,
      pvc: false,
      role: false,
      role_binding: false,
      hpa: false,
      vpa: false,
      pdb: false,
      network_policy: false,
      resource_quota: false,
      limit_range: false,
    },
    form.cost_optimization,
  )
  const base = {
    name: workspaceName.value,
    iac_engine: infraGeneration.value.provision.engine,
    credentials: form.credentials,
    run_init: form.run_init,
    kubernetes_packaging: infraConfigToKubernetesPackaging(infraGeneration.value),
    kubernetes_options,
    cost_optimization: form.cost_optimization,
    container_scaffold: containerScaffoldSchema.parse(form.container_scaffold),
  }
  const artifact_mode = infraConfigToArtifactMode(infraGeneration.value)

  if (provider.value === 'local') {
    return {
      ...base,
      provider: 'local',
      artifact_mode: 'manifest_only',
      kubernetes_packaging:
        base.kubernetes_packaging === 'none' ? 'raw_manifests' : base.kubernetes_packaging,
      resources: form.local,
    }
  }
  if (provider.value === 'gcp') {
    return { ...base, provider: 'gcp', artifact_mode, resources: form.gcp }
  }
  if (provider.value === 'aws') {
    return {
      ...base,
      provider: 'aws',
      artifact_mode,
      resources: { ...form.aws, account_alias: form.aws.account_alias || null },
    }
  }
  if (provider.value === 'azure') {
    return { ...base, provider: 'azure', artifact_mode, resources: form.azure }
  }
  return {
    ...base,
    provider: 'cloudflare',
    artifact_mode,
    resources: { ...form.cloudflare, zone_name: form.cloudflare.zone_name || null },
  }
}

async function onSave() {
  fieldError.value = null
  statusMessage.value = null
  saving.value = true
  try {
    const parsed = provisioningWizardSchema.safeParse(buildPayload())
    if (!parsed.success) {
      const issue = parsed.error.issues[0]
      const path = issue?.path?.length ? `${issue.path.join('.')}: ` : ''
      fieldError.value = `${path}${issue?.message ?? 'Invalid setup'}`
      return
    }

    await updateWorkspace(props.workspaceId, parsed.data)
    const dockerTargets = buildDockerScaffold(form.container_scaffold)
    for (const target of dockerTargets) {
      await writeWorkspaceFile(props.workspaceId, target.path, target.content)
    }
    if (infraGeneration.value.cicd.enabled) {
      const frameworks =
        infraGeneration.value.cicd.frameworks.length > 0
          ? infraGeneration.value.cicd.frameworks
          : (form.container_scaffold.frameworks ?? [])
      await syncWorkspaceCicdToPlatform(
        {
          listWorkspaceFiles,
          readWorkspaceFile,
          writeWorkspaceFile,
          deleteWorkspacePath,
        },
        props.workspaceId,
        infraGeneration.value.cicd.platform,
        {
          appName: form.container_scaffold.app_name || workspaceName.value || 'app',
          frameworks,
          security: infraGeneration.value.cicd.security,
        },
      )
    }
    hasStoredCredentials.value = true
    clearCredentials()
    statusMessage.value = 'Workspace setup updated.'
    emit('saved')
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'Failed to update workspace')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  loading.value = true
  fieldError.value = null
  try {
    const config = await getWizardConfig(props.workspaceId)
    applyConfig(config)
    await hydrateFromWorkspaceFiles()
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'Failed to load workspace setup')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="space-y-4 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-4">
    <div class="flex items-center gap-4">
      <div class="h-1 flex-1 overflow-hidden rounded-full bg-[var(--lp-line)]">
        <div
          class="h-full rounded-full bg-[var(--lp-accent)] transition-all duration-500"
          :style="{ width: `${progressPct}%` }"
        />
      </div>
      <span class="shrink-0 font-mono text-xs text-[var(--lp-accent)]">
        STEP {{ currentStep }}/{{ TOTAL_STEPS }}
      </span>
    </div>

    <div class="flex items-center justify-between gap-3">
      <div>
        <p class="lp-label">Service setup</p>
        <h2 class="text-lg font-semibold">{{ stepTitle }}</h2>
      </div>
      <span class="rounded border border-[var(--lp-line)] bg-[var(--lp-panel)] px-2 py-1 font-mono text-[10px] uppercase text-[var(--lp-muted)]">
        {{ provider }}
      </span>
    </div>

    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">Loading setup…</p>

    <template v-else>
      <div v-show="currentStep === 1" class="space-y-4">
        <div
          v-if="detectionSummary.length"
          class="rounded-xl border border-[var(--lp-accent)]/25 bg-[var(--lp-accent)]/5 px-3 py-2.5 text-sm text-[var(--lp-text)]"
        >
          <p class="font-medium text-[var(--lp-accent)]">Detected from workspace</p>
          <p class="mt-1 text-xs text-[var(--lp-muted)]">
            Toggles below were enabled from files already in this workspace.
          </p>
          <ul class="mt-2 flex flex-wrap gap-1.5">
            <li
              v-for="item in detectionSummary"
              :key="item"
              class="rounded-full border border-[var(--lp-line)] bg-[var(--lp-panel)] px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-muted)]"
            >
              {{ item }}
            </li>
          </ul>
        </div>
        <WorkspaceInfraActions
          v-model:config="infraGeneration"
          v-model:container-scaffold="form.container_scaffold"
          mode="selection"
          :provision-disabled="isLocalProvider"
          :kubernetes-disabled="!hasKubernetesRuntime"
          :disabled="saving"
        />
        <ContainerScaffoldCard
          v-if="form.container_scaffold.enabled"
          v-model="form.container_scaffold"
          :disabled="saving"
        />
        <CostOptimizationCard
          v-if="infraGeneration.kubernetes.enabled"
          v-model:cost="form.cost_optimization"
          :disabled="saving"
        />
      </div>

      <div v-show="currentStep === 2" class="space-y-4">
        <div
          v-if="hasStoredCredentials && !isLocalProvider"
          class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 p-3 text-sm text-[var(--lp-muted)]"
        >
          Credentials are already stored. Leave fields blank to keep them unchanged.
        </div>

        <template v-if="provider === 'local'">
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block space-y-2">
              <span class="lp-label">Kind cluster name</span>
              <input v-model="form.local.cluster_name" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">kubectl context</span>
              <input v-model="form.local.context" class="lp-input">
            </label>
          </div>
        </template>

        <template v-else-if="provider === 'gcp'">
          <label class="block space-y-2">
            <span class="lp-label">Project ID</span>
            <input v-model="form.gcp.project_id" class="lp-input">
          </label>
          <label class="block space-y-2">
            <span class="lp-label">Region</span>
            <select v-model="form.gcp.region" class="lp-input">
              <option v-for="region in GCP_REGIONS" :key="region.value" :value="region.value">
                {{ region.label }}
              </option>
            </select>
          </label>
          <label class="block space-y-2">
            <span class="lp-label">Instance / machine type</span>
            <select v-model="form.gcp.machine_type" class="lp-input font-mono text-xs">
              <option v-for="opt in GCP_MACHINE_TYPES" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </label>
          <div class="space-y-2">
            <p class="lp-label">Services to include in IaC</p>
            <label
              v-for="opt in GCP_SERVICE_OPTIONS"
              :key="opt.key"
              class="flex cursor-pointer items-center justify-between rounded-lg border border-[var(--lp-line)] p-3 transition hover:bg-[var(--lp-panel-2)]"
            >
              <div>
                <p class="text-sm font-medium">{{ opt.title }}</p>
                <p v-if="opt.desc" class="text-xs text-[var(--lp-muted)]">{{ opt.desc }}</p>
              </div>
              <input
                v-model="(form.gcp as Record<string, boolean | string>)[opt.key]"
                type="checkbox"
                class="h-5 w-5 accent-[var(--lp-accent)]"
              >
            </label>
          </div>
          <CloudCredentialsFields v-model:credentials="form.credentials" provider="gcp" />
        </template>

        <template v-else-if="provider === 'aws'">
          <label class="block space-y-2">
            <span class="lp-label">Region</span>
            <select v-model="form.aws.region" class="lp-input">
              <option v-for="region in AWS_REGIONS" :key="region.value" :value="region.value">
                {{ region.label }}
              </option>
            </select>
          </label>
          <label class="block space-y-2">
            <span class="lp-label">Instance type</span>
            <select v-model="form.aws.instance_type" class="lp-input font-mono text-xs">
              <option v-for="opt in AWS_INSTANCE_TYPES" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </label>
          <div class="space-y-2">
            <p class="lp-label">Services to include in IaC</p>
            <div class="grid gap-2 sm:grid-cols-2">
              <label
                v-for="opt in AWS_SERVICE_OPTIONS"
                :key="opt.key"
                class="flex cursor-pointer items-center justify-between rounded-lg border border-[var(--lp-line)] p-3"
              >
                <span class="text-sm">{{ opt.title }}</span>
                <input
                  v-model="(form.aws as Record<string, boolean | string | null>)[opt.key]"
                  type="checkbox"
                  class="h-5 w-5 accent-[var(--lp-accent)]"
                >
              </label>
            </div>
          </div>
          <CloudCredentialsFields v-model:credentials="form.credentials" provider="aws" />
        </template>

        <template v-else-if="provider === 'azure'">
          <label class="block space-y-2">
            <span class="lp-label">Resource group</span>
            <input v-model="form.azure.resource_group" class="lp-input">
          </label>
          <label class="block space-y-2">
            <span class="lp-label">Location</span>
            <select v-model="form.azure.location" class="lp-input">
              <option v-for="loc in AZURE_LOCATIONS" :key="loc.value" :value="loc.value">
                {{ loc.label }}
              </option>
            </select>
          </label>
          <label class="block space-y-2">
            <span class="lp-label">VM size</span>
            <select v-model="form.azure.vm_size" class="lp-input font-mono text-xs">
              <option v-for="opt in AZURE_VM_SIZES" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </label>
          <div class="space-y-2">
            <p class="lp-label">Services to include in IaC</p>
            <div class="grid gap-2 sm:grid-cols-2">
              <label
                v-for="opt in AZURE_SERVICE_OPTIONS"
                :key="opt.key"
                class="flex cursor-pointer items-center justify-between rounded-lg border border-[var(--lp-line)] p-3"
              >
                <span class="text-sm">{{ opt.title }}</span>
                <input
                  v-model="(form.azure as Record<string, boolean | string>)[opt.key]"
                  type="checkbox"
                  class="h-5 w-5 accent-[var(--lp-accent)]"
                >
              </label>
            </div>
          </div>
          <CloudCredentialsFields v-model:credentials="form.credentials" provider="azure" />
        </template>

        <template v-else>
          <label class="block space-y-2">
            <span class="lp-label">Account ID</span>
            <input v-model="form.cloudflare.account_id" class="lp-input">
          </label>
          <div class="space-y-2">
            <p class="lp-label">Services to include in IaC</p>
            <div class="grid gap-2 sm:grid-cols-2">
              <label
                v-for="opt in CLOUDFLARE_SERVICE_OPTIONS"
                :key="opt.key"
                class="flex cursor-pointer items-center justify-between rounded-lg border border-[var(--lp-line)] p-3"
              >
                <span class="text-sm">{{ opt.title }}</span>
                <input
                  v-model="(form.cloudflare as Record<string, boolean | string | null>)[opt.key]"
                  type="checkbox"
                  class="h-5 w-5 accent-[var(--lp-accent)]"
                >
              </label>
            </div>
          </div>
          <CloudCredentialsFields v-model:credentials="form.credentials" provider="cloudflare" />
        </template>
      </div>

      <div v-show="currentStep === 3" class="space-y-4">
        <dl class="grid gap-3 rounded-xl border border-[var(--lp-line)] p-4 text-sm sm:grid-cols-2">
          <div>
            <dt class="lp-label">Workspace</dt>
            <dd class="font-mono">{{ workspaceName }}</dd>
          </div>
          <div>
            <dt class="lp-label">Provider</dt>
            <dd class="font-mono uppercase">{{ provider }}</dd>
          </div>
          <div>
            <dt class="lp-label">Provision</dt>
            <dd class="font-mono">
              {{ infraGeneration.provision.enabled ? infraGeneration.provision.engine : 'skipped' }}
            </dd>
          </div>
          <div>
            <dt class="lp-label">Kubernetes</dt>
            <dd class="font-mono">
              {{ infraGeneration.kubernetes.enabled ? infraGeneration.kubernetes.mode : 'skipped' }}
            </dd>
          </div>
          <div>
            <dt class="lp-label">CI/CD</dt>
            <dd class="font-mono">
              {{
                infraGeneration.cicd.enabled
                  ? [
                      infraGeneration.cicd.platform,
                      infraGeneration.cicd.security.containerScan.enabled ? 'A' : null,
                      infraGeneration.cicd.security.sastGuardrails.enabled ? 'B' : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')
                  : 'skipped'
              }}
            </dd>
          </div>
          <div>
            <dt class="lp-label">Run init</dt>
            <dd class="font-mono">{{ form.run_init ? 'yes' : 'no' }}</dd>
          </div>
          <div class="sm:col-span-2">
            <dt class="lp-label">Cloud services in IaC</dt>
            <dd class="mt-1">
              <template v-if="provider === 'local'">
                <span class="font-mono text-xs text-[var(--lp-muted)]">local kind</span>
              </template>
              <template v-else-if="!selectedCloudServices.length">
                <span class="text-xs text-[var(--lp-muted)]">None selected</span>
              </template>
              <ul v-else class="mt-1 flex flex-wrap gap-1.5">
                <li
                  v-for="svc in selectedCloudServices"
                  :key="svc.key"
                  class="rounded-full border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/10 px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-accent)]"
                >
                  {{ svc.title }}
                </li>
              </ul>
            </dd>
          </div>
        </dl>
      </div>

      <p v-if="fieldError" class="text-sm text-[var(--lp-danger)]">{{ fieldError }}</p>
      <p v-if="statusMessage" class="text-sm text-[var(--lp-ok)]">{{ statusMessage }}</p>

      <div class="flex items-center justify-between gap-3 border-t border-[var(--lp-line)] pt-4">
        <div class="flex items-center gap-2">
          <button
            type="button"
            class="lp-btn-ghost"
            :class="{ invisible: currentStep === 1 }"
            @click="prevStep"
          >
            Back
          </button>
          <button
            type="button"
            class="lp-btn-ghost text-xs uppercase tracking-wide"
            :disabled="saving"
            @click="emit('cancel')"
          >
            Cancel
          </button>
        </div>
        <button
          v-if="currentStep < TOTAL_STEPS"
          type="button"
          class="lp-btn-primary"
          :disabled="saving"
          @click="nextStep"
        >
          Continue
        </button>
        <button
          v-else
          type="button"
          class="lp-btn-primary"
          :disabled="saving"
          @click="onSave"
        >
          {{ saving ? 'Saving…' : 'Save workspace setup' }}
        </button>
      </div>
    </template>
  </section>
</template>

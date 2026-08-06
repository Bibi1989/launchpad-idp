<script setup lang="ts">
import {
  githubRepoSchema,
  provisioningWizardSchema,
  containerScaffoldSchema,
  defaultContainerScaffold,
  defaultWorkloadDependencies,
  workloadDependenciesSchema,
  type ProvisioningWizardInput,
} from '~/utils/cloudValidation'
import type {
  CloudProvider,
  ContainerScaffoldConfig,
  CostOptimizationConfig,
  FrameworkOption,
  GitHubAppStatus,
  GitHubRepoResult,
  IaCBundleSummary,
  IaCEngine,
  InfraGenerationConfig,
  KubernetesPackaging,
  KubernetesWorkloadOptions,
  WorkspaceArtifactsMode,
  WorkspaceListItem,
  WorkspaceWizardConfig,
  WorkloadDependenciesConfig,
} from '~/types/provisioning'
import { defaultKubernetesWorkloadOptions } from '~/utils/cloudValidation'
import {
  applyCostOptimizationToWorkloadOptions,
  costOptimizationFromApi,
  defaultCostOptimizationConfig,
} from '~/utils/costOptimization'
import {
  artifactModeToInfraConfig,
  buildDockerScaffold,
  defaultInfraGenerationConfig,
  infraConfigToArtifactMode,
  infraConfigToKubernetesPackaging,
} from '~/utils/workspaceInfraScaffold'
import {
  detectRepoStackForScaffold,
  enhanceDockerScaffoldTargets,
} from '~/utils/workspaceRepoScaffold'
import { AWS_REGIONS, AZURE_LOCATIONS, AZURE_VM_SIZES, AWS_INSTANCE_TYPES, GCP_MACHINE_TYPES, GCP_REGIONS } from '~/utils/cloudRegions'

const {
  createWorkspace,
  updateWorkspace,
  openTerminal,
  createGithubRepo,
  getGithubAppStatus,
  getWizardConfig,
  getWorkspace,
  listWorkspaces,
  listWorkspaceFiles,
  readWorkspaceFile,
  writeWorkspaceFile,
  deleteWorkspacePath,
  analyzeWorkspaceFile,
} = useProvisioning()
const { t } = useI18n()
const { scanRepo } = useDockerfiles()
const terminalOpen = useState('lp-terminal-open', () => false)
const activeTerminalWsPath = useState<string | null>('lp-terminal-ws-path', () => null)

const TOTAL_STEPS = 4
const currentStep = ref(1)
const NEW_WORKSPACE = '__new__'

const existingWorkspaces = ref<WorkspaceListItem[]>([])
const selectedWorkspaceId = ref<string>(NEW_WORKSPACE)
/** Workspace created in this browser session; retries update it instead of creating again. */
const sessionCreatedWorkspaceId = ref<string | null>(null)
const hasStoredCredentials = ref(false)
const loadingConfig = ref(false)

const infraGeneration = ref<InfraGenerationConfig>(defaultInfraGenerationConfig({ isLocal: true }))

const form = reactive({
  name: '',
  iac_engine: 'terraform' as IaCEngine,
  provider: 'local' as CloudProvider,
  run_init: true,
  artifact_mode: 'manifest_only' as WorkspaceArtifactsMode,
  kubernetes_packaging: 'raw_manifests' as KubernetesPackaging,
  kubernetes_options: defaultKubernetesWorkloadOptions() as KubernetesWorkloadOptions,
  cost_optimization: defaultCostOptimizationConfig() as CostOptimizationConfig,
  container_scaffold: defaultContainerScaffold() as ContainerScaffoldConfig,
  dependencies: defaultWorkloadDependencies() as WorkloadDependenciesConfig,
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
  github: {
    name: '',
    description: 'Bootstrapped by Launchpad',
    private: true,
    installation_id: null as number | null,
    organization: '',
    set_cloud_secrets: false,
    include_workflow: true,
    include_dockerfiles: true,
    repo_mode: 'create' as 'create' | 'existing',
    existing_full_name: '',
  },
})

const fieldError = ref<string | null>(null)
const githubError = ref<string | null>(null)
const githubStatus = ref<string | null>(null)
const submitting = ref(false)
const creationStep = ref(1)
const creationStepLabel = computed(() => {
  if (creationStep.value === 1) return t('provision.steps.validate')
  if (creationStep.value === 2) return t('provision.steps.generate')
  if (creationStep.value === 3) return t('provision.steps.sandbox')
  if (creationStep.value === 4) return isLocalProvider.value ? t('provision.cta.startLocal') : t('provision.cta.generateLaunch')
  return t('provision.generate')
})
const creationProgressPercent = computed(() => Math.min(creationStep.value * 25, 100))
const creationSteps = computed(() => [
  { key: 1, label: t('provision.steps.validate') },
  { key: 2, label: t('provision.steps.generate') },
  { key: 3, label: t('provision.steps.sandbox') },
  { key: 4, label: isLocalProvider.value ? t('provision.cta.startLocal') : t('provision.cta.generateLaunch') },
])
const bundle = ref<IaCBundleSummary | null>(null)
const wsPath = ref<string | null>(null)
const githubResult = ref<GitHubRepoResult | null>(null)
const githubApp = ref<GitHubAppStatus | null>(null)

const isNewWorkspace = computed(() => selectedWorkspaceId.value === NEW_WORKSPACE)
const selectedExisting = computed(() =>
  existingWorkspaces.value.find((ws) => ws.id === selectedWorkspaceId.value) ?? null,
)

const hasKubernetesRuntime = computed(() => {
  if (form.provider === 'local') return true
  if (form.provider === 'gcp') return form.gcp.gke || form.gcp.cloud_run
  if (form.provider === 'aws') return form.aws.eks
  if (form.provider === 'azure') return form.azure.aks || form.azure.container_apps
  return false
})

const showsKubernetesPackaging = computed(() => {
  if (!hasKubernetesRuntime.value) return false
  if (form.provider === 'local') return form.artifact_mode !== 'iac_only'
  return infraGeneration.value.kubernetes.enabled
})

const isLocalProvider = computed(() => form.provider === 'local')

const packagingSectionEl = ref<HTMLElement | null>(null)

watch(showsKubernetesPackaging, async (visible) => {
  if (!visible) {
    if (form.provider !== 'local') {
      infraGeneration.value.kubernetes.enabled = false
      form.kubernetes_packaging = 'none'
      form.kubernetes_options = defaultKubernetesWorkloadOptions()
    }
    return
  }
  if (form.kubernetes_packaging === 'none') {
    form.kubernetes_packaging = 'raw_manifests'
    infraGeneration.value.kubernetes.enabled = true
    form.kubernetes_options = defaultKubernetesWorkloadOptions()
  }
  await nextTick()
  packagingSectionEl.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
})

function packagingFromK8sMode(mode: InfraGenerationConfig['kubernetes']['mode']): KubernetesPackaging {
  if (mode === 'helm') return 'helm'
  if (mode === 'kustomize') return 'kustomize'
  return 'raw_manifests'
}

function syncFormFromInfraGeneration() {
  if (form.provider === 'local') {
    form.artifact_mode = 'manifest_only'
    form.kubernetes_packaging = packagingFromK8sMode(infraGeneration.value.kubernetes.mode)
    return
  }
  form.artifact_mode = infraConfigToArtifactMode(infraGeneration.value)
  form.iac_engine = infraGeneration.value.provision.engine
  form.kubernetes_packaging = infraConfigToKubernetesPackaging(infraGeneration.value)
}

watch(
  infraGeneration,
  () => {
    syncFormFromInfraGeneration()
  },
  { deep: true },
)

watch(
  () => form.kubernetes_packaging,
  (packaging) => {
    if (packaging === 'helm') {
      infraGeneration.value.kubernetes.mode = 'helm'
    } else if (packaging === 'kustomize') {
      infraGeneration.value.kubernetes.mode = 'kustomize'
    } else if (packaging === 'raw_manifests') {
      infraGeneration.value.kubernetes.mode = 'k8s'
    }
  },
)

watch(
  () => form.provider,
  (provider) => {
    if (provider === 'local') {
      infraGeneration.value = defaultInfraGenerationConfig({ isLocal: true })
      form.github.set_cloud_secrets = false
      return
    }
    if (!infraGeneration.value.provision.enabled && !infraGeneration.value.kubernetes.enabled) {
      infraGeneration.value = defaultInfraGenerationConfig()
    } else {
      infraGeneration.value = {
        ...infraGeneration.value,
        provision: { ...infraGeneration.value.provision, enabled: true },
      }
    }
  },
)

onMounted(async () => {
  try {
    existingWorkspaces.value = await listWorkspaces()
  } catch {
    existingWorkspaces.value = []
  }
  try {
    githubApp.value = await getGithubAppStatus()
    applyGithubDefaults(githubApp.value)
  } catch {
    githubApp.value = {
      configured: false,
      message: t('provision.github.loadStatusFailed'),
      installations: [],
    }
  }
})

function applyGithubDefaults(status: GitHubAppStatus) {
  const defaultId = status.default_installation_id
  if (defaultId && !form.github.installation_id) {
    form.github.installation_id = defaultId
  } else if (status.installations.length === 1 && !form.github.installation_id) {
    form.github.installation_id = status.installations[0]!.id
  }
}

function onGithubAppUpdated(status: GitHubAppStatus) {
  githubApp.value = status
  applyGithubDefaults(status)
}

const providers = computed(() => [
  {
    id: 'local' as CloudProvider,
    label: t('provision.providers.local'),
    badge: 'LOCAL',
    icon: 'developer_board',
    blurb: t('provision.sandbox.blurb'),
  },
  {
    id: 'aws' as CloudProvider,
    label: t('provision.providers.aws'),
    badge: 'AWS',
    icon: 'cloud',
    blurb: t('provision.providerBlurbs.aws'),
  },
  {
    id: 'gcp' as CloudProvider,
    label: t('provision.providers.gcp'),
    badge: 'GCP',
    icon: 'deployed_code',
    blurb: t('provision.providerBlurbs.gcp'),
  },
  {
    id: 'azure' as CloudProvider,
    label: t('provision.providers.azure'),
    badge: 'AZURE',
    icon: 'grid_view',
    blurb: t('provision.providerBlurbs.azure'),
  },
  {
    id: 'cloudflare' as CloudProvider,
    label: t('provision.providers.cloudflare'),
    badge: 'EDGE',
    icon: 'bolt',
    blurb: t('provision.providerBlurbs.cloudflare'),
  },
])

const progressPct = computed(() => (currentStep.value / TOTAL_STEPS) * 100)

const stepTitle = computed(() => {
  switch (currentStep.value) {
    case 1:
      return t('provision.stepTitles.step1')
    case 2:
      return t('provision.stepTitles.step2', { provider: form.provider.toUpperCase() })
    case 3:
      return t('integrations.github')
    default:
      return isNewWorkspace.value ? t('provision.stepTitles.step3New') : t('provision.stepTitles.step3Existing')
  }
})

const gcpResourceOptions = computed(() =>
  ([
    'vpc',
    'subnets',
    'gke',
    'artifact_registry',
    'cloud_run',
    'cloud_functions',
    'cloud_sql',
    'cloud_storage',
    'pubsub',
    'memorystore',
    'bigquery',
  ] as const).map((key) => ({
    key,
    title: t(`provision.resources.gcp.${key}.title`),
    desc: t(`provision.resources.gcp.${key}.desc`),
  })),
)

const awsResourceOptions = computed(() =>
  ([
    'vpc',
    'subnets',
    'ec2',
    's3',
    'eks',
    'secrets_manager',
    'rds',
    'ecr',
    'elasticache',
    'lambda_fn',
    'dynamodb',
    'sqs',
    'alb',
  ] as const).map((key) => ({
    key,
    title: t(`provision.resources.aws.${key}.title`),
  })),
)

const azureResourceOptions = computed(() =>
  ([
    'vnet',
    'subnets',
    'aks',
    'key_vault',
    'container_apps',
    'acr',
    'storage_account',
    'cosmos_db',
    'redis_cache',
    'app_service',
    'log_analytics',
  ] as const).map((key) => ({
    key,
    title: t(`provision.resources.azure.${key}.title`),
  })),
)

const cloudflareResourceOptions = computed(() =>
  ([
    'workers',
    'r2',
    'dns_records',
    'pages',
    'kv',
    'd1',
    'tunnels',
    'queues',
  ] as const).map((key) => ({
    key,
    title: t(`provision.resources.cloudflare.${key}.title`),
  })),
)

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

function applyWizardConfig(config: WorkspaceWizardConfig) {
  form.name = config.name
  form.iac_engine = config.iac_engine
  form.run_init = config.run_init
  form.artifact_mode = config.artifact_mode
  form.kubernetes_packaging = config.kubernetes_packaging
  infraGeneration.value = artifactModeToInfraConfig(
    config.artifact_mode,
    config.iac_engine,
    config.kubernetes_packaging,
  )
  form.kubernetes_options = {
    ...defaultKubernetesWorkloadOptions(),
    ...config.kubernetes_options,
  }
  form.cost_optimization = costOptimizationFromApi(
    config.cost_optimization as unknown as Record<string, unknown>,
  )
  if (config.container_scaffold) {
    Object.assign(form.container_scaffold, config.container_scaffold)
  }
  if (config.dependencies) {
    Object.assign(form.dependencies, config.dependencies)
  }
  form.provider = config.cloud.provider
  hasStoredCredentials.value = config.has_credentials
  clearCredentials()

  const resources = config.cloud.resources as Record<string, unknown>
  if (config.cloud.provider === 'local') {
    Object.assign(form.local, {
      cluster_name: 'launchpad',
      context: 'kind-launchpad',
      ...resources,
    })
    if (form.kubernetes_packaging === 'none') {
      form.kubernetes_packaging = 'raw_manifests'
    }
    form.github.set_cloud_secrets = false
  } else if (config.cloud.provider === 'gcp') {
    Object.assign(form.gcp, {
      vpc: true,
      subnets: true,
      network_topology: 'simple',
      gke: false,
      artifact_registry: false,
      secret_backend: 'secret_manager',
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
      ...resources,
    })
  } else if (config.cloud.provider === 'aws') {
    const accountAlias = (resources.account_alias as string | null | undefined) ?? ''
    Object.assign(form.aws, {
      vpc: true,
      subnets: true,
      network_topology: 'simple',
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
      ...resources,
      account_alias: accountAlias,
    })
  } else if (config.cloud.provider === 'azure') {
    Object.assign(form.azure, {
      vnet: true,
      subnets: true,
      network_topology: 'simple',
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
      ...resources,
    })
  } else {
    const zoneName = (resources.zone_name as string | null | undefined) ?? ''
    Object.assign(form.cloudflare, {
      workers: false,
      r2: false,
      dns_records: false,
      pages: false,
      kv: false,
      d1: false,
      tunnels: false,
      queues: false,
      account_id: '',
      ...resources,
      zone_name: zoneName,
    })
  }
}

watch(selectedWorkspaceId, async (id) => {
  if (id === NEW_WORKSPACE) {
    sessionCreatedWorkspaceId.value = null
    hasStoredCredentials.value = false
    clearCredentials()
    return
  }
  loadingConfig.value = true
  fieldError.value = null
  try {
    const config = await getWizardConfig(id)
    applyWizardConfig(config)
  } catch (err) {
    const ws = existingWorkspaces.value.find((item) => item.id === id)
    if (ws) {
      form.name = ws.name
      if (ws.provider === 'local' || ws.provider === 'gcp' || ws.provider === 'aws' || ws.provider === 'azure' || ws.provider === 'cloudflare') {
        form.provider = ws.provider
      }
      if (ws.engine === 'terraform' || ws.engine === 'opentofu' || ws.engine === 'pulumi') {
        form.iac_engine = ws.engine
      }
    }
    fieldError.value = err instanceof Error ? err.message : t('provision.errors.loadFailed')
  } finally {
    loadingConfig.value = false
  }
})

watch(
  () => form.github.installation_id,
  (id) => {
    const match = githubApp.value?.installations.find((item) => item.id === id)
    if (match && match.account_type === 'Organization') {
      form.github.organization = match.account_login
    }
  },
)

function buildWizardPayload(): ProvisioningWizardInput {
  const syncedOptions = applyCostOptimizationToWorkloadOptions(
    form.kubernetes_options,
    form.cost_optimization,
  )
  form.kubernetes_options = syncedOptions
  const base = {
    name: form.name,
    iac_engine: form.iac_engine,
    credentials: form.credentials,
    run_init: form.run_init,
    kubernetes_packaging: form.kubernetes_packaging,
    kubernetes_options: syncedOptions,
    cost_optimization: form.cost_optimization,
    container_scaffold: containerScaffoldSchema.parse(form.container_scaffold),
    dependencies: workloadDependenciesSchema.parse(form.dependencies),
  }
  if (form.provider === 'local') {
    return {
      ...base,
      provider: 'local',
      resources: form.local,
      artifact_mode: 'manifest_only',
      kubernetes_packaging:
        form.kubernetes_packaging === 'none' ? 'raw_manifests' : form.kubernetes_packaging,
    }
  }
  if (form.provider === 'gcp') {
    return { ...base, provider: 'gcp', resources: form.gcp, artifact_mode: form.artifact_mode }
  }
  if (form.provider === 'aws') {
    return {
      ...base,
      provider: 'aws',
      artifact_mode: form.artifact_mode,
      resources: { ...form.aws, account_alias: form.aws.account_alias || null },
    }
  }
  if (form.provider === 'azure') {
    return { ...base, provider: 'azure', resources: form.azure, artifact_mode: form.artifact_mode }
  }
  return {
    ...base,
    provider: 'cloudflare',
    artifact_mode: form.artifact_mode,
    resources: { ...form.cloudflare, zone_name: form.cloudflare.zone_name || null },
  }
}

function validateStep(): boolean {
  fieldError.value = null
  if (currentStep.value === 1) {
    if (!isNewWorkspace.value) {
      if (!selectedExisting.value) {
        fieldError.value = t('provision.errors.selectWorkspace')
        return false
      }
      return true
    }
    if (!form.name.trim() || form.name.trim().length < 3) {
      fieldError.value = t('provision.errors.workspaceNameMin')
      return false
    }
    if (!/^[a-z][a-z0-9-]*$/.test(form.name.trim().toLowerCase())) {
      fieldError.value = t('provision.errors.workspaceNameFormat')
      return false
    }
  }
  if (currentStep.value === 2) {
    if (form.provider === 'local') {
      if (!form.local.cluster_name.trim() || !form.local.context.trim()) {
        fieldError.value = t('provision.errors.localClusterRequired')
        return false
      }
      if (!infraGeneration.value.kubernetes.enabled) {
        fieldError.value = t('provision.errors.enableK8sGeneration')
        return false
      }
      return true
    }
    if (!infraGeneration.value.provision.enabled && !infraGeneration.value.kubernetes.enabled) {
      fieldError.value = t('provision.errors.enableProvisionOrK8s')
      return false
    }
    if (form.provider === 'gcp' && form.gcp.project_id.trim().length < 3) {
      fieldError.value = t('provision.errors.gcpProjectRequired')
      return false
    }
    if (form.provider === 'azure' && form.azure.resource_group.trim().length < 3) {
      fieldError.value = t('provision.errors.azureResourceGroupRequired')
      return false
    }
    if (form.provider === 'cloudflare' && form.cloudflare.account_id.trim().length < 8) {
      fieldError.value = t('provision.errors.cloudflareAccountRequired')
      return false
    }
  }
  return true
}

function nextStep() {
  if (!validateStep()) return
  if (currentStep.value < TOTAL_STEPS) {
    currentStep.value += 1
  }
}

function prevStep() {
  fieldError.value = null
  if (currentStep.value > 1) {
    currentStep.value -= 1
  }
}

async function scaffoldCiCdFiles(workspaceId: string) {
  if (!infraGeneration.value.cicd.enabled) return
  const frameworks =
    infraGeneration.value.cicd.frameworks.length > 0
      ? infraGeneration.value.cicd.frameworks
      : (form.container_scaffold.frameworks ?? [])
  const { syncWorkspaceCicdToPlatform } = await import('~/utils/syncWorkspaceCicd')
  await syncWorkspaceCicdToPlatform(
    {
      listWorkspaceFiles,
      readWorkspaceFile,
      writeWorkspaceFile,
      deleteWorkspacePath,
    },
    workspaceId,
    infraGeneration.value.cicd.platform,
    {
      appName: form.container_scaffold.app_name || form.name || 'app',
      frameworks,
      security: infraGeneration.value.cicd.security,
    },
  )
}

async function scaffoldDockerFiles(
  workspaceId: string,
  scaffold: ContainerScaffoldConfig = form.container_scaffold,
) {
  if (!scaffold.enabled) return
  let resolvedScaffold = { ...scaffold }
  if (
    form.github.repo_mode === 'existing'
    && form.github.installation_id
    && form.github.existing_full_name.trim()
  ) {
    const detected = await detectRepoStackForScaffold(scanRepo, {
      installationId: form.github.installation_id,
      fullName: form.github.existing_full_name.trim(),
    })
    if (detected) {
      resolvedScaffold = {
        ...resolvedScaffold,
        stack: detected,
        frameworks: resolvedScaffold.frameworks?.length ? resolvedScaffold.frameworks : [detected],
      }
    }
  }
  let targets = buildDockerScaffold(resolvedScaffold)
  targets = await enhanceDockerScaffoldTargets(workspaceId, targets, analyzeWorkspaceFile)
  for (const target of targets) {
    await writeWorkspaceFile(workspaceId, target.path, target.content)
  }
}

async function refreshBundle(workspaceId: string) {
  try {
    bundle.value = await getWorkspace(workspaceId)
  } catch {
    // Keep the prior bundle summary if refresh fails.
  }
}

async function recoverCreatedWorkspaceByName(name: string): Promise<string | null> {
  try {
    existingWorkspaces.value = await listWorkspaces()
    const match = existingWorkspaces.value.find((ws) => ws.name === name)
    if (!match) return null
    sessionCreatedWorkspaceId.value = match.id
    return match.id
  } catch {
    return null
  }
}

async function onGenerate() {
  if (submitting.value) return
  fieldError.value = null
  githubError.value = null
  githubStatus.value = null
  githubResult.value = null
  submitting.value = true
  try {
    let workspaceId: string
    syncFormFromInfraGeneration()
    const parsed = provisioningWizardSchema.safeParse(buildWizardPayload())
    if (!parsed.success) {
      const issue = parsed.error.issues[0]
      const path = issue?.path?.length ? `${issue.path.join('.')}: ` : ''
      fieldError.value = `${path}${issue?.message ?? t('provision.errors.invalidForm')}`
      return
    }

    creationStep.value = 1
    const reuseId =
      (!isNewWorkspace.value && selectedExisting.value?.id) ||
      sessionCreatedWorkspaceId.value ||
      null

    if (reuseId) {
      workspaceId = reuseId
      creationStep.value = 2
      bundle.value = await updateWorkspace(workspaceId, parsed.data)
      await scaffoldDockerFiles(workspaceId, parsed.data.container_scaffold)
      await scaffoldCiCdFiles(workspaceId)
      await refreshBundle(workspaceId)
      creationStep.value = 3
      const terminal = await openTerminal(workspaceId, {
        run_init: form.run_init,
      })
      wsPath.value = terminal.ws_path
      activeTerminalWsPath.value = terminal.ws_path
      terminalOpen.value = false
      if (selectedWorkspaceId.value !== workspaceId) {
        selectedWorkspaceId.value = workspaceId
      }
    } else {
      creationStep.value = 2
      bundle.value = await createWorkspace(parsed.data)
      workspaceId = bundle.value.workspace_id
      sessionCreatedWorkspaceId.value = workspaceId
      creationStep.value = 3
      await scaffoldDockerFiles(workspaceId, parsed.data.container_scaffold)
      await scaffoldCiCdFiles(workspaceId)
      await refreshBundle(workspaceId)
      if (!form.github.name.trim()) {
        form.github.name = `launchpad-${parsed.data.name}`
      }
      creationStep.value = 4
      const terminal = await openTerminal(workspaceId, {
        run_init: form.run_init,
      })
      wsPath.value = terminal.ws_path
      activeTerminalWsPath.value = terminal.ws_path
      terminalOpen.value = false
      existingWorkspaces.value = await listWorkspaces()
      selectedWorkspaceId.value = workspaceId
    }

    if (shouldPushGithub()) {
      await pushGithubBootstrap(workspaceId)
    }
    hasStoredCredentials.value = true
    clearCredentials()

    if (workspaceId) {
      setTimeout(() => {
        void navigateTo(`/workspaces/${workspaceId}`)
      }, 1200)
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : t('provision.errors.failed')
    const timedOut = message.toLowerCase().includes('timed out')
    if (timedOut && isNewWorkspace.value && !sessionCreatedWorkspaceId.value && form.name.trim()) {
      const recoveredId = await recoverCreatedWorkspaceByName(form.name.trim())
      if (recoveredId) {
        selectedWorkspaceId.value = recoveredId
        fieldError.value = t('provision.errors.timeoutCreated')
        return
      }
    }
    if (timedOut && sessionCreatedWorkspaceId.value) {
      fieldError.value = t('provision.errors.timeoutContinue')
      return
    }
    fieldError.value = message
  } finally {
    submitting.value = false
  }
}

function shouldPushGithub(): boolean {
  if (!form.github.installation_id) return false
  if (form.github.repo_mode === 'existing') {
    return Boolean(form.github.existing_full_name.trim())
  }
  return Boolean(form.github.name.trim())
}

async function pushGithubBootstrap(workspaceId: string) {
  githubError.value = null
  githubStatus.value = null
  githubResult.value = null

  const selectedInstall =
    githubApp.value?.installations.find((item) => item.id === form.github.installation_id) ?? null
  const organization =
    selectedInstall?.account_type.toLowerCase() === 'organization'
      ? selectedInstall.account_login
      : form.github.organization || null

  const existingFullName =
    form.github.repo_mode === 'existing' ? form.github.existing_full_name.trim() || null : null

  const parsed = githubRepoSchema.safeParse({
    name: form.github.name,
    description: form.github.description,
    private: form.github.private,
    installation_id: form.github.installation_id,
    organization,
    workspace_id: workspaceId,
    set_cloud_secrets: form.github.set_cloud_secrets,
    include_workflow: form.github.include_workflow,
    include_dockerfiles: form.github.include_dockerfiles,
    existing_full_name: existingFullName,
  })
  if (!parsed.success) {
    const issue = parsed.error.issues[0]
    const path = issue?.path?.length ? `${issue.path.join('.')}: ` : ''
    githubError.value = `${path}${issue?.message ?? t('provision.errors.invalidGithubForm')}`
    fieldError.value = githubError.value
    return
  }

  githubStatus.value = form.github.include_workflow
    ? t('provision.github.savingInfraWorkflow')
    : t('provision.github.savingInfra')
  try {
    githubResult.value = await createGithubRepo(parsed.data, form.credentials)
    const workflowNote = githubResult.value.workflow_path
      ? ` · ${githubResult.value.workflow_path}`
      : ` · ${t('provision.github.workflowSkipped')}`
    githubStatus.value = githubResult.value.created
      ? `${t('provision.github.repoCreated')}${workflowNote}`
      : `${t('provision.github.infraPushed', { repo: githubResult.value.full_name })}${workflowNote}`
  } catch (err) {
    githubError.value = err instanceof Error ? err.message : t('provision.github.provisioningFailed')
    githubStatus.value = null
    fieldError.value = githubError.value
  }
}

async function onPrimaryAction() {
  if (currentStep.value < TOTAL_STEPS) {
    nextStep()
    return
  }
  await onGenerate()
}
</script>

<template>
  <div class="mx-auto max-w-4xl animate-fade-up space-y-6 pb-10">
    <header class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <p class="lp-label mb-1">{{ t('provision.eyebrow') }}</p>
        <h1 class="text-2xl font-semibold tracking-tight text-[var(--lp-text)] sm:text-3xl">
          {{ t('provision.title') }}
        </h1>
        <p class="mt-2 max-w-2xl text-sm text-[var(--lp-muted)]">
          {{ t('provision.blurb') }}
        </p>
      </div>
      <NuxtLink to="/workspaces" class="lp-btn-ghost">
        <span class="material-symbols-outlined text-base">arrow_back</span>
        {{ t('nav.workspaces') }}
      </NuxtLink>
    </header>

    <div class="space-y-6 rounded-2xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/80 p-6 sm:p-8">
      <!-- Progress -->
      <div class="flex items-center gap-4">
        <div class="h-1 flex-1 overflow-hidden rounded-full bg-[var(--lp-line)]">
          <div
            class="h-full rounded-full bg-[var(--lp-accent)] transition-all duration-500"
            :style="{ width: `${progressPct}%` }"
          />
        </div>
        <span class="shrink-0 font-mono text-xs text-[var(--lp-accent)]">
          {{ t('provision.stepProgress', { current: currentStep, total: TOTAL_STEPS }) }}
        </span>
      </div>

      <!-- Steps -->
      <div>
        <h2 class="mb-6 flex items-center gap-2 text-lg font-semibold">
          <span class="material-symbols-outlined text-[var(--lp-accent)]">settings_suggest</span>
          {{ stepTitle }}
        </h2>

        <!-- Step 1: Cloud + workspace -->
        <div v-show="currentStep === 1" class="space-y-6">
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block space-y-2 sm:col-span-2">
              <span class="lp-label">{{ t('provision.workspace') }}</span>
              <select v-model="selectedWorkspaceId" class="lp-input">
                <option :value="NEW_WORKSPACE">{{ t('provision.createNew') }}</option>
                <option
                  v-for="ws in existingWorkspaces"
                  :key="ws.id"
                  :value="ws.id"
                >
                  {{ ws.name }} · {{ ws.provider }}/{{ ws.engine }}
                </option>
              </select>
              <p class="text-xs text-[var(--lp-muted)]">
                {{ t('provision.workspaceSelectBlurb') }}
              </p>
            </label>

            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.workspaceName') }}</span>
              <input
                v-model="form.name"
                class="lp-input"
                placeholder="demo-stack"
                autocomplete="off"
                :disabled="!isNewWorkspace || loadingConfig"
              >
            </label>
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.iacEngine') }}</span>
              <select
                v-model="form.iac_engine"
                class="lp-input"
                :disabled="loadingConfig || isLocalProvider"
              >
                <option value="terraform">Terraform</option>
                <option value="opentofu">OpenTofu</option>
                <option value="pulumi">Pulumi</option>
              </select>
              <p v-if="isLocalProvider" class="text-xs text-[var(--lp-muted)]">
                {{ t('provision.iacEngineLocalHint') }}
              </p>
            </label>
          </div>

          <div
            v-if="!isNewWorkspace && selectedExisting"
            class="rounded-xl border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/5 p-4 text-sm text-[var(--lp-muted)]"
          >
            {{ t('provision.editingExisting') }}
            <strong class="text-[var(--lp-text)]">{{ selectedExisting.name }}</strong>
            - {{ t('provision.editingExistingSuffix') }}
          </div>

          <div class="grid gap-4 sm:grid-cols-2">
            <button
              v-for="p in providers"
              :key="p.id"
              type="button"
              class="group rounded-xl border p-5 text-left transition active:scale-[0.98]"
              :class="
                form.provider === p.id
                  ? 'border-2 border-[var(--lp-accent)] bg-[var(--lp-panel-2)]'
                  : 'border-[var(--lp-line)] bg-[var(--lp-panel)]/60 hover:border-[var(--lp-accent)]/50 hover:bg-[var(--lp-panel-2)]/60'
              "
              :disabled="loadingConfig"
              @click="form.provider = p.id"
            >
              <div class="mb-4 flex items-center justify-between">
                <span
                  class="material-symbols-outlined text-3xl"
                  :class="form.provider === p.id ? 'text-[var(--lp-accent)]' : 'text-[var(--lp-muted)] group-hover:text-[var(--lp-accent)]'"
                >
                  {{ p.icon }}
                </span>
                <span
                  class="rounded px-2 py-1 font-mono text-[10px] uppercase tracking-wide"
                  :class="
                    form.provider === p.id
                      ? 'bg-[var(--lp-accent)] text-[var(--lp-ink)]'
                      : 'bg-[var(--lp-panel-2)] text-[var(--lp-muted)]'
                  "
                >
                  {{ p.badge }}
                </span>
              </div>
              <h3 class="text-base font-semibold">{{ p.label }}</h3>
              <p class="mt-2 text-xs text-[var(--lp-muted)]">{{ p.blurb }}</p>
            </button>
          </div>
        </div>

        <!-- Step 2: Resources -->
        <div v-show="currentStep === 2" class="space-y-4">
          <div
            v-if="hasStoredCredentials && !isLocalProvider"
            class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/50 p-4 text-sm text-[var(--lp-muted)]"
          >
            {{ t('provision.credentialsStored') }}
          </div>

          <template v-if="form.provider === 'gcp'">
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.projectId') }}</span>
              <input v-model="form.gcp.project_id" class="lp-input" placeholder="my-gcp-project">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.region') }}</span>
              <select v-model="form.gcp.region" class="lp-input">
                <option v-for="region in GCP_REGIONS" :key="region.value" :value="region.value">
                  {{ region.label }}
                </option>
              </select>
            </label>
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.instanceMachineType') }}</span>
              <select v-model="form.gcp.machine_type" class="lp-input font-mono text-xs">
                <option v-for="opt in GCP_MACHINE_TYPES" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </option>
              </select>
            </label>
            <label v-if="form.gcp.vpc && form.gcp.subnets" class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.networkTopology') }}</span>
              <select v-model="form.gcp.network_topology" class="lp-input">
                <option value="simple">{{ t('provision.topology.simple') }}</option>
                <option value="standard">{{ t('provision.topology.standard') }}</option>
              </select>
            </label>
            <div class="space-y-2">
              <label
                v-for="opt in gcpResourceOptions"
                :key="opt.key"
                class="flex cursor-pointer items-center justify-between rounded-lg border border-[var(--lp-line)] p-4 transition hover:bg-[var(--lp-panel-2)]"
              >
                <div class="flex items-center gap-3">
                  <span class="material-symbols-outlined text-[var(--lp-muted)]">lan</span>
                  <div>
                    <p class="text-sm font-medium">{{ opt.title }}</p>
                    <p class="text-xs text-[var(--lp-muted)]">{{ opt.desc }}</p>
                  </div>
                </div>
                <input v-model="form.gcp[opt.key]" type="checkbox" class="h-5 w-5 accent-[var(--lp-accent)]">
              </label>
            </div>
          </template>

          <template v-else-if="form.provider === 'aws'">
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.region') }}</span>
              <select v-model="form.aws.region" class="lp-input">
                <option v-for="region in AWS_REGIONS" :key="region.value" :value="region.value">
                  {{ region.label }}
                </option>
              </select>
            </label>
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.instanceType') }}</span>
              <select v-model="form.aws.instance_type" class="lp-input font-mono text-xs">
                <option v-for="opt in AWS_INSTANCE_TYPES" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </option>
              </select>
            </label>
            <label v-if="form.aws.vpc && form.aws.subnets" class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.networkTopology') }}</span>
              <select v-model="form.aws.network_topology" class="lp-input">
                <option value="simple">{{ t('provision.topology.simple') }}</option>
                <option value="standard">{{ t('provision.topology.standard') }}</option>
              </select>
            </label>
            <div class="grid gap-2 sm:grid-cols-2">
              <label
                v-for="opt in awsResourceOptions"
                :key="opt.key"
                class="flex cursor-pointer items-center justify-between rounded-lg border border-[var(--lp-line)] p-3"
              >
                <span class="text-sm">{{ opt.title }}</span>
                <input v-model="form.aws[opt.key]" type="checkbox" class="h-5 w-5 accent-[var(--lp-accent)]">
              </label>
            </div>
          </template>

          <template v-else-if="form.provider === 'azure'">
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.resourceGroup') }}</span>
              <input v-model="form.azure.resource_group" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.location') }}</span>
              <select v-model="form.azure.location" class="lp-input">
                <option v-for="loc in AZURE_LOCATIONS" :key="loc.value" :value="loc.value">
                  {{ loc.label }}
                </option>
              </select>
            </label>
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.vmSize') }}</span>
              <select v-model="form.azure.vm_size" class="lp-input font-mono text-xs">
                <option v-for="opt in AZURE_VM_SIZES" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </option>
              </select>
            </label>
            <label v-if="form.azure.vnet && form.azure.subnets" class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.networkTopology') }}</span>
              <select v-model="form.azure.network_topology" class="lp-input">
                <option value="simple">{{ t('provision.topology.simple') }}</option>
                <option value="standard">{{ t('provision.topology.standard') }}</option>
              </select>
            </label>
            <div class="grid gap-2 sm:grid-cols-2">
              <label
                v-for="opt in azureResourceOptions"
                :key="opt.key"
                class="flex cursor-pointer items-center justify-between rounded-lg border border-[var(--lp-line)] p-3"
              >
                <span class="text-sm">{{ opt.title }}</span>
                <input v-model="form.azure[opt.key]" type="checkbox" class="h-5 w-5 accent-[var(--lp-accent)]">
              </label>
            </div>
          </template>

          <template v-else-if="form.provider === 'cloudflare'">
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.accountId') }}</span>
              <input v-model="form.cloudflare.account_id" class="lp-input">
            </label>
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.fields.zoneNameOptional') }}</span>
              <input v-model="form.cloudflare.zone_name" class="lp-input" placeholder="example.com">
            </label>
            <div class="grid gap-2 sm:grid-cols-3">
              <label
                v-for="opt in cloudflareResourceOptions"
                :key="opt.key"
                class="flex cursor-pointer items-center justify-between rounded-lg border border-[var(--lp-line)] p-3"
              >
                <span class="text-sm">{{ opt.title }}</span>
                <input v-model="form.cloudflare[opt.key]" type="checkbox" class="h-5 w-5 accent-[var(--lp-accent)]">
              </label>
            </div>
          </template>

          <div v-if="!isLocalProvider" class="rounded-xl border border-[var(--lp-line)] p-4">
            <p class="lp-label mb-3">{{ t('provision.infraGeneration') }}</p>
            <WorkspaceInfraActions
              v-model:config="infraGeneration"
              v-model:container-scaffold="form.container_scaffold"
              mode="selection"
              :provision-disabled="isLocalProvider"
              :kubernetes-disabled="!hasKubernetesRuntime"
              :disabled="loadingConfig"
            />
          </div>

          <ContainerScaffoldCard
            v-if="form.container_scaffold.enabled && !isLocalProvider"
            v-model="form.container_scaffold"
            :disabled="loadingConfig"
          />

          <template v-if="isLocalProvider">
            <div
              class="rounded-xl border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/5 p-4 text-sm text-[var(--lp-muted)]"
            >
              <p class="font-medium text-[var(--lp-text)]">{{ t('provision.sandbox.title') }}</p>
              <ol class="mt-2 list-decimal space-y-1 pl-5">
                <li>{{ t('provision.sandboxSteps.kindUp') }}</li>
                <li>
                  {{ t('provision.sandboxSteps.envVars', { context: form.local.context }) }}
                </li>
                <li>{{ t('provision.sandboxSteps.applyDestroy') }}</li>
                <li>{{ t('provision.sandboxSteps.switchProvider') }}</li>
              </ol>
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <label class="block space-y-2">
                <span class="lp-label">{{ t('provision.fields.clusterName') }}</span>
                <input v-model="form.local.cluster_name" class="lp-input" placeholder="launchpad">
              </label>
              <label class="block space-y-2">
                <span class="lp-label">{{ t('provision.fields.kubectlContext') }}</span>
                <input v-model="form.local.context" class="lp-input" placeholder="kind-launchpad">
              </label>
            </div>
            <div ref="packagingSectionEl">
              <WorkspaceInfraActions
                v-model:config="infraGeneration"
                v-model:container-scaffold="form.container_scaffold"
                mode="selection"
                provision-disabled
                :disabled="loadingConfig"
              />
              <ContainerScaffoldCard
                v-if="form.container_scaffold.enabled"
                v-model="form.container_scaffold"
                class="mt-4"
                :disabled="loadingConfig"
              />
              <KubernetesPackagingPicker
                v-model:packaging="form.kubernetes_packaging"
                v-model:options="form.kubernetes_options"
                :allow-none="false"
                class="mt-4"
              />
              <WorkloadDependenciesPicker
                v-model:dependencies="form.dependencies"
                provider="local"
                class="mt-4"
                :disabled="loadingConfig"
              />
              <CostOptimizationCard
                v-model:cost="form.cost_optimization"
                class="mt-4"
                :disabled="loadingConfig"
              />
            </div>
          </template>

          <template v-if="form.provider === 'gcp'">
            <div v-if="showsKubernetesPackaging" ref="packagingSectionEl">
              <KubernetesPackagingPicker
                v-model:packaging="form.kubernetes_packaging"
                v-model:options="form.kubernetes_options"
                :allow-none="false"
              />
              <WorkloadDependenciesPicker
                v-model:dependencies="form.dependencies"
                provider="gcp"
                :gcp-cloud-sql="form.gcp.cloud_sql"
                :gcp-memorystore="form.gcp.memorystore"
                class="mt-4"
                :disabled="loadingConfig"
              />
              <CostOptimizationCard
                v-model:cost="form.cost_optimization"
                class="mt-4"
                :disabled="loadingConfig"
              />
            </div>
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.secrets.backend') }}</span>
              <select v-model="form.gcp.secret_backend" class="lp-input">
                <option value="secret_manager">{{ t('provision.secrets.manager') }}</option>
                <option value="native_k8s">{{ t('provision.secrets.k8s') }}</option>
              </select>
            </label>
            <CloudCredentialsFields
              v-model:credentials="form.credentials"
              provider="gcp"
              :sa-placeholder="hasStoredCredentials ? t('provision.credentialsHints.leaveBlank') : t('provision.credentialsHints.pasteSaJson')"
            />
          </template>

          <template v-if="form.provider === 'aws'">
            <div v-if="showsKubernetesPackaging" ref="packagingSectionEl">
              <KubernetesPackagingPicker
                v-model:packaging="form.kubernetes_packaging"
                v-model:options="form.kubernetes_options"
                :allow-none="false"
              />
              <WorkloadDependenciesPicker
                v-model:dependencies="form.dependencies"
                provider="aws"
                :aws-rds="form.aws.rds"
                :aws-elasticache="form.aws.elasticache"
                class="mt-4"
                :disabled="loadingConfig"
              />
              <CostOptimizationCard
                v-model:cost="form.cost_optimization"
                class="mt-4"
                :disabled="loadingConfig"
              />
            </div>
            <CloudCredentialsFields v-model:credentials="form.credentials" provider="aws" />
          </template>

          <template v-if="form.provider === 'azure'">
            <div v-if="showsKubernetesPackaging" ref="packagingSectionEl">
              <KubernetesPackagingPicker
                v-model:packaging="form.kubernetes_packaging"
                v-model:options="form.kubernetes_options"
                :allow-none="false"
              />
              <WorkloadDependenciesPicker
                v-model:dependencies="form.dependencies"
                provider="azure"
                :azure-cosmos-db="form.azure.cosmos_db"
                :azure-redis-cache="form.azure.redis_cache"
                class="mt-4"
                :disabled="loadingConfig"
              />
              <CostOptimizationCard
                v-model:cost="form.cost_optimization"
                class="mt-4"
                :disabled="loadingConfig"
              />
            </div>
            <CloudCredentialsFields v-model:credentials="form.credentials" provider="azure" />
          </template>

          <template v-if="form.provider === 'cloudflare'">
            <CloudCredentialsFields v-model:credentials="form.credentials" provider="cloudflare" />
          </template>
        </div>

        <!-- Step 3: GitHub -->
        <div v-show="currentStep === 3" class="space-y-6">
          <GithubConnectCard
            v-model:model-installation-id="form.github.installation_id"
            v-model:model-repo-name="form.github.name"
            v-model:model-repo-mode="form.github.repo_mode"
            v-model:model-repo-full-name="form.github.existing_full_name"
            show-repo-picker
            compact
            @updated="onGithubAppUpdated"
          />

          <div class="flex flex-wrap gap-4">
            <label class="flex items-center gap-2 text-sm">
              <input v-model="form.github.private" type="checkbox" class="accent-[var(--lp-accent)]">
              {{ t('provision.github.privateRepo') }}
            </label>
            <label class="flex items-center gap-2 text-sm">
              <input
                v-model="form.github.set_cloud_secrets"
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :disabled="isLocalProvider"
              >
              {{ t('provision.github.setCloudSecrets') }}
            </label>
            <label class="flex items-center gap-2 text-sm">
              <input v-model="form.github.include_workflow" type="checkbox" class="accent-[var(--lp-accent)]">
              {{ t('provision.github.addWorkflow') }}
            </label>
            <label class="flex items-center gap-2 text-sm">
              <input v-model="form.github.include_dockerfiles" type="checkbox" class="accent-[var(--lp-accent)]">
              {{ t('provision.github.addDockerfiles') }}
            </label>
          </div>
          <div class="flex items-start gap-3 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel)] p-4">
            <span class="material-symbols-outlined text-[var(--lp-ok)]">verified_user</span>
            <p class="text-sm text-[var(--lp-muted)]">
              {{ t('provision.github.repoBlurb') }}
            </p>
          </div>
          <p v-if="githubError" class="text-sm text-[var(--lp-danger)]">{{ githubError }}</p>
          <p v-else-if="githubStatus" class="text-sm text-[var(--lp-ok)]">{{ githubStatus }}</p>
          <a
            v-if="githubResult"
            :href="githubResult.html_url"
            target="_blank"
            rel="noreferrer"
            class="block font-mono text-xs text-[var(--lp-accent)] hover:underline"
          >
            {{ githubResult.full_name }}<template v-if="githubResult.workflow_path"> · {{ githubResult.workflow_path }}</template>
          </a>
        </div>

        <!-- Step 4: Generate -->
        <div v-show="currentStep === 4" class="space-y-8">
          <div class="rounded-2xl border border-[var(--lp-accent)]/20 bg-[var(--lp-panel-2)]/60 p-6">
            <h3 class="text-lg font-semibold">{{ t('provision.review.title') }}</h3>
            <dl class="mt-4 grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt class="lp-label">{{ t('provision.workspace') }}</dt>
                <dd class="font-mono">
                  {{ isNewWorkspace ? (form.name || '-') : selectedExisting?.name || '-' }}
                  <span v-if="!isNewWorkspace" class="text-[var(--lp-muted)]"> {{ t('provision.review.existing') }}</span>
                </dd>
              </div>
              <div>
                <dt class="lp-label">{{ t('provision.review.provider') }}</dt>
                <dd class="font-mono uppercase">{{ form.provider }}</dd>
              </div>
              <div>
                <dt class="lp-label">{{ t('provision.review.engine') }}</dt>
                <dd class="font-mono">{{ form.iac_engine }}</dd>
              </div>
              <div>
                <dt class="lp-label">{{ t('provision.review.artifacts') }}</dt>
                <dd class="font-mono">{{ form.artifact_mode }}</dd>
              </div>
              <div>
                <dt class="lp-label">{{ t('provision.review.provision') }}</dt>
                <dd class="font-mono">
                  {{
                    infraGeneration.provision.enabled
                      ? infraGeneration.provision.engine
                      : t('provision.review.skipped')
                  }}
                </dd>
              </div>
              <div>
                <dt class="lp-label">{{ t('provision.review.kubernetes') }}</dt>
                <dd class="font-mono">
                  {{
                    infraGeneration.kubernetes.enabled
                      ? infraGeneration.kubernetes.mode
                      : t('provision.review.skipped')
                  }}
                </dd>
              </div>
              <div>
                <dt class="lp-label">{{ t('provision.review.cicd') }}</dt>
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
                      : t('provision.review.skipped')
                  }}
                </dd>
              </div>
              <div>
                <dt class="lp-label">{{ t('provision.review.githubRepo') }}</dt>
                <dd class="font-mono">
                  <template v-if="form.github.repo_mode === 'existing' && form.github.existing_full_name">
                    {{ form.github.existing_full_name }} {{ t('provision.review.import') }}
                  </template>
                  <template v-else-if="form.github.name">
                    {{ form.github.name }} {{ t('provision.review.create') }}
                  </template>
                  <template v-else>{{ t('provision.review.skip') }}</template>
                </dd>
              </div>
              <div>
                <dt class="lp-label">{{ t('provision.review.deployWorkflow') }}</dt>
                <dd class="font-mono">
                  {{ form.github.include_workflow && (form.github.name || form.github.existing_full_name) ? t('common.yes') : t('common.no') }}
                </dd>
              </div>
              <div>
                <dt class="lp-label">{{ t('provision.review.dockerFiles') }}</dt>
                <dd class="font-mono">
                  {{ form.github.include_dockerfiles && (form.github.name || form.github.existing_full_name) ? t('common.yes') : t('common.no') }}
                </dd>
              </div>
            </dl>
          </div>

          <label class="flex items-center gap-3 rounded-lg border border-[var(--lp-line)] p-4">
            <input v-model="form.run_init" type="checkbox" class="h-5 w-5 accent-[var(--lp-accent)]">
            <div>
              <p class="text-sm font-medium">
                {{ isLocalProvider ? t('provision.runInit.localTitle') : t('provision.runInit.cloudTitle') }}
              </p>
              <p class="text-xs text-[var(--lp-muted)]">
                <template v-if="isLocalProvider">
                  {{ t('provision.runInit.localBlurb') }}
                </template>
                <template v-else>
                  {{ t('provision.runInit.cloudBlurb') }}
                </template>
              </p>
            </div>
          </label>

          <p v-if="githubStatus" class="text-sm text-[var(--lp-ok)]">{{ githubStatus }}</p>
          <a
            v-if="githubResult"
            :href="githubResult.html_url"
            target="_blank"
            rel="noreferrer"
            class="block font-mono text-xs text-[var(--lp-accent)] hover:underline"
          >
            {{ githubResult.full_name }}<template v-if="githubResult.workflow_path"> · {{ githubResult.workflow_path }}</template>
          </a>

          <!-- Graphical Creation Progress Card -->
          <div v-if="submitting || bundle" class="mt-4 space-y-4 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/90 p-5 shadow-2xl animate-fade-up">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-3">
                <div class="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]">
                  <span v-if="submitting" class="material-symbols-outlined animate-spin text-lg">sync</span>
                  <span v-else class="material-symbols-outlined text-lg text-[var(--lp-ok)]">check_circle</span>
                </div>
                <div>
                  <h3 class="text-sm font-semibold text-[var(--lp-text)]">
                    {{ submitting ? creationStepLabel : t('provision.progress.workspaceReady') }}
                  </h3>
                  <p class="text-xs text-[var(--lp-muted)]">
                    {{ submitting ? t('provision.progress.configuring') : t('provision.progress.openingWorkspace', { id: bundle?.workspace_id }) }}
                  </p>
                </div>
              </div>
              <span v-if="submitting" class="rounded-full border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/10 px-3 py-1 font-mono text-xs text-[var(--lp-accent)]">
                {{ creationProgressPercent }}%
              </span>
              <span v-else class="rounded-full border border-[var(--lp-ok)]/30 bg-[var(--lp-ok)]/10 px-3 py-1 font-mono text-xs text-[var(--lp-ok)]">
                {{ t('provision.progress.ready') }}
              </span>
            </div>

            <!-- Animated Progress Bar -->
            <div class="h-1.5 w-full overflow-hidden rounded-full bg-[var(--lp-line)]">
              <div
                class="h-full bg-gradient-to-r from-[var(--lp-accent)] to-[var(--lp-ok)] transition-all duration-500"
                :style="{ width: `${submitting ? creationProgressPercent : 100}%` }"
              />
            </div>

            <!-- Realtime Graphical Step List -->
            <div class="grid gap-2 text-xs font-mono">
              <div
                v-for="(st, idx) in creationSteps"
                :key="st.key"
                class="flex items-center gap-2.5 rounded-lg border px-3 py-2 transition-all"
                :class="{
                  'border-[var(--lp-accent)]/40 bg-[var(--lp-accent)]/5 text-[var(--lp-text)]': creationStep === idx + 1 && submitting,
                  'border-[var(--lp-ok)]/30 bg-[var(--lp-ok)]/5 text-[var(--lp-ok)]': creationStep > idx + 1 || !submitting,
                  'border-[var(--lp-line)] opacity-40 text-[var(--lp-muted)]': creationStep < idx + 1 && submitting
                }"
              >
                <span v-if="creationStep > idx + 1 || !submitting" class="material-symbols-outlined text-sm text-[var(--lp-ok)]">check</span>
                <span v-else-if="creationStep === idx + 1 && submitting" class="material-symbols-outlined animate-spin text-sm text-[var(--lp-accent)]">sync</span>
                <span v-else class="material-symbols-outlined text-sm text-[var(--lp-muted)]">radio_button_unchecked</span>
                <span>{{ st.label }}</span>
              </div>
            </div>
          </div>

          <div v-if="bundle" class="space-y-3 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 p-4 font-mono text-xs text-[var(--lp-muted)]">
            <p class="text-[var(--lp-ok)]">{{ t('provision.progress.workspaceReady') }}</p>
            <p>{{ t('provision.progress.workspaceId', { id: bundle.workspace_id }) }}</p>
            <p>{{ t('provision.progress.filesSummary', { engine: bundle.engine, provider: bundle.provider, count: bundle.files.length }) }}</p>
            <NuxtLink
              :to="`/workspaces/${bundle.workspace_id}`"
              class="inline-block rounded border border-[var(--lp-accent)]/50 px-3 py-1.5 text-[var(--lp-accent)] transition hover:bg-[var(--lp-accent)]/10"
            >
              {{ t('provision.progress.openWorkspace') }}
            </NuxtLink>
          </div>

          <ClientOnly>
            <TerminalPanel v-if="wsPath" :ws-path="wsPath" />
          </ClientOnly>
        </div>

        <p v-if="fieldError" class="mt-4 text-sm text-[var(--lp-danger)]">{{ fieldError }}</p>
      </div>

      <!-- Footer -->
      <div class="flex items-center justify-between border-t border-[var(--lp-line)] pt-6">
        <button
          type="button"
          class="lp-btn-ghost"
          :class="{ invisible: currentStep === 1 }"
          @click="prevStep"
        >
          <span class="material-symbols-outlined text-base">arrow_back</span>
          {{ t('common.back') }}
        </button>
        <button
          type="button"
          class="lp-btn-primary px-8"
          :class="currentStep === TOTAL_STEPS ? 'bg-[var(--lp-ok)]' : ''"
          :disabled="submitting || loadingConfig"
          @click="onPrimaryAction"
        >
          <template v-if="currentStep < TOTAL_STEPS">
            <span>{{ t('provision.continue') }}</span>
            <span class="material-symbols-outlined text-base">arrow_forward</span>
          </template>
          <template v-else>
            <span>{{
              submitting
                ? t('provision.cta.busy')
                : isNewWorkspace
                  ? t('provision.generate')
                  : t('provision.saveOpenTerminal')
            }}</span>
            <span class="material-symbols-outlined text-base">rocket_launch</span>
          </template>
        </button>
      </div>
    </div>
  </div>
</template>

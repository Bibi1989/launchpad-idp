<script setup lang="ts">
import type { AuditLogEntry, Environment, PreviewLaunchPayload } from '~/types/environment'
import { ApiError } from '~/composables/useApi'
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'
import { applyEnvStreamPatch } from '~/utils/envStreamPatch'
import {
  resolvePreviewEndpoints,
  secondaryPreviewEndpoints,
} from '~/utils/previewEndpoints'
import { recommendPrimaryService } from '~/utils/cloudPromote'
import { resolveCloudPromoteDeployTargets } from '~/utils/cloudPromoteDeployTargets'
import type { ContainerServiceItem, WorkspaceRuntimeMode } from '~/types/provisioning'
import {
  defaultRegionForProvider,
  regionsForProvider,
  coerceRegionForProvider,
  isRegionForProvider,
} from '~/utils/cloudRegions'
import { emptyCloudCredentials, defaultImageSecurityScanConfig } from '~/utils/cloudValidation'
import {
  ttlCanExtend,
  ttlIsExpired,
  ttlLeftSeconds,
} from '~/utils/previewTtl'

function preferredRegionForProvider(
  provider: CloudProvider,
  status: UserCloudCredentialsStatus | null | undefined,
): string | null {
  if (!status) return null
  let raw: string | null = null
  if (provider === 'gcp') raw = (status.gcp_region || '').trim() || null
  else if (provider === 'aws') raw = (status.aws_region || '').trim() || null
  else if (provider === 'azure') raw = (status.azure_location || '').trim() || null
  if (!raw) return null
  return isRegionForProvider(provider, raw) ? raw : null
}

type CloudProvider = Exclude<PreviewLaunchPayload['provider'], 'local'>

const { t } = useI18n()
const route = useRoute()
const id = computed(() => String(route.params.id))
const environmentId = computed(() => id.value || null)
const { getById, destroy, cancelProvision, extendTtl, promoteToCloud, listAudits, scanDrift, retryProvision, pauseEnvironment, resumeEnvironment, relaunchEnvironment } = useEnvironments()
const { stagePromote } = usePromotions()
const { getWizardConfig } = useProvisioning()
const { createOrLinkJiraIssue } = useOrgIntegrations()
const { reconcileEnvironment } = useNotifications()
const toast = useToast()
const seenNotices = new Set<string>()
const jiraPending = ref(false)
const {
  open: analyzerOpen,
  loading: analyzing,
  analyzeEnvironment,
} = usePreviewAnalyzer()

const environment = ref<Environment | null>(null)
const loadError = ref<string | null>(null)
const loading = ref(true)
const confirmDestroyOpen = ref(false)

const { define } = useAsyncAction()

const pauseAction = define(() => pauseEnvironment(environment.value!.id), {
  success: (env) => ({ title: t('environments.toasts.paused'), message: `${env.name} was paused.` }),
  error: (err) => ({ title: t('environments.toasts.pauseFailed'), message: toastError(err, t('common.failed')) }),
  onSuccess: (env) => { environment.value = env; loadError.value = null },
  onError: (msg) => { loadError.value = msg },
})

const resumeAction = define(() => resumeEnvironment(environment.value!.id), {
  success: (env) => ({ title: t('environments.toasts.resumed'), message: `${env.name} is resuming.` }),
  error: (err) => ({ title: t('environments.toasts.resumeFailed'), message: toastError(err, t('common.failed')) }),
  onSuccess: (env) => { environment.value = env; loadError.value = null },
  onError: (msg) => { loadError.value = msg },
})

const showPromote = ref(false)
const tick = ref(0)
const copied = ref(false)
const audits = ref<AuditLogEntry[]>([])
const auditsLoading = ref(false)
const actionsMenuOpen = ref(false)

const { getStatus: getUserCloudCredentialsStatus, listNetworks, listSecurityGroups } = useUserCloudCredentials()
const storedCredentialsStatus = ref<UserCloudCredentialsStatus | null>(null)
const storedCredentialsLoading = ref(false)
const useStoredCredentials = ref(false)

const promoteCredentials = reactive(emptyCloudCredentials())

const promoteForm = reactive({
  provider: 'gcp' as CloudProvider,
  code_source: 'ssh' as 'ssh' | 'github',
  region: defaultRegionForProvider('gcp'),
  network_mode: 'existing' as 'existing' | 'create' | 'default',
  existing_vpc_id: '' as string,
  security_group_mode: 'auto' as 'auto' | 'existing',
  existing_security_group_id: '' as string,
  create_vpc: false,
  create_subnets: false,
  image_scan: defaultImageSecurityScanConfig(),
})

type CloudNetworkOption = {
  id: string
  name: string
  cidr?: string | null
  is_default?: boolean
}
const promoteNetworks = ref<CloudNetworkOption[]>([])
const promoteNetworksLoading = ref(false)
const promoteNetworksError = ref<string | null>(null)

type CloudSecurityGroupOption = {
  id: string
  name: string
  vpc_id?: string | null
  description?: string | null
}
const promoteSecurityGroups = ref<CloudSecurityGroupOption[]>([])
const promoteSecurityGroupsLoading = ref(false)
const promoteSecurityGroupsError = ref<string | null>(null)

const promoteServices = ref<ContainerServiceItem[]>([])
const promotePrimaryService = ref<string | null>(null)
const promoteServicesLoading = ref(false)
const promoteServicesError = ref<string | null>(null)
const promoteProcessStrategy = ref<string>('docker')
const promoteRuntimeMode = ref<WorkspaceRuntimeMode>('kubernetes')

const promoteRecommendedService = computed(() => recommendPrimaryService(promoteServices.value))
const showPromoteServicePicker = computed(() => promoteServices.value.length > 1)
const showPromoteCodeSource = computed(
  () =>
    promoteRuntimeMode.value !== 'kubernetes'
    && (promoteProcessStrategy.value === 'pm2' || promoteProcessStrategy.value === 'systemd'),
)
const promoteRegionOptions = computed(() => regionsForProvider(promoteForm.provider))
const showPromoteRegion = computed(() => promoteRegionOptions.value.length > 0)
const showPromoteNetworking = computed(
  () => promoteForm.provider === 'gcp' || promoteForm.provider === 'aws' || promoteForm.provider === 'azure',
)

const promoteDeployTargets = computed(() => {
  const vpc = promoteNetworks.value.find((n) => n.id === promoteForm.existing_vpc_id)
  const sg = promoteSecurityGroups.value.find((s) => s.id === promoteForm.existing_security_group_id)
  return resolveCloudPromoteDeployTargets({
    provider: promoteForm.provider,
    runtimeMode: promoteRuntimeMode.value,
    region: promoteForm.region,
    networkMode: promoteForm.network_mode,
    createSubnets: promoteForm.create_subnets,
    existingVpcId: promoteForm.existing_vpc_id,
    existingVpcLabel: vpc ? `${vpc.name}${vpc.cidr ? ` (${vpc.cidr})` : ''}` : null,
    securityGroupMode: promoteForm.security_group_mode,
    existingSecurityGroupId: promoteForm.existing_security_group_id,
    existingSecurityGroupLabel: sg ? `${sg.name} (${sg.id})` : null,
    processStrategy: promoteProcessStrategy.value,
  })
})

watch(
  () => promoteForm.create_subnets,
  (on) => {
    if (on) {
      promoteForm.create_vpc = true
      promoteForm.network_mode = 'create'
    }
  },
)

watch(
  () => promoteForm.create_vpc,
  (on) => {
    if (!on) promoteForm.create_subnets = false
  },
)

watch(
  () => promoteForm.network_mode,
  (mode) => {
    if (mode === 'create') {
      promoteForm.create_vpc = true
      promoteForm.existing_vpc_id = ''
    } else if (mode === 'existing') {
      promoteForm.create_vpc = false
      promoteForm.create_subnets = false
    } else {
      promoteForm.create_vpc = false
      promoteForm.create_subnets = false
      promoteForm.existing_vpc_id = ''
    }
  },
)

async function loadPromoteNetworks() {
  promoteNetworksError.value = null
  promoteNetworks.value = []
  if (!showPromoteNetworking.value) return
  if (promoteForm.provider === 'azure') return
  const hasCreds =
    useStoredCredentials.value && hasStoredCredsForProvider(promoteForm.provider)
  if (!hasCreds && !useStoredCredentials.value) {
    // Still try vault if user has stored creds even when checkbox off for paste mode
  }
  promoteNetworksLoading.value = true
  try {
    const region = coerceRegionForProvider(promoteForm.provider, promoteForm.region)
    if (region !== promoteForm.region) {
      promoteForm.region = region
    }
    const result = await listNetworks({
      provider: promoteForm.provider,
      region,
    })
    promoteNetworks.value = result.networks || []
    if (
      promoteForm.network_mode === 'existing'
      && !promoteForm.existing_vpc_id
      && promoteNetworks.value.length
    ) {
      const preferred =
        promoteNetworks.value.find((n) => n.is_default) ?? promoteNetworks.value[0]
      if (preferred) promoteForm.existing_vpc_id = preferred.id
    }
    if (!promoteNetworks.value.length && promoteForm.network_mode === 'existing') {
      promoteForm.network_mode = 'create'
      promoteForm.create_vpc = true
    }
  } catch (err) {
    promoteNetworksError.value = err instanceof Error ? err.message : t('common.failed')
  } finally {
    promoteNetworksLoading.value = false
  }
}

async function loadPromoteSecurityGroups() {
  promoteSecurityGroupsError.value = null
  promoteSecurityGroups.value = []
  if (promoteForm.provider !== 'aws') return
  promoteSecurityGroupsLoading.value = true
  try {
    const region = coerceRegionForProvider(promoteForm.provider, promoteForm.region)
    const vpcId =
      promoteForm.network_mode === 'existing' && promoteForm.existing_vpc_id
        ? promoteForm.existing_vpc_id
        : null
    const result = await listSecurityGroups({
      provider: 'aws',
      region,
      vpc_id: vpcId,
    })
    promoteSecurityGroups.value = result.security_groups || []
    if (
      promoteForm.security_group_mode === 'existing'
      && !promoteForm.existing_security_group_id
      && promoteSecurityGroups.value.length
    ) {
      const firstSg = promoteSecurityGroups.value[0]
      if (firstSg) promoteForm.existing_security_group_id = firstSg.id
    }
  } catch (err) {
    promoteSecurityGroupsError.value = err instanceof Error ? err.message : t('common.failed')
  } finally {
    promoteSecurityGroupsLoading.value = false
  }
}

watch(
  () => [promoteForm.provider, promoteForm.region, useStoredCredentials.value, storedCredentialsStatus.value] as const,
  () => {
    void loadPromoteNetworks()
  },
)

async function loadPromoteServices() {
  promoteServicesError.value = null
  promoteServices.value = []
  promotePrimaryService.value = null
  promoteProcessStrategy.value = 'docker'
  const workspaceId = environment.value?.workspace_id
  if (!workspaceId) return
  promoteServicesLoading.value = true
  try {
    const config = await getWizardConfig(workspaceId)
    const services = [...(config.container_scaffold?.services ?? [])]
    promoteServices.value = services
    promotePrimaryService.value = recommendPrimaryService(services)
    promoteProcessStrategy.value = config.running_instance?.process_strategy || 'docker'
    promoteRuntimeMode.value = config.runtime_mode || 'kubernetes'
    promoteForm.code_source = (config.running_instance?.code_source as 'ssh' | 'github') || 'ssh'
    const fromConfig = (config.running_instance?.region || '').trim()
    const fromVault = preferredRegionForProvider(promoteForm.provider, storedCredentialsStatus.value)
    promoteForm.region = coerceRegionForProvider(
      promoteForm.provider,
      fromConfig,
      fromVault,
    )
  } catch (err) {
    promoteServicesError.value = err instanceof Error ? err.message : t('common.failed')
  } finally {
    promoteServicesLoading.value = false
  }
}

function clearPromoteCredentials() {
  Object.assign(promoteCredentials, emptyCloudCredentials())
}

function promoteCredentialsEmpty() {
  return Object.values(promoteCredentials).every((v) => !String(v ?? '').trim())
}

function hasStoredCredsForProvider(provider: CloudProvider) {
  if (!storedCredentialsStatus.value) return false
  if (provider === 'gcp') return storedCredentialsStatus.value.has_gcp
  if (provider === 'aws') return storedCredentialsStatus.value.has_aws
  if (provider === 'azure') return storedCredentialsStatus.value.has_azure
  return storedCredentialsStatus.value.has_cloudflare
}

const storedCredsLabel = computed(() => {
  const s = storedCredentialsStatus.value
  if (!s) return null
  if (promoteForm.provider === 'gcp') return s.gcp_label ?? null
  if (promoteForm.provider === 'aws') return s.aws_label ?? null
  if (promoteForm.provider === 'azure') return s.azure_label ?? null
  return s.cloudflare_label ?? null
})

async function refreshStoredCredentialsForPromotion() {
  if (!showPromote.value) return
  storedCredentialsLoading.value = true
  try {
    storedCredentialsStatus.value = await getUserCloudCredentialsStatus()
  } finally {
    storedCredentialsLoading.value = false
  }

  // If user hasn't typed anything yet, default to the encrypted vault.
  if (hasStoredCredsForProvider(promoteForm.provider) && promoteCredentialsEmpty()) {
    useStoredCredentials.value = true
    clearPromoteCredentials()
  } else {
    useStoredCredentials.value = false
  }
}

watch(
  showPromote,
  (open) => {
    if (open) {
      void (async () => {
        await refreshStoredCredentialsForPromotion()
        await loadPromoteServices()
      })()
    }
  },
)

watch(
  () => [
    promoteForm.provider,
    promoteForm.region,
    promoteForm.network_mode,
    promoteForm.existing_vpc_id,
    promoteForm.security_group_mode,
  ] as const,
  () => {
    if (promoteForm.provider === 'aws' && promoteForm.security_group_mode === 'existing') {
      void loadPromoteSecurityGroups()
    } else {
      promoteSecurityGroups.value = []
      promoteSecurityGroupsError.value = null
    }
  },
)

watch(
  () => promoteForm.provider,
  (provider) => {
    if (provider !== 'aws') {
      promoteForm.security_group_mode = 'auto'
      promoteForm.existing_security_group_id = ''
    }
    if (!showPromote.value) return
    const preferred = preferredRegionForProvider(provider, storedCredentialsStatus.value)
    promoteForm.region = coerceRegionForProvider(provider, promoteForm.region, preferred)
    // Switching providers should reset the "use stored" toggle only when the
    // user hasn't typed creds yet.
    if (promoteCredentialsEmpty() && hasStoredCredsForProvider(promoteForm.provider)) {
      useStoredCredentials.value = true
      clearPromoteCredentials()
    } else {
      useStoredCredentials.value = false
    }
  },
)

watch(useStoredCredentials, (enabled) => {
  if (enabled) clearPromoteCredentials()
})

const { lines, connected, done, connect } = useEnvironmentLogStream(environmentId)

useEnvironmentLiveStream(environmentId, {
  onEvent: (event) => {
    if (!environment.value) return
    applyEnvStreamPatch(environment.value, event)
    reconcileEnvironment(environment.value)
    if (event.notice && !seenNotices.has(event.notice)) {
      seenNotices.add(event.notice)
      toast.info(t('environments.toasts.portRemap'), event.notice)
    }
    // Soft REST refresh for cost / audits once provision settles.
    if (
      event.type === 'STATUS_CHANGE'
      && (event.status === 'RUNNING' || event.status === 'FAILED' || event.status === 'DESTROYED')
    ) {
      void load({ softAudits: true })
    }
    if (event.status === 'DESTROYED') {
      toast.info(t('environments.toasts.destroyed'), t('environments.toasts.destroyComplete'))
      void navigateTo('/environments')
    }
  },
})

const remainingLabel = computed(() => {
  tick.value
  if (!environment.value) return '-'
  if (environment.value.ttl_disabled || !environment.value.ttl_expires_at) {
    return t('environments.detail.noTtl')
  }
  return formatDuration(ttlLeftSeconds(environment.value.ttl_expires_at))
})

const ttlExpired = computed(() => {
  tick.value
  if (!environment.value) return false
  if (environment.value.ttl_disabled || !environment.value.ttl_expires_at) return false
  if (environment.value.status === 'EXPIRED') return true
  return ttlIsExpired(environment.value.ttl_expires_at)
})

const displayStatus = computed(() => {
  if (!environment.value) return 'PROVISIONING' as const
  if (environment.value.status === 'EXPIRED') return 'EXPIRED' as const
  if (environment.value.status === 'PAUSED' && ttlExpired.value) return 'EXPIRED' as const
  return environment.value.status
})

const canResume = computed(
  () => environment.value?.status === 'PAUSED' && !ttlExpired.value,
)

const appHref = computed(() => resolvePreviewUrl(environment.value ?? undefined))

const previewEndpoints = computed(() =>
  environment.value ? resolvePreviewEndpoints(environment.value) : [],
)
const alsoExposedEndpoints = computed(() =>
  environment.value ? secondaryPreviewEndpoints(environment.value) : [],
)

const portalHref = computed(() => {
  if (!environment.value) return '#'
  return environment.value.portal_url || `/p/${environment.value.id}`
})

const canOpenApp = computed(() => {
  if (!environment.value) return false
  return Boolean(environment.value.app_ready && appHref.value)
})

const openAppTitle = computed(() => {
  const env = environment.value
  if (!env) return t('environments.detail.openPreview')
  const image = env.workload_image || env.name || 'app'
  const port = env.node_port != null ? `NodePort ${env.node_port}` : env.preview_url
  return t('environments.detail.openPreviewTitle', { image, port: port ?? '-' })
})

const isProvisioning = computed(() => environment.value?.status === 'PROVISIONING')
const isLocal = computed(() => Boolean(environment.value?.is_local))

const canExtend = computed(() => {
  const env = environment.value
  if (!env) return false
  if (env.ttl_disabled || !env.ttl_expires_at) return false
  const s = env.status
  if (s !== 'RUNNING' && s !== 'FAILED') return false
  tick.value
  return ttlCanExtend(env.created_at, env.ttl_expires_at)
})
const canRelaunch = computed(() => displayStatus.value === 'EXPIRED')
const canPromote = computed(() => {
  if (!environment.value) return false
  const status = environment.value.status
  return (status === 'RUNNING' || status === 'FAILED') && isLocal.value
})
const canStagePromoteStaging = computed(() => Boolean(environment.value?.can_promote_to_staging))
const canStagePromoteProduction = computed(() => Boolean(environment.value?.can_promote_to_production))
const stagePromotePending = ref(false)
const stagePromoteNotice = ref<string | null>(null)
const confirmStagePromoteOpen = ref(false)
const confirmStageTarget = ref<'staging' | 'production' | null>(null)

const stagePromoteDialogTitle = computed(() => {
  if (confirmStageTarget.value === 'production') {
    return t('environments.detail.confirmPromoteProductionTitle')
  }
  return t('environments.detail.confirmPromoteStagingTitle')
})

const stagePromoteDialogMessage = computed(() => {
  const name = environment.value?.name || '-'
  if (confirmStageTarget.value === 'production') {
    return t('environments.detail.confirmPromoteProductionMessage', { name })
  }
  return t('environments.detail.confirmPromoteStagingMessage', { name })
})

const stagePromoteConfirmLabel = computed(() => {
  if (confirmStageTarget.value === 'production') {
    return t('environments.detail.confirmPromoteProductionAction')
  }
  return t('environments.detail.confirmPromoteStagingAction')
})

function requestStagePromote(target: 'staging' | 'production') {
  confirmStageTarget.value = target
  confirmStagePromoteOpen.value = true
  closeActionsMenu()
}

function cancelStagePromote() {
  if (stagePromotePending.value) return
  confirmStagePromoteOpen.value = false
  confirmStageTarget.value = null
}

async function onStagePromoteConfirm() {
  const target = confirmStageTarget.value
  if (!environment.value || !target || stagePromotePending.value) return
  stagePromotePending.value = true
  stagePromoteNotice.value = null
  loadError.value = null
  try {
    const result = await stagePromote(environment.value.id, { target_stage: target })
    confirmStagePromoteOpen.value = false
    confirmStageTarget.value = null
    if (result.environment_id) {
      const stageLabel =
        target === 'production'
          ? t('environments.lifecycle.production')
          : t('environments.lifecycle.staging')
      toast.success(
        t('environments.detail.stagePromoteStarted', { stage: stageLabel }),
        t('environments.detail.stagePromoteStartedBlurb'),
      )
      await navigateTo(`/environments/${result.environment_id}`)
      return
    }
    stagePromoteNotice.value = t('environments.detail.stagePromotePendingApproval', {
      stage:
        target === 'production'
          ? t('environments.lifecycle.production')
          : t('environments.lifecycle.staging'),
    })
    toast.info(
      t('environments.detail.stagePromotePendingTitle'),
      stagePromoteNotice.value,
    )
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : t('common.failed')
    toast.error(t('environments.detail.stagePromoteFailed'), loadError.value)
  } finally {
    stagePromotePending.value = false
  }
}
const canScanDrift = computed(() => environment.value?.status === 'RUNNING')
const canRetry = computed(() => {
  const s = environment.value?.status
  return s === 'FAILED' || s === 'RUNNING'
})
const canAnalyze = computed(() => {
  if (!environment.value) return false
  // Always allow analysis once the environment exists; Gemini needs logs/errors
  // but the drawer can still explain empty telemetry / missing API key.
  return environment.value.status !== 'DESTROYED'
})

const canCreateJira = computed(() => {
  if (!environment.value) return false
  return environment.value.status === 'FAILED' && !environment.value.jira_issue_key
})

async function onCreateJiraIssue() {
  if (!environment.value || jiraPending.value) return
  jiraPending.value = true
  try {
    const result = await createOrLinkJiraIssue(environment.value.id)
    environment.value = {
      ...environment.value,
      jira_issue_key: result.issue_key,
      jira_issue_url: result.issue_url,
    }
    toast.success(
      result.created ? t('integrations.jiraIssueCreated') : t('integrations.jiraIssueLinked'),
      result.issue_key,
    )
  } catch (err) {
    toast.error(
      t('integrations.jiraIssueFailed'),
      err instanceof Error ? err.message : t('common.failed'),
    )
  } finally {
    jiraPending.value = false
  }
}

async function onAnalyze() {
  if (!environment.value || analyzing.value) return
  try {
    await analyzeEnvironment(environment.value.id, {
      cicdLogs: lines.value.join('\n') || null,
      includeEnvironmentLogs: true,
    })
  } catch {
    // error surfaced via usePreviewAnalyzer().error in drawer
  }
}

const retryAction = define(() => retryProvision(environment.value!.id), {
  success: (env) => ({ type: 'info', title: t('environments.actions.retrying'), message: `${env.name} is provisioning again.` }),
  error: (err) => ({ title: t('common.failed'), message: toastError(err, t('common.failed')) }),
  onSuccess: (env) => { environment.value = env; loadError.value = null; connect(env.id) },
  onError: (msg) => { loadError.value = msg },
})

async function load(opts: { softAudits?: boolean } = {}) {
  loadError.value = null
  try {
    environment.value = await getById(id.value)
    reconcileEnvironment(environment.value)
    const soft = opts.softAudits && audits.value.length > 0
    if (!soft) auditsLoading.value = true
    try {
      audits.value = await listAudits(id.value)
    } catch {
      if (!soft) audits.value = []
    } finally {
      auditsLoading.value = false
    }
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : t('common.failed')
  } finally {
    loading.value = false
  }
}

function closeActionsMenu() {
  actionsMenuOpen.value = false
}

function toggleActionsMenu() {
  actionsMenuOpen.value = !actionsMenuOpen.value
}

const destroyAction = define(
  () => destroy(environment.value!.id, {
    // Force cancels in-flight provision and tears down stranded cloud/local
    // resources. Cancel-only is a separate "Stop provisioning" action.
    force:
      environment.value?.status === 'PROVISIONING'
      || environment.value?.status === 'TEARDOWN_PENDING',
  }),
  {
  success: (env) => ({ title: t('environments.toasts.destroyed'), message: `${env.name} is being destroyed.` }),
  error: (err) => ({
    title: t('environments.toasts.destroyFailed'),
    message: toastError(err, t('common.failed')),
  }),
  onSuccess: (env) => { environment.value = env; connect(env.id) },
  onError: (msg) => { loadError.value = msg },
})

const stopProvisionAction = define(
  () => cancelProvision(environment.value!.id),
  {
    success: (env) => ({
      title: t('environments.toasts.stopped'),
      message: `${env.name} stopped. No teardown was queued.`,
    }),
    error: (err) => ({
      title: t('environments.toasts.stopFailed'),
      message: toastError(err, t('common.failed')),
    }),
    onSuccess: (env) => { environment.value = env; connect(env.id) },
    onError: (msg) => { loadError.value = msg },
  },
)

function requestDestroy() {
  if (!environment.value || destroyAction.pending) return
  confirmDestroyOpen.value = true
}

async function onDestroy() {
  confirmDestroyOpen.value = false
  await destroyAction.run()
}

const extendAction = define(() => extendTtl(environment.value!.id, {}), {
  success: (env) => ({ title: t('environments.toasts.extended'), message: `${env.name} will live longer.` }),
  error: (err) => ({ title: t('environments.toasts.extendFailed'), message: toastError(err, t('common.failed')) }),
  onSuccess: (env) => { environment.value = env; loadError.value = null },
  onError: (msg) => { loadError.value = msg },
})

const relaunchAction = define(() => relaunchEnvironment(environment.value!.id), {
  success: (env) => ({ title: t('environments.toasts.relaunched'), message: `${env.name} is relaunching.` }),
  error: (err) => ({ title: t('environments.toasts.relaunchFailed'), message: toastError(err, t('common.failed')) }),
  onSuccess: (env) => { environment.value = env; loadError.value = null; connect(env.id) },
  onError: (msg) => { loadError.value = msg },
})

const scanDriftAction = define(
  async () => {
    const env = await scanDrift(environment.value!.id)
    const soft = audits.value.length > 0
    if (!soft) auditsLoading.value = true
    try {
      audits.value = await listAudits(env.id)
    } catch {
      if (!soft) audits.value = []
    } finally {
      auditsLoading.value = false
    }
    return env
  },
  {
    success: (env) => env.drift_detected
      ? { type: 'warning', title: t('environments.detail.driftWarning'), message: t('environments.detail.driftWarning') }
      : { title: t('environments.toasts.driftOk'), message: t('environments.toasts.driftOk') },
    error: (err) => ({ title: t('environments.toasts.driftFailed'), message: toastError(err, t('common.failed')) }),
    onSuccess: (env) => { environment.value = env; loadError.value = null },
    onError: (msg) => { loadError.value = msg },
  },
)

const promoteAction = define(
  () => promoteToCloud(environment.value!.id, {
    provider: promoteForm.provider,
    credentials: { ...promoteCredentials },
    primary_service: promotePrimaryService.value ?? promoteRecommendedService.value,
    code_source: showPromoteCodeSource.value ? promoteForm.code_source : null,
    region: showPromoteRegion.value ? promoteForm.region : null,
    create_vpc:
      showPromoteNetworking.value && promoteForm.network_mode === 'create'
        ? true
        : false,
    create_subnets:
      showPromoteNetworking.value && promoteForm.network_mode === 'create'
        ? promoteForm.create_subnets
        : false,
    existing_vpc_id:
      showPromoteNetworking.value
      && promoteForm.network_mode === 'existing'
      && promoteForm.existing_vpc_id
        ? promoteForm.existing_vpc_id
        : null,
    existing_security_group_id:
      promoteForm.provider === 'aws'
      && promoteForm.security_group_mode === 'existing'
      && promoteForm.existing_security_group_id
        ? promoteForm.existing_security_group_id
        : null,
    kubernetes_image_scan: { ...promoteForm.image_scan },
  }),
  {
    success: () => ({ title: t('environments.detail.launchCloudPreview'), message: t('environments.detail.deployingToCloud', { provider: promoteForm.provider.toUpperCase() }) }),
    error: (err) => {
      if (err instanceof ApiError) {
        const code = err.code ? ` (${err.code})` : ''
        const corr = err.correlationId ? ` [${err.correlationId}]` : ''
        return { title: t('environments.toasts.cloudFailed'), message: `${err.message}${code}${corr}` }
      }
      return { title: t('environments.toasts.cloudFailed'), message: toastError(err, t('common.failed')) }
    },
    onSuccess: async (created) => {
      showPromote.value = false
      loadError.value = null
      await navigateTo(`/environments/${created.id}`)
    },
    onError: (msg) => { loadError.value = msg },
  },
)

async function copyAppUrl() {
  if (!appHref.value) return
  try {
    await navigator.clipboard.writeText(appHref.value)
    copied.value = true
    closeActionsMenu()
    toast.success(t('environments.actions.copied'), t('environments.actions.copied'))
    setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch {
    loadError.value = t('common.failed')
    toast.error(t('common.failed'), t('common.failed'))
  }
}

async function onOpenAppClick(e: MouseEvent) {
  if (!appHref.value) return
  // Prevent navigation to a potentially-stale URL; refresh latest preview_url first.
  e.preventDefault()
  const fallbackHref = appHref.value
  try {
    environment.value = await getById(id.value)
  } catch {
    // Fall back to the current href if refresh fails.
  }
  const href = resolvePreviewUrl(environment.value ?? undefined) || fallbackHref
  if (!href) return
  const w = window.open(href, '_blank')
  // Ensure the opened window can't reach back into our page.
  if (w) w.opener = null
}

watch(done, async (isDone) => {
  if (isDone) {
    await load({ softAudits: true })
  }
})

watch(
  () => environment.value?.status,
  async (status, prev) => {
    if (prev === 'PROVISIONING' && status && status !== 'PROVISIONING') {
      await load({ softAudits: true })
    }
  },
)

function onDocClick() {
  if (actionsMenuOpen.value) closeActionsMenu()
}

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  void load()
  document.addEventListener('click', onDocClick)
  // TTL clock only - lifecycle status/URL arrive via Redis SSE.
  pollTimer = setInterval(() => {
    tick.value += 1
  }, 1_000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  document.removeEventListener('click', onDocClick)
})
</script>

<template>
  <div class="space-y-8 animate-fade-up">
    <NuxtLink
      to="/environments"
      class="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-wide text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
    >
      <span class="material-symbols-outlined text-sm">arrow_back</span>
      {{ t('environments.detail.crumb') }}
    </NuxtLink>

    <AppSplash
      v-if="loading && !environment"
      compact
      :message="t('environments.index.loading')"
    />
    <p v-else-if="loadError" class="text-sm text-[var(--lp-danger)]">{{ loadError }}</p>

    <template v-if="environment">
      <p
        v-if="environment.status === 'TEARDOWN_PENDING'"
        class="rounded-lg border border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 px-4 py-3 text-sm text-[var(--lp-warn)] animate-pulse"
      >
        {{ t('environments.toasts.deletingBanner') }}
      </p>
      <p
        v-if="environment.ttl_warning"
        class="rounded-lg border border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 px-4 py-3 text-sm text-[var(--lp-warn)]"
      >
        {{ t('environments.detail.ttlWarning') }}
      </p>
      <p
        v-if="environment.soft_cost_cap_exceeded"
        class="rounded-lg border border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/10 px-4 py-3 text-sm text-[var(--lp-danger)]"
      >
        {{ t('environments.detail.softCostCapWarning') }}
        <code class="font-mono text-xs">PREVIEW_SOFT_COST_CAP</code>.
      </p>
      <p
        v-if="environment.drift_detected"
        class="rounded-lg border border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 px-4 py-3 text-sm text-[var(--lp-warn)]"
      >
        {{ t('environments.detail.driftWarning') }}
        <span v-if="environment.drift_summary" class="mt-1 block font-mono text-xs">
          {{ environment.drift_summary }}
        </span>
      </p>

      <section class="lp-glass overflow-hidden rounded-xl">
        <div class="flex flex-col gap-5 border-b border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 px-5 py-5 lg:flex-row lg:items-start lg:justify-between">
          <div class="min-w-0 space-y-3">
            <h1 class="text-3xl font-semibold tracking-tight">{{ environment.name }}</h1>
            <div class="flex flex-wrap items-center gap-3">
              <LifecycleStageBadge :stage="environment.lifecycle_stage" />
              <DeployKindBadge :deploy-mode="environment.deploy_mode" />
              <StatusBadge :status="displayStatus" />
              <EnvironmentHealthDot :status="displayStatus" :app-ready="environment.app_ready" />
              <span class="break-all font-mono text-xs text-[var(--lp-muted)]">{{ environment.namespace_name }}</span>
            </div>
            <p v-if="stagePromoteNotice" class="text-sm text-[var(--lp-accent)]">
              {{ stagePromoteNotice }}
              <NuxtLink to="/org/promotions" class="ml-1 underline">{{ t('environments.detail.viewPromotions') }}</NuxtLink>
            </p>
            <p v-if="environment.runtime_summary" class="font-mono text-xs text-[var(--lp-muted)]">
              {{ environment.runtime_summary }}
            </p>
          </div>

          <div class="flex w-full justify-end gap-2.5 lg:w-6xl lg:max-w-3xl lg:items-end">
            <!-- Primary actions -->
            <div class="flex flex-wrap items-center gap-2 lg:justify-end">
              <a
                v-if="canOpenApp"
                :href="appHref!"
                target="_blank"
                rel="noopener noreferrer"
                class="lp-btn-primary whitespace-nowrap"
                :title="openAppTitle"
                @click="onOpenAppClick"
              >
                <span class="material-symbols-outlined text-base">open_in_new</span>
                {{ t('environments.detail.openApp') }}
                <span v-if="environment.node_port" class="font-mono text-xs opacity-80">
                  :{{ environment.node_port }}
                </span>
              </a>
              <button
                v-else-if="isProvisioning"
                type="button"
                class="lp-btn-primary whitespace-nowrap opacity-60"
                disabled
                :title="t('environments.detail.openAppWhenRunning')"
              >
                <span class="material-symbols-outlined text-base">hourglass_top</span>
                {{ t('environments.detail.provisioning') }}
              </button>
              <button
                v-if="canResume"
                type="button"
                class="lp-btn-primary whitespace-nowrap bg-emerald-600 hover:bg-emerald-500 text-white"
                :disabled="resumeAction.pending"
                @click="resumeAction.run()"
              >
                <span class="material-symbols-outlined text-base">play_arrow</span>
                {{ resumeAction.pending ? t('environments.actions.resuming') : t('environments.actions.resume') }}
              </button>
              <span
                v-else-if="displayStatus === 'EXPIRED'"
                class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-[var(--lp-line)] px-3 py-2 text-sm text-[var(--lp-muted)]"
                :title="t('environments.detail.ttlExpiredResumeDisabled')"
              >
                <span class="material-symbols-outlined text-base">timer_off</span>
                {{ t('environments.actions.expired') }}
              </span>
              <button
                v-if="canRelaunch"
                type="button"
                class="lp-btn-primary whitespace-nowrap"
                :disabled="relaunchAction.pending"
                @click="relaunchAction.run()"
              >
                <span class="material-symbols-outlined text-base">rocket_launch</span>
                {{ relaunchAction.pending ? t('environments.actions.retrying') : t('environments.actions.relaunch') }}
              </button>
              <button
                v-if="canRetry"
                type="button"
                class="lp-btn-primary whitespace-nowrap"
                :disabled="retryAction.pending"
                @click="retryAction.run()"
              >
                <span class="material-symbols-outlined text-base">replay</span>
                {{ retryAction.pending ? t('environments.actions.retrying') : t('environments.actions.retry') }}
              </button>
            </div>

            <!-- Secondary tools: overflow menu -->
            <div class="relative" @click.stop>
              <button
                type="button"
                class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/25 text-[var(--lp-muted)] transition hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]"
                :aria-expanded="actionsMenuOpen"
                aria-haspopup="menu"
                :aria-label="t('common.actions')"
                @click="toggleActionsMenu"
              >
                <span class="material-symbols-outlined text-xl">more_vert</span>
              </button>
              <div
                v-if="actionsMenuOpen"
                role="menu"
                class="absolute right-0 top-full z-30 mt-1.5 min-w-[200px] overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] py-1 shadow-xl"
              >
                <button
                  v-if="environment.status === 'RUNNING'"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-amber-300 transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="pauseAction.pending"
                  @click="pauseAction.run(); closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base">pause</span>
                  {{ pauseAction.pending ? t('environments.actions.pausing') : t('environments.actions.pause') }}
                </button>
                <button
                  v-if="canOpenApp"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)]"
                  @click="copyAppUrl"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">content_copy</span>
                  {{ copied ? t('environments.actions.copied') : t('environments.actions.copyUrl') }}
                </button>
                <button
                  v-if="canExtend"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="extendAction.pending"
                  @click="extendAction.run(); closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">more_time</span>
                  {{ extendAction.pending ? t('environments.actions.extending') : t('environments.actions.extendTtl') }}
                </button>
                <button
                  v-if="canScanDrift"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="scanDriftAction.pending"
                  @click="scanDriftAction.run(); closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">difference</span>
                  {{ scanDriftAction.pending ? t('environments.actions.scanning') : t('environments.actions.scanDrift') }}
                </button>
                <button
                  v-if="canAnalyze"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="analyzing"
                  @click="onAnalyze(); closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">psychology</span>
                  {{ analyzing ? t('environments.actions.analyzing') : t('environments.actions.analyze') }}
                </button>
                <button
                  v-if="canCreateJira"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="jiraPending"
                  @click="onCreateJiraIssue(); closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">bug_report</span>
                  {{ jiraPending ? t('integrations.linkingJira') : t('integrations.createJiraIssue') }}
                </button>
                <a
                  v-if="environment?.jira_issue_url"
                  :href="environment.jira_issue_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)]"
                  @click="closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">open_in_new</span>
                  {{ t('integrations.openJiraIssue') }}
                </a>
                <button
                  v-if="canPromote"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)]"
                  @click="showPromote = !showPromote; closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">cloud_upload</span>
                  {{ t('environments.detail.deployToCloud') }}
                </button>
                <button
                  v-if="canStagePromoteStaging"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="stagePromotePending"
                  @click="requestStagePromote('staging')"
                >
                  <span class="material-symbols-outlined text-base text-sky-300">upgrade</span>
                  {{ t('environments.detail.promoteToStaging') }}
                </button>
                <button
                  v-if="canStagePromoteProduction"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="stagePromotePending"
                  @click="requestStagePromote('production')"
                >
                  <span class="material-symbols-outlined text-base text-amber-300">verified</span>
                  {{ t('environments.detail.promoteToProduction') }}
                </button>
                <NuxtLink
                  :to="`/environments/${environment.id}/observability`"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)]"
                  @click="closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">monitoring</span>
                  {{ t('envConsole.openObservability') }}
                </NuxtLink>
                <a
                  :href="portalHref"
                  target="_blank"
                  rel="noopener noreferrer"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)]"
                  @click="closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">open_in_new</span>
                  {{ t('common.status') }}
                </a>
                <button
                  v-if="environment.status === 'PROVISIONING'"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-text)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="stopProvisionAction.pending || destroyAction.pending"
                  @click="stopProvisionAction.run(); closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">stop_circle</span>
                  {{
                    stopProvisionAction.pending
                      ? t('environments.actions.queuingStop')
                      : t('environments.actions.stopProvision')
                  }}
                </button>
                <button
                  v-if="environment.status !== 'DESTROYED'"
                  type="button"
                  role="menuitem"
                  class="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-[var(--lp-danger)] transition hover:bg-[var(--lp-panel-2)] disabled:opacity-60"
                  :disabled="destroyAction.pending || stopProvisionAction.pending"
                  @click="requestDestroy(); closeActionsMenu()"
                >
                  <span class="material-symbols-outlined text-base">delete</span>
                  {{
                    destroyAction.pending
                      ? t('environments.actions.queuingTeardown')
                      : t('environments.actions.destroy')
                  }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <div
          v-if="showPromote"
          class="space-y-4 border-b border-[var(--lp-line)] bg-[var(--lp-ink)]/30 px-5 py-4"
        >
          <p class="text-sm text-[var(--lp-muted)]">
            {{ t('environments.detail.promoteBlurb') }}
          </p>
          <WorkspaceRepoSourcePanel
            v-if="environment?.workspace_id"
            :workspace-id="environment.workspace_id"
          />
          <div
            v-if="showPromoteServicePicker"
            class="space-y-2 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 px-3 py-3"
          >
            <p class="text-sm font-medium text-[var(--lp-text)]">
              {{ t('environments.detail.promotePrimaryService') }}
            </p>
            <p class="text-xs text-[var(--lp-muted)]">
              {{ t('environments.detail.promotePrimaryServiceHint') }}
            </p>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="svc in promoteServices"
                :key="svc.name"
                type="button"
                class="rounded-lg border px-3 py-1.5 text-sm"
                :class="
                  promotePrimaryService === svc.name
                    ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
                    : 'border-[var(--lp-line)]'
                "
                @click="promotePrimaryService = svc.name"
              >
                {{ svc.name }}
                <span
                  v-if="svc.name === promoteRecommendedService"
                  class="ml-1 text-xs text-[var(--lp-muted)]"
                >
                  ({{ t('environments.detail.promoteRecommended') }})
                </span>
              </button>
            </div>
          </div>
          <p
            v-else-if="promoteServicesLoading"
            class="text-xs text-[var(--lp-muted)]"
          >
            {{ t('environments.detail.promoteServicesLoading') }}
          </p>
          <p
            v-else-if="promoteServicesError"
            class="text-xs text-[var(--lp-danger)]"
          >
            {{ promoteServicesError }}
          </p>
          <div
            v-if="showPromoteCodeSource"
            class="space-y-2 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 px-3 py-3"
          >
            <p class="text-sm font-medium text-[var(--lp-text)]">
              {{ t('environments.detail.promoteCodeSource') }}
            </p>
            <p class="text-xs text-[var(--lp-muted)]">
              {{ t('environments.detail.promoteCodeSourceHint') }}
            </p>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="src in (['ssh', 'github'] as const)"
                :key="src"
                type="button"
                class="rounded-lg border px-3 py-1.5 text-sm"
                :class="
                  promoteForm.code_source === src
                    ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
                    : 'border-[var(--lp-line)]'
                "
                @click="promoteForm.code_source = src"
              >
                {{ t(`environments.detail.promoteCodeSources.${src}`) }}
              </button>
            </div>
          </div>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="p in (['gcp', 'aws', 'azure', 'cloudflare'] as CloudProvider[])"
              :key="p"
              type="button"
              class="rounded-lg border px-3 py-1.5 text-sm uppercase"
              :class="
                promoteForm.provider === p
                  ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
                  : 'border-[var(--lp-line)]'
              "
              @click="promoteForm.provider = p"
            >
              {{ p }}
            </button>
          </div>
          <CloudPromoteDeployTargets
            :targets="promoteDeployTargets"
            :provider="promoteForm.provider"
          />
          <ImageSecurityScanPicker
            v-model:scan="promoteForm.image_scan"
          />
          <label
            v-if="showPromoteRegion"
            class="block max-w-md space-y-2"
          >
            <span class="lp-label">{{ t('environments.detail.promoteRegion') }}</span>
            <select
              v-model="promoteForm.region"
              class="lp-input"
            >
              <option
                v-for="opt in promoteRegionOptions"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
            <p class="text-xs text-[var(--lp-muted)]">
              {{ t('environments.detail.promoteRegionHint') }}
            </p>
          </label>
          <div
            v-if="showPromoteNetworking"
            class="max-w-md space-y-3 rounded-xl border border-[var(--lp-line)] p-3"
          >
            <p class="lp-label">{{ t('environments.detail.promoteNetworking') }}</p>
            <div class="space-y-2 text-sm">
              <label class="flex items-start gap-3">
                <input
                  v-model="promoteForm.network_mode"
                  type="radio"
                  value="existing"
                  class="mt-1 accent-[var(--lp-accent)]"
                >
                <span class="font-medium text-[var(--lp-text)]">{{ t('environments.detail.promoteUseExistingVpc') }}</span>
              </label>
              <label class="flex items-start gap-3">
                <input
                  v-model="promoteForm.network_mode"
                  type="radio"
                  value="create"
                  class="mt-1 accent-[var(--lp-accent)]"
                >
                <span class="font-medium text-[var(--lp-text)]">{{ t('environments.detail.promoteCreateVpc') }}</span>
              </label>
              <label class="flex items-start gap-3">
                <input
                  v-model="promoteForm.network_mode"
                  type="radio"
                  value="default"
                  class="mt-1 accent-[var(--lp-accent)]"
                >
                <span class="font-medium text-[var(--lp-text)]">{{ t('environments.detail.promoteDefaultNetwork') }}</span>
              </label>
            </div>

            <div v-if="promoteForm.network_mode === 'existing'" class="space-y-2">
              <label class="block space-y-1">
                <span class="lp-label">{{ t('environments.detail.promoteSelectVpc') }}</span>
                <select
                  v-model="promoteForm.existing_vpc_id"
                  class="lp-input"
                  :disabled="promoteNetworksLoading || !promoteNetworks.length"
                >
                  <option value="" disabled>
                    {{ promoteNetworksLoading ? t('common.loading') : t('environments.detail.promoteSelectVpcPlaceholder') }}
                  </option>
                  <option
                    v-for="net in promoteNetworks"
                    :key="net.id"
                    :value="net.id"
                  >
                    {{ net.name }}{{ net.cidr ? ` (${net.cidr})` : '' }}{{ net.is_default ? ' · default' : '' }}
                  </option>
                </select>
              </label>
              <p v-if="promoteNetworksError" class="text-xs text-[var(--lp-danger)]">{{ promoteNetworksError }}</p>
              <p v-else-if="!promoteNetworksLoading && !promoteNetworks.length" class="text-xs text-[var(--lp-muted)]">
                {{ t('environments.detail.promoteNoVpcs') }}
              </p>
              <button
                type="button"
                class="lp-btn-ghost text-xs uppercase tracking-wide"
                :disabled="promoteNetworksLoading"
                @click="loadPromoteNetworks"
              >
                {{ promoteNetworksLoading ? t('common.loading') : t('environments.detail.promoteRefreshVpcs') }}
              </button>
            </div>

            <label
              v-if="promoteForm.network_mode === 'create'"
              class="flex items-start gap-3 text-sm"
            >
              <input
                v-model="promoteForm.create_subnets"
                type="checkbox"
                class="mt-1 h-4 w-4 accent-[var(--lp-accent)]"
              >
              <span class="font-medium text-[var(--lp-text)]">{{ t('environments.detail.promoteCreateSubnets') }}</span>
            </label>
            <p class="text-xs text-[var(--lp-muted)]">
              {{ t('environments.detail.promoteNetworkingHint') }}
            </p>

            <div
              v-if="promoteForm.provider === 'aws' && promoteRuntimeMode !== 'kubernetes'"
              class="space-y-3 border-t border-[var(--lp-line)] pt-3"
            >
              <p class="lp-label">{{ t('environments.detail.promoteSecurityGroup') }}</p>
              <div class="space-y-2 text-sm">
                <label class="flex items-start gap-3">
                  <input
                    v-model="promoteForm.security_group_mode"
                    type="radio"
                    value="auto"
                    class="mt-1 accent-[var(--lp-accent)]"
                  >
                  <span class="font-medium text-[var(--lp-text)]">{{ t('environments.detail.promoteCreateSecurityGroup') }}</span>
                </label>
                <label class="flex items-start gap-3">
                  <input
                    v-model="promoteForm.security_group_mode"
                    type="radio"
                    value="existing"
                    class="mt-1 accent-[var(--lp-accent)]"
                  >
                  <span class="font-medium text-[var(--lp-text)]">{{ t('environments.detail.promoteUseExistingSecurityGroup') }}</span>
                </label>
              </div>

              <div v-if="promoteForm.security_group_mode === 'existing'" class="space-y-2">
                <label class="block space-y-1">
                  <span class="lp-label">{{ t('environments.detail.promoteSelectSecurityGroup') }}</span>
                  <select
                    v-model="promoteForm.existing_security_group_id"
                    class="lp-input"
                    :disabled="promoteSecurityGroupsLoading || !promoteSecurityGroups.length"
                  >
                    <option value="" disabled>
                      {{ promoteSecurityGroupsLoading ? t('common.loading') : t('environments.detail.promoteSelectSecurityGroupPlaceholder') }}
                    </option>
                    <option
                      v-for="sg in promoteSecurityGroups"
                      :key="sg.id"
                      :value="sg.id"
                    >
                      {{ sg.name }} ({{ sg.id }}){{ sg.description ? ` - ${sg.description}` : '' }}
                    </option>
                  </select>
                </label>
                <p v-if="promoteSecurityGroupsError" class="text-xs text-[var(--lp-danger)]">{{ promoteSecurityGroupsError }}</p>
                <p v-else-if="!promoteSecurityGroupsLoading && !promoteSecurityGroups.length" class="text-xs text-[var(--lp-muted)]">
                  {{ t('environments.detail.promoteNoSecurityGroups') }}
                </p>
                <button
                  type="button"
                  class="lp-btn-ghost text-xs uppercase tracking-wide"
                  :disabled="promoteSecurityGroupsLoading"
                  @click="loadPromoteSecurityGroups"
                >
                  {{ promoteSecurityGroupsLoading ? t('common.loading') : t('environments.detail.promoteRefreshSecurityGroups') }}
                </button>
              </div>
              <p class="text-xs text-[var(--lp-muted)]">
                {{ t('environments.detail.promoteSecurityGroupHint') }}
              </p>
            </div>
          </div>
          <div
            v-if="hasStoredCredsForProvider(promoteForm.provider)"
            class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 px-3 py-2 text-sm"
          >
            <label class="flex items-center gap-2">
              <input
                type="checkbox"
                class="h-4 w-4 accent-[var(--lp-accent)]"
                v-model="useStoredCredentials"
              >
              <span>
                {{ t('environments.detail.useStoredCredentials') }}
                <span v-if="storedCredsLabel" class="ml-2 font-mono text-xs text-[var(--lp-text)]">{{ storedCredsLabel }}</span>
              </span>
            </label>
            <p class="mt-1 text-xs text-[var(--lp-muted)]">
              {{ t('environments.detail.useStoredCredentialsHint') }}
            </p>
          </div>

          <template v-if="!useStoredCredentials">
            <CloudCredentialsFields
              :credentials="promoteCredentials"
              :provider="promoteForm.provider"
            />
          </template>
          <button
            type="button"
            class="lp-btn-primary"
            :disabled="promoteAction.pending"
            @click="promoteAction.run()"
          >
            {{ promoteAction.pending ? t('environments.detail.launchingCloud') : t('environments.detail.launchCloudPreview') }}
          </button>
        </div>

        <div class="grid gap-6 p-5 sm:grid-cols-2 lg:grid-cols-3">
          <div class="sm:col-span-2 lg:col-span-2">
            <p class="lp-label">{{ t('environments.preview.allUrls') }}</p>
            <div v-if="previewEndpoints.length" class="mt-2 space-y-3">
              <PreviewEndpointsList :endpoints="previewEndpoints" />
              <p
                v-if="alsoExposedEndpoints.length"
                class="text-[11px] text-[var(--lp-muted)]"
              >
                {{ t('environments.detail.openApp') }}
                → {{ t('environments.preview.frontend') }}
              </p>
            </div>
            <template v-else>
              <a
                v-if="canOpenApp && appHref"
                :href="appHref"
                target="_blank"
                rel="noopener noreferrer"
                class="mt-1 inline-flex items-center gap-2 break-all font-mono text-xs text-[var(--lp-accent)] hover:underline"
              >
                {{ appHref }}
                <span class="material-symbols-outlined text-sm">open_in_new</span>
              </a>
              <p v-else-if="isProvisioning" class="mt-1 text-sm text-[var(--lp-muted)]">
                {{ t('environments.detail.appUrlWhenRunning') }}
              </p>
              <p v-else class="mt-1 text-sm text-[var(--lp-muted)]">{{ t('common.notSet') }}</p>
            </template>
          </div>
          <div>
            <p class="lp-label">{{ t('environments.detail.ttlRemaining') }}</p>
            <p
              class="mt-1 font-mono text-sm"
              :class="environment.ttl_warning ? 'text-[var(--lp-warn)]' : ''"
            >
              {{ remainingLabel }}
            </p>
            <p
              v-if="environment.ttl_expires_at"
              class="mt-0.5 text-xs text-[var(--lp-muted)]"
            >
              {{ t('environments.detail.expires') }} {{ new Date(environment.ttl_expires_at).toLocaleString() }}
            </p>
            <p v-else class="mt-0.5 text-xs text-[var(--lp-muted)]">
              {{ t('environments.detail.noTtlHint') }}
            </p>
          </div>
          <div v-if="!isLocal">
            <p class="lp-label">{{ t('environments.detail.costToDate') }}</p>
            <p class="mt-1 text-lg font-semibold text-[var(--lp-accent)]">
              {{ COST_DISPLAY_SYMBOL }}{{ formatCostAmount(environment.cost_accrued) }}
            </p>
            <p class="text-xs text-[var(--lp-muted)]">
              {{ COST_DISPLAY_SYMBOL }}{{ formatCostAmount(environment.cost_estimate_hourly, { decimals: 4 }) }}/hr
              <span v-if="environment.cost_source" class="ml-1 opacity-80">
                · {{ formatCostSource(environment.cost_source) }}
              </span>
            </p>
          </div>
          <div v-else>
            <p class="lp-label">{{ t('environments.detail.costToDate') }}</p>
            <p class="mt-1 text-sm text-[var(--lp-muted)]">
              {{ t('environments.detail.localShadow') }}
              <span class="font-mono"> {{ COST_DISPLAY_SYMBOL }}{{ formatCostAmount(environment.cost_accrued) }}</span>
              <span v-if="environment.cost_source" class="opacity-80">
                · {{ formatCostSource(environment.cost_source) }}
              </span>
            </p>
          </div>
          <div>
            <p class="lp-label">{{ t('environments.detail.gitRepo') }}</p>
            <p class="mt-1 break-all font-mono text-xs">{{ environment.git_repo_url }}</p>
          </div>
          <div>
            <p class="lp-label">{{ t('environments.detail.gitBranch') }}</p>
            <p class="mt-1 font-mono text-sm">{{ environment.git_branch }}</p>
          </div>
          <div v-if="environment.enable_postgres || environment.enable_redis">
            <p class="lp-label">{{ t('environments.detail.datastores') }}</p>
            <div class="mt-1 flex flex-wrap gap-2">
              <DatastoreStatusBadge
                v-if="environment.enable_postgres"
                name="postgres"
                :status="environment.postgres_status || 'pending'"
              />
              <DatastoreStatusBadge
                v-if="environment.enable_redis"
                name="redis"
                :status="environment.redis_status || 'pending'"
              />
            </div>
            <p class="mt-1 text-[10px] text-[var(--lp-muted)]">
              {{ t('environments.detail.datastoresInjected') }}
            </p>
          </div>
          <div v-if="environment.stable_pr_url">
            <p class="lp-label">{{ t('environments.detail.stablePrUrl') }}</p>
            <a
              :href="environment.stable_pr_url"
              class="mt-1 block break-all font-mono text-sm text-[var(--lp-accent)] hover:underline"
              target="_blank"
              rel="noreferrer"
            >
              {{ environment.stable_pr_url }}
            </a>
          </div>
          <div v-if="environment.github_pr_number">
            <p class="lp-label">{{ t('environments.detail.linkedPr') }}</p>
            <a
              v-if="environment.github_pr_url"
              :href="environment.github_pr_url"
              target="_blank"
              rel="noopener noreferrer"
              class="mt-1 inline-block font-mono text-sm text-[var(--lp-accent)] hover:underline"
            >
              #{{ environment.github_pr_number }}
            </a>
            <p v-else class="mt-1 font-mono text-sm">#{{ environment.github_pr_number }}</p>
          </div>
          <div v-if="environment.jira_issue_key">
            <p class="lp-label">{{ t('integrations.jiraIssueLabel') }}</p>
            <a
              v-if="environment.jira_issue_url"
              :href="environment.jira_issue_url"
              target="_blank"
              rel="noopener noreferrer"
              class="mt-1 inline-block font-mono text-sm text-[var(--lp-accent)] hover:underline"
            >
              {{ environment.jira_issue_key }}
            </a>
            <p v-else class="mt-1 font-mono text-sm">{{ environment.jira_issue_key }}</p>
          </div>
          <div v-if="environment.template_id">
            <p class="lp-label">{{ t('environments.detail.template') }}</p>
            <p class="mt-1 font-mono text-sm">{{ environment.template_id }}</p>
          </div>
          <div v-if="environment.workload_image">
            <p class="lp-label">{{ t('environments.detail.workloadImage') }}</p>
            <p class="mt-1 break-all font-mono text-xs">{{ environment.workload_image }}</p>
          </div>
          <div v-if="environment.workspace_id">
            <p class="lp-label">{{ t('environments.detail.linkedWorkspace') }}</p>
            <NuxtLink
              :to="`/workspaces/${environment.workspace_id}`"
              class="mt-1 inline-block font-mono text-xs text-[var(--lp-accent)] hover:underline"
            >
              {{ t('workspaces.index.open') }}
            </NuxtLink>
          </div>
          <div>
            <p class="lp-label">{{ t('environments.detail.lifecycleStage') }}</p>
            <div class="mt-1.5">
              <LifecycleStageBadge :stage="environment.lifecycle_stage" />
            </div>
          </div>
          <div v-if="environment.promoted_from_id">
            <p class="lp-label">{{ t('environments.detail.promotedFrom') }}</p>
            <NuxtLink
              :to="`/environments/${environment.promoted_from_id}`"
              class="mt-1 inline-block font-mono text-xs text-[var(--lp-accent)] hover:underline"
            >
              {{ environment.promoted_from_id }}
            </NuxtLink>
          </div>
          <div>
            <p class="lp-label">{{ t('environments.detail.environmentId') }}</p>
            <p class="mt-1 break-all font-mono text-xs">{{ environment.id }}</p>
          </div>
        </div>

        <div class="border-t border-[var(--lp-line)] px-5 py-4">
          <p class="lp-label mb-2">{{ t('environments.detail.gitPushRebuilds') }}</p>
          <p class="text-sm text-[var(--lp-muted)]">
            {{ t('environments.detail.gitPushRebuildActive', { branch: environment.git_branch }) }}
            <template v-if="environment.gitops_rebuild_enabled">
              {{ t('environments.detail.webhookConfigured') }}
              <NuxtLink to="/docs#rebuild" class="text-[var(--lp-accent)] hover:underline">{{ t('common.docs') }}</NuxtLink>.
            </template>
            <template v-else>
              {{ t('environments.detail.webhookSetup') }}
              <NuxtLink to="/docs#rebuild" class="text-[var(--lp-accent)] hover:underline">{{ t('common.docs') }}</NuxtLink>.
            </template>
          </p>
          <p
            v-if="environment.latest_commit_sha"
            class="mt-2 font-mono text-xs text-[var(--lp-muted)]"
          >
            {{ t('environments.detail.latestCommit', { sha: environment.latest_commit_sha }) }}
          </p>
          <p
            v-if="environment.max_concurrent_environments != null"
            class="mt-2 text-xs text-[var(--lp-muted)]"
          >
            {{ t('environments.detail.concurrentPreviews', {
              active: environment.concurrent_active_count ?? '-',
              max: environment.max_concurrent_environments,
            }) }}
          </p>
        </div>

        <div class="border-t border-[var(--lp-line)] px-5 py-3 text-sm text-[var(--lp-muted)]">
          {{ t('environments.detail.needCustomManifests') }}
          <NuxtLink to="/provision" class="text-[var(--lp-accent)] hover:underline">{{ t('environments.detail.openProvision') }}</NuxtLink>
          {{ t('environments.detail.livePreviewOnly') }}
        </div>

        <div
          v-if="environment.failure_summary || environment.error_message"
          class="border-t border-[var(--lp-line)] px-5 py-3 text-sm space-y-2"
        >
          <p v-if="environment.failure_summary" class="text-[var(--lp-danger)] font-medium">
            {{ environment.failure_summary }}
          </p>
          <p
            v-if="environment.error_message && environment.error_message !== environment.failure_summary"
            class="text-[var(--lp-muted)]"
          >
            {{ environment.error_message }}
          </p>
        </div>
      </section>

      <AuditTimeline
        :title="t('audit.title')"
        :entries="audits"
        :loading="auditsLoading"
        :empty-label="t('environments.detail.auditEmpty')"
      />

      <JobLogStream
        :lines="lines"
        :connected="connected"
        :done="done"
        :can-analyze="canAnalyze"
        :analyzing="analyzing"
        @analyze="onAnalyze"
      />

      <PreviewAnalyzerDrawer
        v-model="analyzerOpen"
        :environment-name="environment.name"
        :workspace-id="environment.workspace_id"
      />

      <ConfirmDialog
        v-model:open="confirmDestroyOpen"
        :title="t('environments.destroy.title')"
        :message="environment.status === 'PROVISIONING'
          ? t('environments.destroy.messageForce', { name: environment.name })
          : t('environments.destroy.message', { name: environment.name })"
        :confirm-label="t('environments.destroy.confirm')"
        :cancel-label="t('environments.destroy.cancel')"
        :busy="destroyAction.pending"
        @confirm="onDestroy"
      />

      <ConfirmDialog
        v-model:open="confirmStagePromoteOpen"
        :title="stagePromoteDialogTitle"
        :message="stagePromoteDialogMessage"
        :confirm-label="stagePromoteConfirmLabel"
        :cancel-label="t('common.cancel')"
        :danger="confirmStageTarget === 'production'"
        :busy="stagePromotePending"
        @confirm="onStagePromoteConfirm"
        @cancel="cancelStagePromote"
      />
    </template>
  </div>
</template>

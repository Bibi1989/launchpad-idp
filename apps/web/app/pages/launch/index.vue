<script setup lang="ts">
import type { KindClusterStatus, PreviewBuildStatus, PreviewLaunchPayload } from '~/types/environment'
import type {
  GitHubInstallationItem,
  GitHubRepositoryItem,
  GitHubRepositorySearchItem,
  GitlabProjectItem,
  WorkspaceListItem,
  WorkspaceWizardConfig,
} from '~/types/provisioning'
import type { GitHost } from '~/types/git'
import { githubCloneUrl } from '~/utils/githubAccount'
import { hasAwsAuth, hasGcpAuth } from '~/utils/cloudValidation'
import {
  resolvePreviewDeployPlan,
  type PreviewDeployPlan,
} from '~/utils/previewDeployPlan'
import {
  launchRequiresWorkloadImage,
  launchShowsWorkloadImageInput,
} from '~/utils/launchWorkloadImage'

type PreviewTarget = PreviewLaunchPayload['provider']

const { launchPreview, getKindStatus, getPreviewBuildStatus } = useEnvironments()
const {
  listWorkspaces,
  getWorkspace,
  getWizardConfig,
  listGithubInstallations,
  getGitlabStatus,
} = useProvisioning()
const { t } = useI18n()
const route = useRoute()

const step = ref(1)
const workspaces = ref<WorkspaceListItem[]>([])
const loadingWorkspaces = ref(true)
const linkedWorkspaceLabel = ref<string | null>(null)
const linkedWorkspaceMissing = ref(false)
const submitting = ref(false)
const errorMessage = ref<string | null>(null)
const kindStatus = ref<KindClusterStatus | null>(null)
const kindStatusLoading = ref(false)
const kindStatusError = ref<string | null>(null)
const previewBuild = ref<PreviewBuildStatus | null>(null)
const gitHost = ref<GitHost>('github')
const workspacePlan = ref<PreviewDeployPlan | null>(null)
const workspaceWizard = ref<WorkspaceWizardConfig | null>(null)

const githubInstallations = ref<GitHubInstallationItem[]>([])
const selectedInstallationId = ref<number | null>(null)
const selectedRepoFullName = ref('')
const selectedGitlabPath = ref('')
const gitlabConnected = ref(false)
const gitlabBaseUrl = ref('https://gitlab.com')
const loadingGitAccounts = ref(false)

const linkedFromQuery = computed(() => {
  const raw = route.query.workspace
  return typeof raw === 'string' && raw.length > 0 ? raw : null
})

/** Hide the empty default wizard until a deep-linked workspace is ready. */
const bootstrappingLinkedWorkspace = computed(
  () => Boolean(linkedFromQuery.value) && loadingWorkspaces.value,
)

const splashDetail = computed(() => {
  if (linkedWorkspaceLabel.value) return linkedWorkspaceLabel.value
  if (linkedFromQuery.value) return linkedFromQuery.value
  return null
})

const form = reactive({
  name: '',
  provider: 'local' as PreviewTarget,
  ttl_unit: 'hours' as 'hours' | 'minutes',
  ttl_value: 8,
  workload_image: '',
  workspace_id: null as string | null,
  git_repo_url: '',
  git_branch: 'main',
  github_pr_number: null as number | null,
  enable_postgres: false,
  enable_redis: false,
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

const PROVIDER_STORAGE_KEY = 'launchpad.lastPreviewProvider'

const providers = computed(() => [
  { id: 'local' as PreviewTarget, label: t('launch.targets.local'), hint: t('launch.hints.local') },
  { id: 'gcp' as PreviewTarget, label: t('launch.targets.gcp'), hint: t('launch.hints.gcp') },
  { id: 'aws' as PreviewTarget, label: t('launch.targets.aws'), hint: t('launch.hints.aws') },
  { id: 'azure' as PreviewTarget, label: t('launch.targets.azure'), hint: t('launch.hints.azure') },
  { id: 'cloudflare' as PreviewTarget, label: t('launch.targets.cloudflare'), hint: t('launch.hints.cloudflare') },
])

const isLocal = computed(() => form.provider === 'local')

const kindBannerTone = computed(() => {
  const status = kindStatus.value?.status
  if (!status) return 'muted'
  if (status === 'ready') return 'ok'
  if (status === 'absent' && kindStatus.value?.can_launch) return 'warn'
  return 'danger'
})

const localLaunchBlocked = computed(
  () =>
    isLocal.value
    && showLocalClusterBanner.value
    && !workspacePlan.value?.skip_local_cluster
    && kindStatus.value != null
    && !kindStatus.value.can_launch,
)

const buildsFromRepo = computed(
  () => Boolean(previewBuild.value?.enabled && !usesWorkspaceSource.value && form.git_repo_url.trim()),
)

const selectedWorkspace = computed(() =>
  workspaces.value.find((ws) => ws.id === form.workspace_id) ?? null,
)

const usesStoredWorkspaceCredentials = computed(
  () => Boolean(form.workspace_id && selectedWorkspace.value && !isLocal.value),
)

const usesWorkspaceSource = computed(() => Boolean(form.workspace_id && selectedWorkspace.value))

const workspaceHasManifests = computed(() => {
  if (!usesWorkspaceSource.value || !selectedWorkspace.value) return false
  if (workspacePlan.value?.deploy_mode === 'compose' || workspacePlan.value?.deploy_mode === 'attach') {
    return false
  }
  // If the workspace carries Kubernetes manifests, MANIFEST deploy takes the image from the
  // Deployment/Helm chart itself. So the user should not override with the default nginx image.
  return (
    selectedWorkspace.value.artifact_mode === 'manifest_only' ||
    selectedWorkspace.value.artifact_mode === 'both'
  )
})

const showWorkloadImageInput = computed(() =>
  launchShowsWorkloadImageInput({
    usesWorkspaceSource: usesWorkspaceSource.value,
    buildsFromRepo: buildsFromRepo.value,
    workspaceHasManifests: workspaceHasManifests.value,
    deployMode: workspacePlan.value?.deploy_mode ?? null,
  }),
)

const requiresWorkloadImage = computed(() =>
  launchRequiresWorkloadImage({
    usesWorkspaceSource: usesWorkspaceSource.value,
    buildsFromRepo: buildsFromRepo.value,
    workspaceHasManifests: workspaceHasManifests.value,
    deployMode: workspacePlan.value?.deploy_mode ?? null,
  }),
)

const workspaceLaunchDetail = computed(() => {
  const mode = workspacePlan.value?.deploy_mode
  if (mode === 'attach') return t('launch.workspace.launchingFromInstance')
  if (mode === 'compose') return t('launch.workspace.launchingFromCompose')
  if (mode === 'manifest') return t('launch.workspace.launchingFromManifest')
  return t('launch.workspace.launchingFromDetail')
})

const showLocalClusterBanner = computed(
  () => isLocal.value && !workspacePlan.value?.skip_local_cluster,
)

const showSourceStep = computed(() => !usesWorkspaceSource.value && !isLocal.value)

const cloudSteps = computed(() => {
  if (isLocal.value) return []
  if (usesWorkspaceSource.value) {
    return [
      { n: 1, label: t('launch.chooseTarget') },
      { n: 2, label: t('launch.launch') },
    ]
  }
  return [
    { n: 1, label: t('launch.chooseTarget') },
    { n: 2, label: t('launch.pickSource') },
    { n: 3, label: t('launch.launch') },
  ]
})

const confirmStep = computed(() => (showSourceStep.value ? 3 : 2))

const providerLabel = computed(() => {
  const match = providers.value.find((p) => p.id === form.provider)
  return match?.label ?? form.provider
})

const hourlyDisplay = computed(() => {
  if (form.provider === 'local') return '0.00'
  return '0.42'
})

const sourceSummary = computed(() => {
  if (usesWorkspaceSource.value && selectedWorkspace.value) {
    return t('launch.summary.workspace', { name: selectedWorkspace.value.name })
  }
  if (form.git_repo_url.trim()) {
    return form.git_repo_url.trim()
  }
  return form.workload_image.trim()
    ? t('launch.summary.imageRef', { image: form.workload_image.trim() })
    : t('launch.summary.noImage')
})

const imageSummary = computed(() => {
  if (buildsFromRepo.value) {
    return t('launch.summary.builtFrom', { dockerfile: previewBuild.value?.dockerfile || 'Dockerfile' })
  }
  if (usesWorkspaceSource.value) {
    if (workspacePlan.value?.deploy_mode === 'attach') {
      return t('launch.summary.fromWorkspaceInstance')
    }
    if (workspacePlan.value?.deploy_mode === 'compose') {
      return t('launch.summary.fromWorkspaceCompose')
    }
    if (workspaceHasManifests.value) {
      return t('launch.summary.fromManifests')
    }
    return t('launch.summary.fromWorkspace')
  }
  if (workspaceHasManifests.value) {
    return t('launch.summary.fromManifests')
  }
  return form.workload_image.trim() || t('launch.summary.required')
})

async function refreshKindStatus() {
  if (!isLocal.value) return
  kindStatusLoading.value = true
  kindStatusError.value = null
  try {
    kindStatus.value = await getKindStatus()
  } catch (err) {
    kindStatus.value = null
    kindStatusError.value = err instanceof Error ? err.message : t('launch.errors.kindCheckFailed')
  } finally {
    kindStatusLoading.value = false
  }
}

onMounted(async () => {
  form.name = `preview-${Math.random().toString(36).slice(2, 8)}`
  const remembered = localStorage.getItem(PROVIDER_STORAGE_KEY)
  if (
    remembered === 'local'
    || remembered === 'gcp'
    || remembered === 'aws'
    || remembered === 'azure'
    || remembered === 'cloudflare'
  ) {
    form.provider = remembered
  }
  const linkedId = linkedFromQuery.value
  if (linkedId) {
    form.workspace_id = linkedId
  }

  // Resolve the deep-linked workspace first so the splash can show its name
  // and the form is primed before the generic wizard paints.
  const linkedWarm = linkedId
    ? getWorkspace(linkedId)
        .then((ws) => {
          linkedWorkspaceLabel.value = ws.name || ws.workspace_id
          const item: WorkspaceListItem = {
            id: ws.workspace_id,
            name: ws.name || ws.workspace_id,
            engine: String(ws.engine),
            provider: String(ws.provider),
            status: ws.status || 'ready',
            artifact_mode: ws.artifact_mode || 'iac_only',
            created_at: ws.created_at || new Date().toISOString(),
            root_dir: ws.root_dir,
            starred: Boolean(ws.starred),
          }
          if (!workspaces.value.some((row) => row.id === item.id)) {
            workspaces.value = [item, ...workspaces.value]
          }
          form.workspace_id = item.id
          applyWorkspaceSelection(item.id)
        })
        .catch(() => {
          linkedWorkspaceMissing.value = true
        })
    : Promise.resolve()

  try {
    const [workspaceList, buildStatus] = await Promise.all([
      listWorkspaces(),
      getPreviewBuildStatus(),
      linkedWarm,
    ])
    // Prefer list metadata when present; keep the warm entry if the list lags.
    const byId = new Map(workspaceList.map((row) => [row.id, row]))
    for (const row of workspaces.value) {
      if (!byId.has(row.id)) byId.set(row.id, row)
    }
    workspaces.value = Array.from(byId.values())
    previewBuild.value = buildStatus
    if (linkedId) {
      if (workspaces.value.some((ws) => ws.id === linkedId)) {
        linkedWorkspaceMissing.value = false
        applyWorkspaceSelection(linkedId)
      } else if (!linkedWorkspaceLabel.value) {
        linkedWorkspaceMissing.value = true
      }
    }
    void loadGitAccounts()
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : t('launch.errors.loadFailed')
  } finally {
    loadingWorkspaces.value = false
  }
  await refreshKindStatus()
})

const aiDrawerOpen = ref(false)

function isPreviewProvider(value: string): value is PreviewTarget {
  return value === 'local'
    || value === 'gcp'
    || value === 'aws'
    || value === 'azure'
    || value === 'cloudflare'
}

function applyWorkspaceSelection(workspaceId: string) {
  const ws = workspaces.value.find((item) => item.id === workspaceId)
  if (!ws) return
  if (ws.name) {
    const sanitized = ws.name.toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/^-+|-+$/g, '')
    if (sanitized) {
      form.name = sanitized
    }
  }
  if (ws.provider !== 'local' && isPreviewProvider(ws.provider)) {
    form.provider = ws.provider
  }
  void applyWorkspacePlan(workspaceId)
}

async function applyWorkspacePlan(workspaceId: string) {
  try {
    const config = await getWizardConfig(workspaceId)
    workspaceWizard.value = config
    const plan = resolvePreviewDeployPlan(config)
    workspacePlan.value = plan
    form.enable_postgres = plan.enable_postgres
    form.enable_redis = plan.enable_redis
    if (config.cloud.provider !== 'local' && isPreviewProvider(config.cloud.provider)) {
      form.provider = config.cloud.provider
    }
  } catch {
    workspaceWizard.value = null
    workspacePlan.value = null
  }
}

watch(linkedFromQuery, (id) => {
  if (id) {
    form.workspace_id = id
    applyWorkspaceSelection(id)
  }
})

watch(
  () => form.workspace_id,
  (id) => {
    if (id) {
      applyWorkspaceSelection(id)
    } else {
      workspacePlan.value = null
      workspaceWizard.value = null
    }
    if (!isLocal.value) {
      step.value = 1
    }
  },
)

watch(
  () => form.provider,
  (provider) => {
    localStorage.setItem(PROVIDER_STORAGE_KEY, provider)
    if (provider === 'local') {
      step.value = 1
      void refreshKindStatus()
    }
  },
)

watch(gitHost, (host) => {
  selectedRepoFullName.value = ''
  selectedGitlabPath.value = ''
  if (form.git_repo_url.trim() && !urlMatchesGitHost(form.git_repo_url, host)) {
    form.git_repo_url = ''
  }
  void loadGitAccounts()
})

async function loadGitAccounts() {
  loadingGitAccounts.value = true
  try {
    if (gitHost.value === 'github') {
      gitlabConnected.value = false
      const installations = await listGithubInstallations()
      githubInstallations.value = installations
      if (!installations.length) {
        selectedInstallationId.value = null
        selectedRepoFullName.value = ''
        return
      }
      const stillValid = installations.some((item) => item.id === selectedInstallationId.value)
      selectedInstallationId.value = stillValid
        ? selectedInstallationId.value
        : installations[0]!.id
    } else {
      githubInstallations.value = []
      selectedInstallationId.value = null
      selectedRepoFullName.value = ''
      const status = await getGitlabStatus()
      gitlabConnected.value = Boolean(status.connected)
      gitlabBaseUrl.value = status.base_url || 'https://gitlab.com'
    }
  } catch {
    githubInstallations.value = []
    selectedInstallationId.value = null
    gitlabConnected.value = false
  } finally {
    loadingGitAccounts.value = false
  }
}

function onGithubInstallationChange(installationId: number | null) {
  selectedInstallationId.value = installationId
  selectedRepoFullName.value = ''
  form.git_repo_url = ''
}

function onGithubRepoSelect(repo: GitHubRepositorySearchItem | GitHubRepositoryItem) {
  const fullName = 'fullName' in repo ? repo.fullName : repo.full_name
  const defaultBranch = 'defaultBranch' in repo ? repo.defaultBranch : repo.default_branch
  selectedRepoFullName.value = fullName
  form.git_repo_url = githubCloneUrl(fullName)
  form.git_branch = defaultBranch || 'main'
}

function onGitlabProjectSelect(project: GitlabProjectItem) {
  selectedGitlabPath.value = project.path_with_namespace
  const base = gitlabBaseUrl.value.replace(/\/$/, '')
  form.git_repo_url =
    project.http_url_to_repo || `${base}/${project.path_with_namespace}.git`
  form.git_branch = project.default_branch || 'main'
}

function canContinueStep1(): boolean {
  if (form.provider === 'local') return true
  if (usesStoredWorkspaceCredentials.value) return true
  if (form.provider === 'gcp') return hasGcpAuth(form.credentials)
  if (form.provider === 'aws') return hasAwsAuth(form.credentials)
  if (form.provider === 'azure') {
    return Boolean(
      form.credentials.azure_client_id
      && form.credentials.azure_client_secret
      && form.credentials.azure_tenant_id
      && form.credentials.azure_subscription_id,
    )
  }
  return form.credentials.cloudflare_api_token.trim().length > 0
}

const gitUrlPlaceholder = computed(() =>
  gitHost.value === 'github'
    ? 'https://github.com/org/app.git'
    : 'https://gitlab.com/group/project.git',
)

const prFieldLabel = computed(() =>
  gitHost.value === 'github' ? t('launch.prNumber') : t('launch.mrNumber'),
)

function urlMatchesGitHost(url: string, host: GitHost): boolean {
  const lower = url.trim().toLowerCase()
  if (!lower) return true
  if (host === 'github') {
    return lower.includes('github.com') || lower.startsWith('git@github.com:')
  }
  // GitLab: gitlab.com or any other host that is not github
  return !lower.includes('github.com') && !lower.startsWith('git@github.com:')
}

function sourceValid(): boolean {
  if (usesWorkspaceSource.value) return true
  // Local image-only: nginx (or custom image) without cloning a repo.
  if (isLocal.value && !form.git_repo_url.trim()) {
    return Boolean(form.workload_image.trim())
  }
  const repo = form.git_repo_url.trim()
  const branch = form.git_branch.trim()
  if (!branch || /[\s\\]/.test(branch) || branch.includes('..')) return false
  const lower = repo.toLowerCase()
  const looksLikeGit = (
    Boolean(repo)
    && (lower.startsWith('https://') || lower.startsWith('http://') || lower.startsWith('git@') || lower.startsWith('ssh://'))
    && !/[\s\n\r\t]/.test(repo)
  )
  if (!looksLikeGit) return false
  return urlMatchesGitHost(repo, gitHost.value)
}

function urlMismatchError(): string {
  return gitHost.value === 'github'
    ? t('launch.errors.urlMustMatchGithub')
    : t('launch.errors.urlMustMatchGitlab')
}

function goNext() {
  errorMessage.value = null
  if (step.value === 1 && !canContinueStep1()) {
    errorMessage.value = t('launch.errors.pasteCredentials')
    return
  }
  if (step.value === 2 && showSourceStep.value && !sourceValid()) {
    errorMessage.value = form.git_repo_url.trim() && !urlMatchesGitHost(form.git_repo_url, gitHost.value)
      ? urlMismatchError()
      : t('launch.errors.invalidRepo')
    return
  }
  if (step.value === 1 && !showSourceStep.value) {
    step.value = confirmStep.value
    return
  }
  step.value += 1
}

function goBackFromConfirm() {
  step.value = showSourceStep.value ? 2 : 1
}

async function launch() {
  errorMessage.value = null
  const name = form.name.trim().toLowerCase()
  if (!/^[a-z][a-z0-9-]{2,63}$/.test(name)) {
    errorMessage.value = t('launch.errors.invalidName')
    return
  }
  if (!sourceValid()) {
    errorMessage.value = usesWorkspaceSource.value
      ? t('launch.errors.invalidWorkspaceSource')
      : isLocal.value && !form.git_repo_url.trim()
        ? t('launch.errors.setContainerImage')
        : form.git_repo_url.trim() && !urlMatchesGitHost(form.git_repo_url, gitHost.value)
          ? urlMismatchError()
          : t('launch.errors.invalidRepo')
    return
  }
  if (!isLocal.value && !canContinueStep1()) {
    errorMessage.value = t('launch.errors.pasteCredentials')
    return
  }
  if (isLocal.value) {
    if (showLocalClusterBanner.value) {
      await refreshKindStatus()
      if (localLaunchBlocked.value) {
        errorMessage.value = kindStatus.value?.message
          || t('launch.errors.localClusterNotReady')
        return
      }
    }
  }

  if (requiresWorkloadImage.value && !form.workload_image.trim()) {
    errorMessage.value = t('launch.errors.containerImageRequired')
    return
  }
  if (
    !form.workspace_id
    && !form.git_repo_url.trim()
    && !form.workload_image.trim()
  ) {
    errorMessage.value = t('launch.errors.containerImageRequiredLocal')
    return
  }

  submitting.value = true
  try {
    const payload: PreviewLaunchPayload = {
      name,
      provider: form.provider,
      enable_postgres: form.enable_postgres,
      enable_redis: form.enable_redis,
    }
    if (workspacePlan.value?.deploy_mode) {
      payload.deploy_mode = workspacePlan.value.deploy_mode
    }
    if (form.ttl_unit === 'minutes') {
      payload.ttl_minutes = form.ttl_value
    } else {
      payload.ttl_hours = form.ttl_value
    }
    if (form.workload_image.trim()) {
      payload.workload_image = form.workload_image.trim()
    }
    if (form.workspace_id) {
      payload.workspace_id = form.workspace_id
    } else if (form.git_repo_url.trim()) {
      payload.git_repo_url = form.git_repo_url.trim()
      payload.git_branch = form.git_branch.trim() || 'main'
      if (form.github_pr_number && form.github_pr_number > 0) {
        payload.github_pr_number = form.github_pr_number
      }
    } else if (form.workload_image.trim()) {
      payload.workload_image = form.workload_image.trim()
    }
    if (form.provider !== 'local') {
      payload.credentials = { ...form.credentials }
    }
    const env = await launchPreview(payload)
    await navigateTo(`/environments/${env.id}`)
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : t('launch.errors.launchFailed')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-8 animate-fade-up">
    <AppSplash
      v-if="bootstrappingLinkedWorkspace"
      :message="t('launch.splash.preparingWorkspace')"
      :detail="splashDetail"
    />

    <template v-else>
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        {{ usesWorkspaceSource ? t('launch.splash.fromWorkspace') : t('launch.eyebrow') }}
      </p>
      <h1 class="text-3xl font-semibold tracking-tight md:text-4xl">
        <template v-if="usesWorkspaceSource && selectedWorkspace">
          {{ t('launch.splash.titleForWorkspace', { name: selectedWorkspace.name }) }}
        </template>
        <template v-else>
          {{ t('launch.title') }}
        </template>
      </h1>
      <p class="max-w-2xl text-sm text-[var(--lp-muted)]">
        <template v-if="usesWorkspaceSource && selectedWorkspace">
          {{ t('launch.splash.blurbForWorkspace') }}
        </template>
        <template v-else>
          {{ t('launch.blurb') }}
          <NuxtLink to="/provision" class="font-medium text-[var(--lp-accent)] hover:underline">
            {{ t('launch.useProvision') }}
          </NuxtLink>
        </template>
      </p>
      <p
        v-if="linkedWorkspaceMissing"
        class="rounded-lg border border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 px-3 py-2 text-sm text-[var(--lp-warn)]"
      >
        {{ t('launch.splash.workspaceMissing') }}
      </p>
    </header>

    <ol
      v-if="!isLocal"
      class="grid gap-2"
      :class="cloudSteps.length === 2 ? 'sm:grid-cols-2' : 'sm:grid-cols-3'"
    >
      <li
        v-for="item in cloudSteps"
        :key="item.n"
        class="rounded-lg border px-3 py-2 text-sm"
        :class="
          step === item.n
            ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
            : step > item.n
              ? 'border-[var(--lp-ok)]/40 text-[var(--lp-ok)]'
              : 'border-[var(--lp-line)] text-[var(--lp-muted)]'
        "
      >
        <span class="font-mono text-xs">{{ item.n }}.</span> {{ item.label }}
      </li>
    </ol>

    <div
      v-if="errorMessage"
      class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-xs font-mono text-rose-300 shadow-lg backdrop-blur-md"
    >
      <div class="flex items-center gap-2.5 min-w-0">
        <span class="material-symbols-outlined text-lg text-rose-400 shrink-0">error</span>
        <p class="break-words font-semibold">{{ errorMessage }}</p>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/20 px-3 py-1.5 text-xs font-semibold text-amber-200 hover:bg-amber-500/30 transition shadow-md shrink-0"
        @click="aiDrawerOpen = true"
      >
        <span class="material-symbols-outlined text-sm text-amber-400">auto_awesome</span>
        <span>{{ t('launch.aiAnalyze') }}</span>
      </button>
    </div>

    <!-- Local: single screen -->
    <section v-if="isLocal" class="lp-glass space-y-6 rounded-xl p-6">
      <div>
        <h2 class="text-lg font-semibold">{{ t('launch.localTitle') }}</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">
          {{ t('launch.localBlurb') }}
        </p>
      </div>

      <div
        v-if="showLocalClusterBanner"
        class="rounded-lg border px-3 py-2 text-sm"
        :class="{
          'border-[var(--lp-line)] text-[var(--lp-muted)]': kindBannerTone === 'muted',
          'border-[var(--lp-ok)]/40 bg-[var(--lp-ok)]/10 text-[var(--lp-ok)]': kindBannerTone === 'ok',
          'border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 text-[var(--lp-warn)]': kindBannerTone === 'warn',
          'border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/10 text-[var(--lp-danger)]': kindBannerTone === 'danger',
        }"
      >
        <div class="flex flex-wrap items-start justify-between gap-2">
          <p>
            <template v-if="kindStatusLoading">{{ t('launch.kind.checking') }}</template>
            <template v-else-if="kindStatusError">{{ kindStatusError }}</template>
            <template v-else-if="kindStatus">{{ kindStatus.message }}</template>
            <template v-else>{{ t('launch.kind.unavailable') }}</template>
          </p>
          <button
            type="button"
            class="shrink-0 font-mono text-xs underline-offset-2 hover:underline"
            :disabled="kindStatusLoading"
            @click="refreshKindStatus"
          >
            {{ t('common.refresh') }}
          </button>
        </div>
        <p
          v-if="kindStatus && !kindStatusLoading"
          class="mt-1 font-mono text-[10px] opacity-80"
        >
          status={{ kindStatus.status }}
          · cluster={{ kindStatus.cluster }}
          · kind={{ kindStatus.kind_installed ? 'yes' : 'no' }}
          · kubectl={{ kindStatus.kubectl_installed ? 'yes' : 'no' }}
        </p>
      </div>

      <div class="grid gap-2 sm:grid-cols-2">
        <button
          v-for="p in providers"
          :key="p.id"
          type="button"
          class="rounded-lg border p-3 text-left transition"
          :class="
            form.provider === p.id
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
              : 'border-[var(--lp-line)] hover:bg-[var(--lp-panel-2)]'
          "
          @click="form.provider = p.id"
        >
          <p class="text-sm font-medium">{{ p.label }}</p>
          <p class="text-xs text-[var(--lp-muted)]">{{ p.hint }}</p>
        </button>
      </div>

      <label class="block space-y-2">
        <span class="lp-label">{{ t('provision.workspace') }}</span>
        <select v-model="form.workspace_id" class="lp-input" :disabled="loadingWorkspaces">
          <option :value="null">{{ t('common.none') }}</option>
          <option v-for="ws in workspaces" :key="ws.id" :value="ws.id">
            {{ ws.name }} ({{ ws.provider }}/{{ ws.engine }})
          </option>
        </select>
        <p class="text-xs text-[var(--lp-muted)]">
          <template v-if="usesWorkspaceSource && selectedWorkspace">
            {{ t('launch.workspace.launchingFrom') }}
            <strong class="text-[var(--lp-text)]">{{ selectedWorkspace.name }}</strong>
            - {{ workspaceLaunchDetail }}
          </template>
          <template v-else>
            {{ t('launch.workspace.linkBlurb') }}
            <NuxtLink to="/provision" class="text-[var(--lp-accent)] hover:underline">{{ t('launch.createOne') }}</NuxtLink>
          </template>
        </p>
        <div
          v-if="workspacePlan"
          class="rounded-xl border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/5 p-3 text-xs text-[var(--lp-muted)]"
        >
          <p class="font-medium text-[var(--lp-text)]">{{ t('launch.workspace.planTitle') }}</p>
          <p class="mt-1 font-mono">
            {{ t('launch.workspace.planMode') }}: {{ workspacePlan.deploy_mode }}
            · {{ t('launch.workspace.planRuntime') }}: {{ workspacePlan.runtime_mode }}
          </p>
          <p class="mt-1">{{ workspacePlan.reason }}</p>
          <p v-if="workspacePlan.enable_postgres || workspacePlan.enable_redis" class="mt-1">
            {{ t('launch.workspace.planDeps') }}:
            <span v-if="workspacePlan.enable_postgres">Postgres/MySQL/Mongo</span>
            <span v-if="workspacePlan.enable_postgres && workspacePlan.enable_redis"> · </span>
            <span v-if="workspacePlan.enable_redis">Redis</span>
          </p>
        </div>
      </label>

      <template v-if="!usesWorkspaceSource">
      <GitProviderPicker v-model="gitHost" size="sm" />
      <div class="grid gap-3 sm:grid-cols-3">
        <div class="block space-y-2 sm:col-span-2">
          <span class="lp-label">
            <template v-if="gitHost === 'github'">
              {{ t('integrations.github') }} {{ t('common.repository') }}
              <span class="font-normal text-[var(--lp-muted)]">{{ t('launch.optionalLocal') }}</span>
            </template>
            <template v-else>
              {{ t('integrations.gitlab') }} {{ t('common.repository') }}
              <span class="font-normal text-[var(--lp-muted)]">{{ t('launch.optionalLocal') }}</span>
            </template>
          </span>
          <template v-if="gitHost === 'github'">
            <p v-if="loadingGitAccounts" class="text-xs text-[var(--lp-muted)]">{{ t('common.loading') }}</p>
            <template v-else-if="githubInstallations.length">
              <GithubInstallationPicker
                :model-value="selectedInstallationId"
                :installations="githubInstallations"
                manage-link
                @update:model-value="onGithubInstallationChange"
              />
              <GithubRepoPicker
                v-model="selectedRepoFullName"
                :installation-id="selectedInstallationId"
                @select-repo="onGithubRepoSelect"
              />
            </template>
            <template v-else>
              <p class="text-xs text-[var(--lp-muted)]">
                <NuxtLink to="/integrations/github" class="text-[var(--lp-accent)] hover:underline">{{ t('integrations.connectGithub') }}</NuxtLink>
                {{ t('import.connectGithubOrPaste') }}
              </p>
              <input
                v-model="form.git_repo_url"
                class="lp-input font-mono text-xs"
                :placeholder="gitUrlPlaceholder"
                autocomplete="off"
              >
            </template>
          </template>
          <template v-else>
            <p v-if="loadingGitAccounts" class="text-xs text-[var(--lp-muted)]">{{ t('common.loading') }}</p>
            <template v-else-if="gitlabConnected">
              <GitlabRepoPicker
                v-model="selectedGitlabPath"
                @select-project="onGitlabProjectSelect"
              />
            </template>
            <template v-else>
              <p class="text-xs text-[var(--lp-muted)]">
                <NuxtLink to="/integrations/gitlab" class="text-[var(--lp-accent)] hover:underline">{{ t('integrations.connectGitlab') }}</NuxtLink>
                {{ t('import.gitlabPasteUrl') }}
              </p>
              <input
                v-model="form.git_repo_url"
                class="lp-input font-mono text-xs"
                :placeholder="gitUrlPlaceholder"
                autocomplete="off"
              >
            </template>
          </template>
        </div>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('common.branch') }}</span>
          <input v-model="form.git_branch" class="lp-input font-mono text-xs" placeholder="main">
        </label>
        <label class="block space-y-2 sm:col-span-3">
          <span class="lp-label">{{ prFieldLabel }}</span>
          <input
            v-model.number="form.github_pr_number"
            type="number"
            min="1"
            class="lp-input max-w-xs"
            placeholder="42"
          >
          <p class="text-xs text-[var(--lp-muted)]">
            {{ t('launch.prSyncBlurb') }}
          </p>
        </label>
        <p
          v-if="buildsFromRepo && previewBuild"
          class="sm:col-span-3 rounded-lg border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/5 px-3 py-2 text-xs text-[var(--lp-muted)]"
        >
          {{ previewBuild.message }}
        </p>
      </div>
      </template>

      <div class="grid gap-3 sm:grid-cols-2">
        <label class="block space-y-2">
          <span class="lp-label">{{ t('common.name') }}</span>
          <input v-model="form.name" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('environments.create.ttl') }}</span>
          <div class="flex gap-2">
            <input
              v-model.number="form.ttl_value"
              type="number"
              min="1"
              :max="form.ttl_unit === 'minutes' ? 10080 : 168"
              class="lp-input flex-1"
            >
            <select v-model="form.ttl_unit" class="lp-input w-28">
              <option value="hours">{{ t('common.hours') }}</option>
              <option value="minutes">{{ t('common.minutes') }}</option>
            </select>
          </div>
        </label>
        <label v-if="showWorkloadImageInput" class="block space-y-2 sm:col-span-2">
          <span class="lp-label">{{ t('launch.containerImage') }}</span>
          <input
            v-model="form.workload_image"
            class="lp-input font-mono text-xs"
            placeholder="ghcr.io/org/app:tag"
            autocomplete="off"
          >
          <p class="text-xs text-[var(--lp-muted)]">
            {{ t('launch.containerImageRequired') }}
          </p>
        </label>
        <p
          v-else-if="usesWorkspaceSource"
          class="sm:col-span-2 text-xs text-[var(--lp-muted)]"
        >
          {{ t('launch.containerImageFromWorkspace') }}
        </p>
      </div>

      <p v-if="!usesWorkspaceSource" class="text-xs text-[var(--lp-muted)]">
        {{ t('launch.afterLaunchRebuild', { branch: form.git_branch || t('launch.yourBranch') }) }}
      </p>

      <div class="flex justify-end">
        <button
          type="button"
          class="lp-btn-primary"
          :disabled="submitting || kindStatusLoading || localLaunchBlocked"
          @click="launch"
        >
          {{ submitting ? t('common.working') : t('environments.index.launchPreview') }}
        </button>
      </div>
    </section>

    <!-- Cloud step 1 -->
    <section v-else-if="step === 1" class="lp-glass space-y-5 rounded-xl p-6">
      <div>
        <h2 class="text-lg font-semibold">{{ t('launch.chooseTarget') }}</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">
          {{ t('launch.cloud.chooseTargetBlurb') }}
        </p>
      </div>

      <label class="block space-y-2">
        <span class="lp-label">{{ t('provision.workspace') }}</span>
        <select v-model="form.workspace_id" class="lp-input" :disabled="loadingWorkspaces">
          <option :value="null">{{ t('common.none') }} - {{ t('launch.workspace.noneUseGitRepo') }}</option>
          <option v-for="ws in workspaces" :key="ws.id" :value="ws.id">
            {{ ws.name }} · {{ ws.provider }}/{{ ws.engine }}
          </option>
        </select>
        <p v-if="selectedWorkspace" class="text-xs text-[var(--lp-muted)]">
          <template v-if="usesWorkspaceSource">
            {{ t('launch.workspace.launchingFrom') }}
            <strong class="text-[var(--lp-text)]">{{ selectedWorkspace.name }}</strong>
            - {{ t('launch.workspace.launchingFromSkipGit') }}
          </template>
          <template v-else>
            {{ t('launch.workspace.usingStoredFrom') }}
            <strong class="text-[var(--lp-text)]">{{ selectedWorkspace.name }}</strong>
            {{ t('launch.workspace.storedCredentials') }}
          </template>
        </p>
      </label>

      <div class="grid gap-2 sm:grid-cols-2">
        <button
          v-for="p in providers"
          :key="p.id"
          type="button"
          class="rounded-lg border p-3 text-left transition"
          :class="
            form.provider === p.id
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
              : 'border-[var(--lp-line)] hover:bg-[var(--lp-panel-2)]'
          "
          @click="form.provider = p.id"
        >
          <p class="text-sm font-medium">{{ p.label }}</p>
          <p class="text-xs text-[var(--lp-muted)]">{{ p.hint }}</p>
        </button>
      </div>

      <template v-if="form.provider !== 'local' && !usesStoredWorkspaceCredentials">
        <CloudCredentialsFields
          v-model:credentials="form.credentials"
          :provider="(form.provider as 'gcp' | 'aws' | 'azure' | 'cloudflare')"
        />
      </template>

      <div class="flex justify-end">
        <button type="button" class="lp-btn-primary" @click="goNext">
          {{ usesWorkspaceSource ? t('launch.launch') : t('common.continue') }}
        </button>
      </div>
    </section>

    <!-- Cloud step 2: source -->
    <section v-else-if="step === 2 && showSourceStep" class="lp-glass space-y-5 rounded-xl p-6">
      <div>
        <h2 class="text-lg font-semibold">{{ t('launch.pickSource') }}</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">
          {{ t('launch.pickSourceBlurb') }}
        </p>
      </div>
      <GitProviderPicker v-model="gitHost" size="sm" />
      <div class="grid gap-3 sm:grid-cols-3">
        <div class="block space-y-2 sm:col-span-2">
          <span class="lp-label">
            <template v-if="gitHost === 'github'">
              {{ t('integrations.github') }} {{ t('common.repository') }}
            </template>
            <template v-else>
              {{ t('integrations.gitlab') }} {{ t('common.repository') }}
            </template>
          </span>
          <template v-if="gitHost === 'github'">
            <p v-if="loadingGitAccounts" class="text-xs text-[var(--lp-muted)]">{{ t('common.loading') }}</p>
            <template v-else-if="githubInstallations.length">
              <GithubInstallationPicker
                :model-value="selectedInstallationId"
                :installations="githubInstallations"
                manage-link
                @update:model-value="onGithubInstallationChange"
              />
              <GithubRepoPicker
                v-model="selectedRepoFullName"
                :installation-id="selectedInstallationId"
                @select-repo="onGithubRepoSelect"
              />
            </template>
            <template v-else>
              <p class="text-xs text-[var(--lp-muted)]">
                <NuxtLink to="/integrations/github" class="text-[var(--lp-accent)] hover:underline">{{ t('integrations.connectGithub') }}</NuxtLink>
                {{ t('import.connectGithubOrPaste') }}
              </p>
              <input
                v-model="form.git_repo_url"
                class="lp-input font-mono text-xs"
                :placeholder="gitUrlPlaceholder"
              >
            </template>
          </template>
          <template v-else>
            <p v-if="loadingGitAccounts" class="text-xs text-[var(--lp-muted)]">{{ t('common.loading') }}</p>
            <template v-else-if="gitlabConnected">
              <GitlabRepoPicker
                v-model="selectedGitlabPath"
                @select-project="onGitlabProjectSelect"
              />
            </template>
            <template v-else>
              <p class="text-xs text-[var(--lp-muted)]">
                <NuxtLink to="/integrations/gitlab" class="text-[var(--lp-accent)] hover:underline">{{ t('integrations.connectGitlab') }}</NuxtLink>
                {{ t('import.gitlabPasteUrl') }}
              </p>
              <input
                v-model="form.git_repo_url"
                class="lp-input font-mono text-xs"
                :placeholder="gitUrlPlaceholder"
              >
            </template>
          </template>
        </div>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('common.branch') }}</span>
          <input v-model="form.git_branch" class="lp-input font-mono text-xs" placeholder="main">
        </label>
        <label class="block space-y-2 sm:col-span-3">
          <span class="lp-label">{{ prFieldLabel }}</span>
          <input
            v-model.number="form.github_pr_number"
            type="number"
            min="1"
            class="lp-input max-w-xs"
            placeholder="42"
          >
          <p class="text-xs text-[var(--lp-muted)]">
            {{ t('launch.prEnableBlurb') }}
          </p>
        </label>
      </div>
      <div class="flex justify-between">
        <button type="button" class="lp-btn-ghost" @click="step = 1">{{ t('common.back') }}</button>
        <button type="button" class="lp-btn-primary" @click="goNext">{{ t('common.continue') }}</button>
      </div>
    </section>

    <!-- Cloud confirm -->
    <section v-else-if="step === confirmStep" class="lp-glass space-y-5 rounded-xl p-6">
      <div>
        <h2 class="text-lg font-semibold">{{ t('launch.launch') }}</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">
          {{ t('launch.confirmBlurb') }}
        </p>
      </div>
      <label class="block space-y-2">
        <span class="lp-label">{{ t('common.name') }}</span>
        <input v-model="form.name" class="lp-input" autocomplete="off">
      </label>
      <label class="block space-y-2">
        <span class="lp-label">{{ t('environments.create.ttl') }}</span>
        <div class="flex gap-2">
          <input
            v-model.number="form.ttl_value"
            type="number"
            min="1"
            :max="form.ttl_unit === 'minutes' ? 10080 : 168"
            class="lp-input flex-1"
          >
          <select v-model="form.ttl_unit" class="lp-input w-28">
            <option value="hours">{{ t('common.hours') }}</option>
            <option value="minutes">{{ t('common.minutes') }}</option>
          </select>
        </div>
      </label>
      <label
        v-if="showWorkloadImageInput"
        class="block space-y-2"
      >
        <span class="lp-label">{{ t('launch.containerImage') }}</span>
        <input
          v-model="form.workload_image"
          class="lp-input font-mono text-xs"
          placeholder="ghcr.io/org/app:tag"
          autocomplete="off"
        >
      </label>
      <p
        v-else-if="usesWorkspaceSource"
        class="text-xs text-[var(--lp-muted)]"
      >
        {{ t('launch.containerImageFromWorkspace') }}
      </p>
      <div class="space-y-2 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 p-4">
        <span class="lp-label">{{ t('launch.ephemeralDatastores') }}</span>
        <div class="grid gap-2 sm:grid-cols-2 pt-1">
          <label class="flex items-center gap-2 text-xs cursor-pointer">
            <input v-model="form.enable_postgres" type="checkbox" class="accent-[var(--lp-accent)]">
            <span>{{ t('launch.postgresInCluster') }}</span>
          </label>
          <label class="flex items-center gap-2 text-xs cursor-pointer">
            <input v-model="form.enable_redis" type="checkbox" class="accent-[var(--lp-accent)]">
            <span>{{ t('launch.redisInCluster') }}</span>
          </label>
        </div>
      </div>
      <dl class="grid gap-3 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 p-4 text-sm sm:grid-cols-2">
        <div>
          <dt class="lp-label">{{ t('launch.chooseTarget') }}</dt>
          <dd class="mt-1">{{ providerLabel }}</dd>
        </div>
        <div>
          <dt class="lp-label">{{ t('launch.pickSource') }}</dt>
          <dd class="mt-1 break-all">{{ sourceSummary }}</dd>
        </div>
        <div v-if="selectedWorkspace && !usesWorkspaceSource">
          <dt class="lp-label">{{ t('provision.workspace') }}</dt>
          <dd class="mt-1">{{ selectedWorkspace.name }} ({{ selectedWorkspace.provider }})</dd>
        </div>
        <div>
          <dt class="lp-label">{{ t('launch.summary.estHourly') }}</dt>
          <dd class="mt-1 font-mono text-[var(--lp-accent)]">${{ hourlyDisplay }}/hr</dd>
        </div>
        <div>
          <dt class="lp-label">{{ t('launch.summary.image') }}</dt>
          <dd class="mt-1 break-all font-mono text-xs">{{ imageSummary }}</dd>
        </div>
        <div>
          <dt class="lp-label">{{ t('launch.summary.rebuilds') }}</dt>
          <dd class="mt-1 text-[var(--lp-muted)]">{{ t('launch.summary.rebuildsBlurb') }}</dd>
        </div>
      </dl>
      <div class="flex justify-between">
        <button type="button" class="lp-btn-ghost" @click="goBackFromConfirm">{{ t('common.back') }}</button>
        <button
          type="button"
          class="lp-btn-primary"
          :disabled="submitting"
          @click="launch"
        >
          {{ submitting ? t('common.working') : t('environments.index.launchPreview') }}
        </button>
      </div>
    </section>

    <WorkspaceAiAnalysisDrawer
      v-model:open="aiDrawerOpen"
      :workspace-id="form.workspace_id || ''"
      :error-context="errorMessage"
    />
    </template>
  </div>
</template>

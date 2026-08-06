<script setup lang="ts">
import type { KindClusterStatus, PreviewBuildStatus, PreviewLaunchPayload } from '~/types/environment'
import type { WorkspaceListItem } from '~/types/provisioning'
import type { GitHost } from '~/types/git'
import { hasAwsAuth, hasGcpAuth } from '~/utils/cloudValidation'

type PreviewTarget = PreviewLaunchPayload['provider']

const { launchPreview, getKindStatus, getPreviewBuildStatus } = useEnvironments()
const { listWorkspaces } = useProvisioning()
const { t } = useI18n()
const route = useRoute()

const step = ref(1)
const workspaces = ref<WorkspaceListItem[]>([])
const loadingWorkspaces = ref(true)
const submitting = ref(false)
const errorMessage = ref<string | null>(null)
const kindStatus = ref<KindClusterStatus | null>(null)
const kindStatusLoading = ref(false)
const kindStatusError = ref<string | null>(null)
const previewBuild = ref<PreviewBuildStatus | null>(null)
const gitHost = ref<GitHost>('github')

const linkedFromQuery = computed(() => {
  const raw = route.query.workspace
  return typeof raw === 'string' && raw.length > 0 ? raw : null
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
  () => isLocal.value && kindStatus.value != null && !kindStatus.value.can_launch,
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
  // If the workspace carries Kubernetes manifests, MANIFEST deploy takes the image from the
  // Deployment/Helm chart itself. So the user should not override with the default nginx image.
  return (
    selectedWorkspace.value.artifact_mode === 'manifest_only' ||
    selectedWorkspace.value.artifact_mode === 'both'
  )
})

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
  if (linkedFromQuery.value) {
    form.workspace_id = linkedFromQuery.value
  }
  try {
    const [workspaceList, buildStatus] = await Promise.all([
      listWorkspaces(),
      getPreviewBuildStatus(),
    ])
    workspaces.value = workspaceList
    previewBuild.value = buildStatus
    if (linkedFromQuery.value) {
      applyWorkspaceSelection(linkedFromQuery.value)
    }
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

watch(gitHost, () => {
  // Clear mismatched URL when switching provider so the user re-enters intentionally.
  if (form.git_repo_url.trim() && !urlMatchesGitHost(form.git_repo_url, gitHost.value)) {
    form.git_repo_url = ''
  }
})

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
    await refreshKindStatus()
    if (localLaunchBlocked.value) {
      errorMessage.value = kindStatus.value?.message
        || t('launch.errors.localClusterNotReady')
      return
    }
  }

  submitting.value = true
  try {
    const payload: PreviewLaunchPayload = {
      name,
      provider: form.provider,
      enable_postgres: form.enable_postgres,
      enable_redis: form.enable_redis,
    }
    if (form.ttl_unit === 'minutes') {
      payload.ttl_minutes = form.ttl_value
    } else {
      payload.ttl_hours = form.ttl_value
    }
    if (!buildsFromRepo.value && !workspaceHasManifests.value) {
      const image = form.workload_image.trim()
      if (!image) {
        errorMessage.value = t('launch.errors.containerImageRequired')
        return
      }
      payload.workload_image = image
    }
    if (form.workspace_id) {
      payload.workspace_id = form.workspace_id
    } else if (form.git_repo_url.trim()) {
      payload.git_repo_url = form.git_repo_url.trim()
      payload.git_branch = form.git_branch.trim() || 'main'
      if (form.github_pr_number && form.github_pr_number > 0) {
        payload.github_pr_number = form.github_pr_number
      }
    } else {
      const image = form.workload_image.trim()
      if (!image) {
        errorMessage.value = t('launch.errors.containerImageRequiredLocal')
        return
      }
      payload.workload_image = image
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
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        {{ t('launch.eyebrow') }}
      </p>
      <h1 class="text-3xl font-semibold tracking-tight md:text-4xl">{{ t('launch.title') }}</h1>
      <p class="max-w-2xl text-sm text-[var(--lp-muted)]">
        {{ t('launch.blurb') }}
        <NuxtLink to="/provision" class="font-medium text-[var(--lp-accent)] hover:underline">
          {{ t('launch.useProvision') }}
        </NuxtLink>
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
            - {{ t('launch.workspace.launchingFromDetail') }}
          </template>
          <template v-else>
            {{ t('launch.workspace.linkBlurb') }}
            <NuxtLink to="/provision" class="text-[var(--lp-accent)] hover:underline">{{ t('launch.createOne') }}</NuxtLink>
          </template>
        </p>
      </label>

      <template v-if="!usesWorkspaceSource">
      <GitProviderPicker v-model="gitHost" size="sm" />
      <div class="grid gap-3 sm:grid-cols-3">
        <label class="block space-y-2 sm:col-span-2">
          <span class="lp-label">{{ gitHost === 'github' ? t('integrations.github') : t('integrations.gitlab') }} {{ t('launch.repoUrl') }} <span class="font-normal text-[var(--lp-muted)]">{{ t('launch.optionalLocal') }}</span></span>
          <input
            v-model="form.git_repo_url"
            class="lp-input font-mono text-xs"
            :placeholder="gitUrlPlaceholder"
            autocomplete="off"
          >
        </label>
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
        <label v-if="!buildsFromRepo && !workspaceHasManifests" class="block space-y-2 sm:col-span-2">
          <span class="lp-label">{{ t('launch.containerImage') }}</span>
          <input
            v-model="form.workload_image"
            class="lp-input font-mono text-xs"
            placeholder="ghcr.io/org/app:tag"
            autocomplete="off"
            required
          >
          <p class="text-xs text-[var(--lp-muted)]">
            {{ t('launch.containerImageRequired') }}
          </p>
        </label>
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
        <label class="block space-y-2 sm:col-span-2">
          <span class="lp-label">{{ gitHost === 'github' ? t('integrations.github') : t('integrations.gitlab') }} {{ t('launch.repoUrl') }}</span>
          <input
            v-model="form.git_repo_url"
            class="lp-input font-mono text-xs"
            :placeholder="gitUrlPlaceholder"
          >
        </label>
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
        v-if="!buildsFromRepo && !workspaceHasManifests"
        class="block space-y-2"
      >
        <span class="lp-label">{{ t('launch.containerImage') }}</span>
        <input
          v-model="form.workload_image"
          class="lp-input font-mono text-xs"
          placeholder="ghcr.io/org/app:tag"
          autocomplete="off"
          required
        >
      </label>
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
  </div>
</template>

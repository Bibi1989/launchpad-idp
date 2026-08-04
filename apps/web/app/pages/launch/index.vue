<script setup lang="ts">
import type { KindClusterStatus, PreviewAppTemplate, PreviewBuildStatus, PreviewLaunchPayload } from '~/types/environment'
import type { WorkspaceListItem } from '~/types/provisioning'
import { hasAwsAuth, hasGcpAuth } from '~/utils/cloudValidation'

type PreviewTarget = PreviewLaunchPayload['provider']
type SourceMode = 'template' | 'repo'

const { listPreviewTemplates, launchPreview, getKindStatus, getPreviewBuildStatus } = useEnvironments()
const { listWorkspaces } = useProvisioning()
const route = useRoute()

const step = ref(1)
const templates = ref<PreviewAppTemplate[]>([])
const workspaces = ref<WorkspaceListItem[]>([])
const loadingTemplates = ref(true)
const loadingWorkspaces = ref(true)
const submitting = ref(false)
const errorMessage = ref<string | null>(null)
const sourceMode = ref<SourceMode>('template')
const kindStatus = ref<KindClusterStatus | null>(null)
const kindStatusLoading = ref(false)
const kindStatusError = ref<string | null>(null)
const previewBuild = ref<PreviewBuildStatus | null>(null)

const linkedFromQuery = computed(() => {
  const raw = route.query.workspace
  return typeof raw === 'string' && raw.length > 0 ? raw : null
})

const form = reactive({
  name: '',
  provider: 'local' as PreviewTarget,
  ttl_unit: 'hours' as 'hours' | 'minutes',
  ttl_value: 8,
  workload_image: 'nginx:1.27-alpine',
  workspace_id: null as string | null,
  template_id: '' as string,
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

const providers: Array<{ id: PreviewTarget; label: string; hint: string }> = [
  { id: 'local', label: 'Local (Sandbox)', hint: 'On your machine - we start the local cluster if needed' },
  { id: 'gcp', label: 'Google Cloud', hint: 'SA JSON or keyless WIF' },
  { id: 'aws', label: 'AWS', hint: 'Access keys or role ARN' },
  { id: 'azure', label: 'Azure', hint: 'Service principal' },
  { id: 'cloudflare', label: 'Cloudflare', hint: 'API token' },
]

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
  () => Boolean(previewBuild.value?.enabled && sourceMode.value === 'repo' && !usesWorkspaceSource.value),
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
      { n: 1, label: 'Choose target' },
      { n: 2, label: 'Launch' },
    ]
  }
  return [
    { n: 1, label: 'Choose target' },
    { n: 2, label: 'Pick source' },
    { n: 3, label: 'Launch' },
  ]
})

const confirmStep = computed(() => (showSourceStep.value ? 3 : 2))

const selectedTemplate = computed(() =>
  templates.value.find((t) => t.id === form.template_id) ?? null,
)

const providerLabel = computed(() => {
  const match = providers.find((p) => p.id === form.provider)
  return match?.label ?? form.provider
})

const hourlyDisplay = computed(() => {
  if (form.provider === 'local') return '0.00'
  if (sourceMode.value === 'repo') return '0.42'
  return selectedTemplate.value?.hourly_cost_hint || '0.42'
})

const sourceSummary = computed(() => {
  if (usesWorkspaceSource.value && selectedWorkspace.value) {
    return `Workspace: ${selectedWorkspace.value.name}`
  }
  if (sourceMode.value === 'repo') {
    return form.git_repo_url.trim() || 'Your repository'
  }
  return selectedTemplate.value?.title || '-'
})

const imageSummary = computed(() => {
  if (buildsFromRepo.value) {
    return `Built from ${previewBuild.value?.dockerfile || 'Dockerfile'}`
  }
  if (workspaceHasManifests.value) {
    return 'From workspace manifests'
  }
  return form.workload_image.trim() || 'nginx:1.27-alpine'
})

async function refreshKindStatus() {
  if (!isLocal.value) return
  kindStatusLoading.value = true
  kindStatusError.value = null
  try {
    kindStatus.value = await getKindStatus()
  } catch (err) {
    kindStatus.value = null
    kindStatusError.value = err instanceof Error ? err.message : 'Failed to check local cluster'
  } finally {
    kindStatusLoading.value = false
  }
}

function selectTemplate(tpl: any) {
  form.template_id = tpl.id
  form.ttl_unit = 'hours'
  form.ttl_value = tpl.default_ttl_hours
  if (tpl.workload_image) {
    form.workload_image = tpl.workload_image
  }
  if (tpl.enable_postgres !== undefined) {
    form.enable_postgres = Boolean(tpl.enable_postgres)
  }
  if (tpl.enable_redis !== undefined) {
    form.enable_redis = Boolean(tpl.enable_redis)
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
    const [templateList, workspaceList, buildStatus] = await Promise.all([
      listPreviewTemplates(),
      listWorkspaces(),
      getPreviewBuildStatus(),
    ])
    templates.value = templateList
    workspaces.value = workspaceList
    previewBuild.value = buildStatus
    if (templates.value[0]) {
      selectTemplate(templates.value[0])
    }
    if (linkedFromQuery.value) {
      applyWorkspaceSelection(linkedFromQuery.value)
    }
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Failed to load launch options'
  } finally {
    loadingTemplates.value = false
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

watch(
  () => form.template_id,
  (id) => {
    if (sourceMode.value !== 'template') return
    const tpl = templates.value.find((t) => t.id === id)
    if (tpl) {
      form.ttl_unit = 'hours'
      form.ttl_value = tpl.default_ttl_hours
    }
  },
)

watch(sourceMode, (mode) => {
  if (mode === 'template' && selectedTemplate.value) {
    form.ttl_unit = 'hours'
    form.ttl_value = selectedTemplate.value.default_ttl_hours
  } else if (mode === 'repo') {
    form.ttl_unit = 'hours'
    form.ttl_value = 24
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

function sourceValid(): boolean {
  if (usesWorkspaceSource.value) return true
  if (sourceMode.value === 'template') return Boolean(form.template_id)
  const repo = form.git_repo_url.trim()
  const branch = form.git_branch.trim()
  if (!branch || /[\s\\]/.test(branch) || branch.includes('..')) return false
  const lower = repo.toLowerCase()
  return (
    Boolean(repo)
    && (lower.startsWith('https://') || lower.startsWith('http://') || lower.startsWith('git@') || lower.startsWith('ssh://'))
    && !/[\s\n\r\t]/.test(repo)
  )
}

function goNext() {
  errorMessage.value = null
  if (step.value === 1 && !canContinueStep1()) {
    errorMessage.value = 'Paste cloud credentials to continue'
    return
  }
  if (step.value === 2 && showSourceStep.value && !sourceValid()) {
    errorMessage.value = sourceMode.value === 'repo'
      ? 'Enter a valid git repo URL and branch'
      : 'Pick a preview app'
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
    errorMessage.value = 'Name must be lowercase, start with a letter, 3-64 chars'
    return
  }
  if (!sourceValid()) {
    errorMessage.value = sourceMode.value === 'repo'
      ? 'Enter a valid git repo URL and branch'
      : 'Pick a preview app'
    return
  }
  if (!isLocal.value && !canContinueStep1()) {
    errorMessage.value = 'Paste cloud credentials to continue'
    return
  }
  if (isLocal.value) {
    await refreshKindStatus()
    if (localLaunchBlocked.value) {
      errorMessage.value = kindStatus.value?.message
        || 'Local Sandbox cluster is not ready. Fix tools/cluster before launching.'
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
      payload.workload_image = form.workload_image.trim() || 'nginx:1.27-alpine'
    }
    if (form.workspace_id) {
      payload.workspace_id = form.workspace_id
    } else if (sourceMode.value === 'template') {
      payload.template_id = form.template_id
    } else {
      payload.git_repo_url = form.git_repo_url.trim()
      payload.git_branch = form.git_branch.trim()
      if (form.github_pr_number && form.github_pr_number > 0) {
        payload.github_pr_number = form.github_pr_number
      }
    }
    if (form.provider !== 'local') {
      payload.credentials = { ...form.credentials }
    }
    const env = await launchPreview(payload)
    await navigateTo(`/environments/${env.id}`)
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Launch failed'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-8 animate-fade-up">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        One-click preview
      </p>
      <h1 class="text-3xl font-semibold tracking-tight md:text-4xl">Launch a preview app</h1>
      <p class="max-w-2xl text-sm text-[var(--lp-muted)]">
        Deploy a catalog demo or your own repo. Local Sandbox is the default happy path.
        Need custom YAML, Helm, or Terraform?
        <NuxtLink to="/provision" class="font-medium text-[var(--lp-accent)] hover:underline">
          Use Provision →
        </NuxtLink>
        - Launch stays for ephemeral app URLs; Provision is for IaC workspaces.
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
        <span>AI Analyze &amp; Fix</span>
      </button>
    </div>

    <!-- Local: single screen -->
    <section v-if="isLocal" class="lp-glass space-y-6 rounded-xl p-6">
      <div>
        <h2 class="text-lg font-semibold">Local (Sandbox)</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">
          Launchpad starts the local cluster if needed (~1-2 min first time). No cloud credentials.
          Open app uses the NodePort URL once the pod is Ready.
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
            <template v-if="kindStatusLoading">Checking local cluster…</template>
            <template v-else-if="kindStatusError">{{ kindStatusError }}</template>
            <template v-else-if="kindStatus">{{ kindStatus.message }}</template>
            <template v-else>Kind status unavailable.</template>
          </p>
          <button
            type="button"
            class="shrink-0 font-mono text-xs underline-offset-2 hover:underline"
            :disabled="kindStatusLoading"
            @click="refreshKindStatus"
          >
            Refresh
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
        <span class="lp-label">Workspace</span>
        <select v-model="form.workspace_id" class="lp-input" :disabled="loadingWorkspaces">
          <option :value="null">None</option>
          <option v-for="ws in workspaces" :key="ws.id" :value="ws.id">
            {{ ws.name }} ({{ ws.provider }}/{{ ws.engine }})
          </option>
        </select>
        <p class="text-xs text-[var(--lp-muted)]">
          <template v-if="usesWorkspaceSource && selectedWorkspace">
            Launching from workspace
            <strong class="text-[var(--lp-text)]">{{ selectedWorkspace.name }}</strong>
            - if the workspace has raw manifests, Launchpad applies them into a preview namespace
            (manifest deploy). Otherwise it uses the built-in preview profile.
          </template>
          <template v-else>
            Link a workspace to deploy its Kubernetes manifests, or choose a catalog template / git
            repo below.
            <NuxtLink to="/provision" class="text-[var(--lp-accent)] hover:underline">Create one</NuxtLink>
          </template>
        </p>
      </label>

      <template v-if="!usesWorkspaceSource">
      <div class="flex gap-2">
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-sm transition"
          :class="
            sourceMode === 'template'
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
              : 'border-[var(--lp-line)] text-[var(--lp-muted)]'
          "
          @click="sourceMode = 'template'"
        >
          Catalog template
        </button>
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-sm transition"
          :class="
            sourceMode === 'repo'
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
              : 'border-[var(--lp-line)] text-[var(--lp-muted)]'
          "
          @click="sourceMode = 'repo'"
        >
          Your repository
        </button>
      </div>

      <div v-if="sourceMode === 'template'">
        <p v-if="loadingTemplates" class="text-sm text-[var(--lp-muted)]">Loading templates…</p>
        <div v-else class="grid gap-3">
          <button
            v-for="tpl in templates"
            :key="tpl.id"
            type="button"
            class="rounded-lg border p-4 text-left transition"
            :class="
              form.template_id === tpl.id
                ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
                : 'border-[var(--lp-line)] hover:bg-[var(--lp-panel-2)]'
            "
            @click="selectTemplate(tpl)"
          >
            <p class="text-sm font-medium">{{ tpl.title }}</p>
            <p class="mt-1 text-xs text-[var(--lp-muted)]">{{ tpl.description }}</p>
            <p class="mt-2 font-mono text-[10px] text-[var(--lp-muted)]">
              image {{ tpl.workload_image }}
            </p>
          </button>
        </div>
      </div>
      <div v-else class="grid gap-3 sm:grid-cols-3">
        <label class="block space-y-2 sm:col-span-2">
          <span class="lp-label">Git repository URL</span>
          <input
            v-model="form.git_repo_url"
            class="lp-input font-mono text-xs"
            placeholder="https://github.com/org/app.git"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Branch</span>
          <input v-model="form.git_branch" class="lp-input font-mono text-xs" placeholder="main">
        </label>
        <label class="block space-y-2 sm:col-span-3">
          <span class="lp-label">GitHub PR number (optional)</span>
          <input
            v-model.number="form.github_pr_number"
            type="number"
            min="1"
            class="lp-input max-w-xs"
            placeholder="42"
          >
          <p class="text-xs text-[var(--lp-muted)]">
            When Running, Launchpad smokes the preview URL, posts a PR comment + commit status
            (Open in Launchpad), and exposes a stable <code class="font-mono text-[10px]">/pr/{n}</code> URL.
            Synchronize rebuilds; closing or merging the PR tears it down automatically.
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
          <span class="lp-label">Environment name</span>
          <input v-model="form.name" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">TTL</span>
          <div class="flex gap-2">
            <input
              v-model.number="form.ttl_value"
              type="number"
              min="1"
              :max="form.ttl_unit === 'minutes' ? 10080 : 168"
              class="lp-input flex-1"
            >
            <select v-model="form.ttl_unit" class="lp-input w-28">
              <option value="hours">Hours</option>
              <option value="minutes">Minutes</option>
            </select>
          </div>
        </label>
        <label v-if="!buildsFromRepo && !workspaceHasManifests" class="block space-y-2 sm:col-span-2">
          <span class="lp-label">Container image</span>
          <input
            v-model="form.workload_image"
            class="lp-input font-mono text-xs"
            placeholder="nginx:1.27-alpine"
            autocomplete="off"
          >
          <p class="text-xs text-[var(--lp-muted)]">
            Default stays <span class="font-mono">nginx:1.27-alpine</span>.
          </p>
        </label>
      </div>

      <p v-if="!usesWorkspaceSource" class="text-xs text-[var(--lp-muted)]">
        After launch: push to
        <span class="font-mono text-[var(--lp-text)]">{{ form.git_branch || 'your branch' }}</span>
        rebuilds the preview when a GitHub webhook is configured.
      </p>

      <div class="flex justify-end">
        <button
          type="button"
          class="lp-btn-primary"
          :disabled="submitting || kindStatusLoading || localLaunchBlocked"
          @click="launch"
        >
          {{ submitting ? 'Starting cluster & launching…' : 'Launch preview' }}
        </button>
      </div>
    </section>

    <!-- Cloud step 1 -->
    <section v-else-if="step === 1" class="lp-glass space-y-5 rounded-xl p-6">
      <div>
        <h2 class="text-lg font-semibold">Choose target</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">
          Local uses kind on your machine. Cloud options encrypt credentials onto a linked workspace,
          or reuse an existing Provision workspace.
        </p>
      </div>

      <label class="block space-y-2">
        <span class="lp-label">Workspace</span>
        <select v-model="form.workspace_id" class="lp-input" :disabled="loadingWorkspaces">
          <option :value="null">None - use catalog or git repo</option>
          <option v-for="ws in workspaces" :key="ws.id" :value="ws.id">
            {{ ws.name }} · {{ ws.provider }}/{{ ws.engine }}
          </option>
        </select>
        <p v-if="selectedWorkspace" class="text-xs text-[var(--lp-muted)]">
          <template v-if="usesWorkspaceSource">
            Launching from workspace
            <strong class="text-[var(--lp-text)]">{{ selectedWorkspace.name }}</strong>
            - skip catalog/git on the next step.
          </template>
          <template v-else>
            Using stored credentials from
            <strong class="text-[var(--lp-text)]">{{ selectedWorkspace.name }}</strong>
            when available - skip credential fields below.
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
          {{ usesWorkspaceSource ? 'Continue to launch' : 'Continue' }}
        </button>
      </div>
    </section>

    <!-- Cloud step 2: source -->
    <section v-else-if="step === 2 && showSourceStep" class="lp-glass space-y-5 rounded-xl p-6">
      <div>
        <h2 class="text-lg font-semibold">Pick source</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">
          Use a catalog template or point at your own git repository and branch.
        </p>
      </div>
      <div class="flex gap-2">
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-sm transition"
          :class="
            sourceMode === 'template'
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
              : 'border-[var(--lp-line)] text-[var(--lp-muted)]'
          "
          @click="sourceMode = 'template'"
        >
          Catalog template
        </button>
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-sm transition"
          :class="
            sourceMode === 'repo'
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
              : 'border-[var(--lp-line)] text-[var(--lp-muted)]'
          "
          @click="sourceMode = 'repo'"
        >
          Your repository
        </button>
      </div>
      <p v-if="loadingTemplates && sourceMode === 'template'" class="text-sm text-[var(--lp-muted)]">
        Loading templates…
      </p>
      <div v-else-if="sourceMode === 'template'" class="grid gap-3">
        <button
          v-for="tpl in templates"
          :key="tpl.id"
          type="button"
          class="rounded-lg border p-4 text-left transition"
          :class="
            form.template_id === tpl.id
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
              : 'border-[var(--lp-line)] hover:bg-[var(--lp-panel-2)]'
          "
          @click="form.template_id = tpl.id"
        >
          <div class="flex items-start justify-between gap-3">
            <div>
              <p class="text-sm font-medium">{{ tpl.title }}</p>
              <p class="mt-1 text-xs text-[var(--lp-muted)]">{{ tpl.description }}</p>
            </div>
            <span class="font-mono text-xs text-[var(--lp-accent)]">${{ tpl.hourly_cost_hint }}/hr</span>
          </div>
        </button>
      </div>
      <div v-else class="grid gap-3 sm:grid-cols-3">
        <label class="block space-y-2 sm:col-span-2">
          <span class="lp-label">Git repository URL</span>
          <input
            v-model="form.git_repo_url"
            class="lp-input font-mono text-xs"
            placeholder="https://github.com/org/app.git"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Branch</span>
          <input v-model="form.git_branch" class="lp-input font-mono text-xs" placeholder="main">
        </label>
        <label class="block space-y-2 sm:col-span-3">
          <span class="lp-label">GitHub PR number (optional)</span>
          <input
            v-model.number="form.github_pr_number"
            type="number"
            min="1"
            class="lp-input max-w-xs"
            placeholder="42"
          >
          <p class="text-xs text-[var(--lp-muted)]">
            Enables stable /pr/{n} URL, smoke + status checks, sync rebuilds, and auto-destroy on PR close.
          </p>
        </label>
      </div>
      <div class="flex justify-between">
        <button type="button" class="lp-btn-ghost" @click="step = 1">Back</button>
        <button type="button" class="lp-btn-primary" @click="goNext">Continue</button>
      </div>
    </section>

    <!-- Cloud confirm -->
    <section v-else-if="step === confirmStep" class="lp-glass space-y-5 rounded-xl p-6">
      <div>
        <h2 class="text-lg font-semibold">Confirm &amp; launch</h2>
        <p class="mt-1 text-sm text-[var(--lp-muted)]">
          You’ll get status logs, TTL, and an Open app link when the workload is Running.
        </p>
      </div>
      <label class="block space-y-2">
        <span class="lp-label">Environment name</span>
        <input v-model="form.name" class="lp-input" autocomplete="off">
      </label>
      <label class="block space-y-2">
        <span class="lp-label">TTL</span>
        <div class="flex gap-2">
          <input
            v-model.number="form.ttl_value"
            type="number"
            min="1"
            :max="form.ttl_unit === 'minutes' ? 10080 : 168"
            class="lp-input flex-1"
          >
          <select v-model="form.ttl_unit" class="lp-input w-28">
            <option value="hours">Hours</option>
            <option value="minutes">Minutes</option>
          </select>
        </div>
      </label>
      <label
        v-if="!buildsFromRepo && !workspaceHasManifests"
        class="block space-y-2"
      >
        <span class="lp-label">Container image</span>
        <input
          v-model="form.workload_image"
          class="lp-input font-mono text-xs"
          placeholder="nginx:1.27-alpine"
          autocomplete="off"
      </label>
      <div class="space-y-2 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 p-4">
        <span class="lp-label">Ephemeral Datastores (opt-in)</span>
        <div class="grid gap-2 sm:grid-cols-2 pt-1">
          <label class="flex items-center gap-2 text-xs cursor-pointer">
            <input v-model="form.enable_postgres" type="checkbox" class="accent-[var(--lp-accent)]">
            <span>Postgres (in-cluster)</span>
          </label>
          <label class="flex items-center gap-2 text-xs cursor-pointer">
            <input v-model="form.enable_redis" type="checkbox" class="accent-[var(--lp-accent)]">
            <span>Redis (in-cluster)</span>
          </label>
        </div>
      </div>
      <dl class="grid gap-3 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/40 p-4 text-sm sm:grid-cols-2">
        <div>
          <dt class="lp-label">Target</dt>
          <dd class="mt-1">{{ providerLabel }}</dd>
        </div>
        <div>
          <dt class="lp-label">Source</dt>
          <dd class="mt-1 break-all">{{ sourceSummary }}</dd>
        </div>
        <div v-if="selectedWorkspace && !usesWorkspaceSource">
          <dt class="lp-label">Workspace</dt>
          <dd class="mt-1">{{ selectedWorkspace.name }} ({{ selectedWorkspace.provider }})</dd>
        </div>
        <div>
          <dt class="lp-label">Est. hourly</dt>
          <dd class="mt-1 font-mono text-[var(--lp-accent)]">${{ hourlyDisplay }}/hr</dd>
        </div>
        <div>
          <dt class="lp-label">Image</dt>
          <dd class="mt-1 break-all font-mono text-xs">{{ imageSummary }}</dd>
        </div>
        <div>
          <dt class="lp-label">Rebuilds</dt>
          <dd class="mt-1 text-[var(--lp-muted)]">Push to branch when webhook is configured</dd>
        </div>
      </dl>
      <div class="flex justify-between">
        <button type="button" class="lp-btn-ghost" @click="goBackFromConfirm">Back</button>
        <button
          type="button"
          class="lp-btn-primary"
          :disabled="submitting"
          @click="launch"
        >
          {{ submitting ? 'Launching…' : 'Launch preview' }}
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

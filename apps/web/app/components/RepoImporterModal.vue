<script setup lang="ts">
import type {
  DatastoreImportConfig,
  DetectedService,
  EnvVarOverride,
  RepoImportSession,
} from '~/types/repoImport'
import type { GitHost } from '~/types/git'
import type {
  GitHubInstallationItem,
  GitHubRepositoryItem,
  GitHubRepositorySearchItem,
  GitlabProjectItem,
} from '~/types/provisioning'
import { githubCloneUrl } from '~/utils/githubAccount'

const open = defineModel<boolean>('open', { default: false })

const props = withDefaults(
  defineProps<{
    /** Prefill Launchpad project (from project detail / query). */
    launchpadProjectId?: string | null
  }>(),
  {
    launchpadProjectId: null,
  },
)

const emit = defineEmits<{
  saved: [workspaceId: string]
}>()

const { startImport, saveImport, discardImport } = useRepoImport()
const {
  listGithubInstallations,
  getGitlabStatus,
} = useProvisioning()
const { listProjects, projects: launchpadProjects } = useProjects()
const { t } = useI18n()

const step = ref<'url' | 'preview'>('url')
const gitHost = ref<GitHost>('github')
const repoUrl = ref('')
const branch = ref('main')
const workspaceName = ref('')
const selectedLaunchpadProjectId = ref('')
const session = ref<RepoImportSession | null>(null)
const services = ref<DetectedService[]>([])
const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const runtimeMode = ref<'kubernetes' | 'docker_compose' | 'running_instance'>('kubernetes')
const processStrategy = ref<'docker' | 'systemd' | 'pm2'>('docker')
const reverseProxy = ref<'none' | 'nginx' | 'caddy'>('none')
const iacEngine = ref<'terraform' | 'opentofu' | 'pulumi'>('terraform')
const enableIac = ref(true)
const enableCicd = ref(false)
const cicdPlatform = ref<'github' | 'gitlab'>('github')
const envVars = ref<EnvVarOverride[]>([])
const datastoreConfigs = ref<DatastoreImportConfig[]>([])

const githubInstallations = ref<GitHubInstallationItem[]>([])
const selectedInstallationId = ref<number | null>(null)
const selectedRepoFullName = ref('')
const selectedGitlabPath = ref('')
const loadingAccounts = ref(false)
const gitlabConnected = ref(false)
const gitlabBaseUrl = ref('https://gitlab.com')

const urlPlaceholder = computed(() =>
  gitHost.value === 'github'
    ? 'https://github.com/org/repo.git'
    : 'https://gitlab.com/group/project.git',
)

const canAnalyze = computed(() => {
  const url = repoUrl.value.trim().toLowerCase()
  if (!url) return false
  if (!(url.startsWith('https://') || url.startsWith('http://') || url.startsWith('git@') || url.startsWith('ssh://'))) {
    return false
  }
  if (gitHost.value === 'github' && url.includes('gitlab')) return false
  if (gitHost.value === 'gitlab' && (url.includes('github.com') || url.includes('www.github.com'))) return false
  return true
})

function deriveName(url: string) {
  const cleaned = url.trim().replace(/\.git$/i, '')
  const parts = cleaned.split('/').filter(Boolean)
  const leaf = parts[parts.length - 1] || 'imported'
  return leaf.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48) || 'imported'
}

function onInstallationChange(installationId: number | null) {
  selectedInstallationId.value = installationId
  selectedRepoFullName.value = ''
  repoUrl.value = ''
}

function onGithubRepoSelect(repo: GitHubRepositorySearchItem | GitHubRepositoryItem) {
  const fullName = 'fullName' in repo ? repo.fullName : repo.full_name
  const defaultBranch = 'defaultBranch' in repo ? repo.defaultBranch : repo.default_branch
  selectedRepoFullName.value = fullName
  repoUrl.value = githubCloneUrl(fullName)
  branch.value = defaultBranch || 'main'
  workspaceName.value = deriveName(fullName)
}

async function loadConnectedAccounts() {
  loadingAccounts.value = true
  error.value = null
  try {
    if (gitHost.value === 'github') {
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
      selectedGitlabPath.value = ''
      const status = await getGitlabStatus()
      gitlabConnected.value = Boolean(status.connected)
      gitlabBaseUrl.value = status.base_url || 'https://gitlab.com'
    }
  } catch {
    githubInstallations.value = []
    gitlabConnected.value = false
  } finally {
    loadingAccounts.value = false
  }
}

function onGitlabProjectSelect(project: GitlabProjectItem) {
  selectedGitlabPath.value = project.path_with_namespace
  const base = gitlabBaseUrl.value.replace(/\/$/, '')
  repoUrl.value =
    project.http_url_to_repo || `${base}/${project.path_with_namespace}.git`
  branch.value = project.default_branch || 'main'
  workspaceName.value = deriveName(project.path_with_namespace)
}

watch(gitHost, () => {
  repoUrl.value = ''
  selectedRepoFullName.value = ''
  selectedGitlabPath.value = ''
  void loadConnectedAccounts()
})

watch(open, (isOpen) => {
  if (isOpen) {
    error.value = null
    void loadConnectedAccounts()
    void loadLaunchpadProjects()
  }
})

watch(
  () => props.launchpadProjectId,
  (id) => {
    if (id) selectedLaunchpadProjectId.value = id
  },
  { immediate: true },
)

async function loadLaunchpadProjects() {
  try {
    const listed = await listProjects()
    const preferred = props.launchpadProjectId?.trim()
    if (preferred && listed.some((p) => p.id === preferred)) {
      selectedLaunchpadProjectId.value = preferred
    } else if (!selectedLaunchpadProjectId.value && listed[0]) {
      selectedLaunchpadProjectId.value = listed[0].id
    }
  } catch {
    // Projects optional until user creates one
  }
}

async function analyze() {
  error.value = null
  if (!canAnalyze.value) {
    error.value = gitHost.value === 'github'
      ? t('import.invalidGithubUrl')
      : t('import.invalidGitlabUrl')
    return
  }
  if (gitHost.value === 'github' && githubInstallations.value.length > 1 && !selectedInstallationId.value) {
    error.value = t('import.selectGithubAccount')
    return
  }
  loading.value = true
  try {
    if (!workspaceName.value.trim()) {
      workspaceName.value = deriveName(repoUrl.value)
    }
    const result = await startImport({
      git_repo_url: repoUrl.value.trim(),
      git_branch: branch.value.trim() || 'main',
      use_github_app_token: gitHost.value === 'github',
      github_installation_id:
        gitHost.value === 'github' ? selectedInstallationId.value : null,
    })
    session.value = result
    services.value = result.services.map((s) => ({ ...s }))
    envVars.value = (result.detection.env_example || []).map((item) => ({
      key: item.key,
      value: item.suggested_value || item.example_value || '',
    }))
    const suggestions = result.datastore_suggestions || {}
    datastoreConfigs.value = (result.detection.datastores || []).map((kind) => {
      const preferExternal = !(result.detection.has_kubernetes)
      return {
        kind,
        placement: preferExternal ? 'external' as const : 'in_cluster' as const,
        connection_url: preferExternal
          ? (suggestions[kind]?.external || '')
          : (suggestions[kind]?.in_cluster || ''),
      }
    })
    // Suggest a mode from repo artifacts; user can still override.
    if (result.detection.has_kubernetes) {
      runtimeMode.value = 'kubernetes'
    } else if (result.detection.has_compose) {
      runtimeMode.value = 'docker_compose'
    }
    step.value = 'preview'
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('import.importFailed')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!session.value) return
  error.value = null
  const name = workspaceName.value.trim().toLowerCase()
  if (!/^[a-z][a-z0-9-]{2,63}$/.test(name)) {
    error.value = t('import.workspaceNameInvalid')
    return
  }
  saving.value = true
  try {
    const result = await saveImport({
      importId: session.value.import_id,
      name,
      services: services.value.map((s) => ({
        id: s.id,
        enabled: s.enabled,
        port: s.port,
        is_preview_target: s.is_preview_target,
        name: s.name,
      })),
      runtime_mode: runtimeMode.value,
      process_strategy: processStrategy.value,
      reverse_proxy: reverseProxy.value,
      iac_engine: iacEngine.value,
      enable_iac: enableIac.value,
      enable_cicd: enableCicd.value,
      cicd_platform: cicdPlatform.value,
      ensure_local_cluster: runtimeMode.value === 'kubernetes',
      project_id: selectedLaunchpadProjectId.value || null,
      env_vars: envVars.value,
      datastores: datastoreConfigs.value,
    })
    open.value = false
    emit('saved', result.workspace_id)
    await navigateTo(`/workspaces/${result.workspace_id}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('import.saveWorkspaceFailed')
  } finally {
    saving.value = false
  }
}

async function reset() {
  if (session.value) {
    try {
      await discardImport(session.value.import_id)
    } catch {
      // best-effort
    }
  }
  session.value = null
  services.value = []
  envVars.value = []
  datastoreConfigs.value = []
  step.value = 'url'
  error.value = null
}

async function close() {
  await reset()
  open.value = false
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 backdrop-blur-sm sm:p-8"
      @click.self="close"
    >
      <div class="lp-glass my-4 w-full max-w-3xl space-y-5 rounded-2xl border border-[var(--lp-line)] p-6 shadow-2xl">
        <div class="flex items-start justify-between gap-3">
          <div>
            <h2 class="text-xl font-semibold">{{ t('import.modalTitle') }}</h2>
            <p class="mt-1 text-sm text-[var(--lp-muted)]">
              {{ t('import.modalBlurbFull') }}
            </p>
          </div>
          <button type="button" class="lp-btn-ghost px-2" :aria-label="t('common.close')" @click="close">
            <span class="material-symbols-outlined">close</span>
          </button>
        </div>

        <div
          v-if="error"
          class="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200"
        >
          {{ error }}
        </div>

        <template v-if="step === 'url'">
          <GitProviderPicker v-model="gitHost" />

          <template v-if="gitHost === 'github'">
            <p v-if="loadingAccounts" class="text-xs text-[var(--lp-muted)]">{{ t('import.loadingGithubAccounts') }}</p>
            <template v-else-if="githubInstallations.length">
              <GithubInstallationPicker
                :model-value="selectedInstallationId"
                :installations="githubInstallations"
                manage-link
                @update:model-value="onInstallationChange"
              />
              <div class="block space-y-2">
                <span class="lp-label">{{ t('import.searchRepo') }}</span>
                <GithubRepoPicker
                  v-model="selectedRepoFullName"
                  :installation-id="selectedInstallationId"
                  @select-repo="onGithubRepoSelect"
                />
              </div>
            </template>
            <p v-else class="text-xs text-[var(--lp-muted)]">
              {{ t('import.noGithubInstalls') }}
              <NuxtLink to="/integrations/github" class="text-[var(--lp-accent)] hover:underline">{{ t('integrations.connectGithub') }}</NuxtLink>
              {{ t('import.connectGithubOrPaste') }}
            </p>
          </template>

          <label v-if="gitHost === 'gitlab' && gitlabConnected" class="block space-y-2">
            <span class="lp-label">{{ t('import.connectedGitlabProject') }}</span>
            <GitlabRepoPicker
              v-model="selectedGitlabPath"
              @select-project="onGitlabProjectSelect"
            />
          </label>
          <p v-else-if="gitHost === 'gitlab' && !loadingAccounts" class="text-xs text-[var(--lp-muted)]">
            <template v-if="!gitlabConnected">
              {{ t('import.gitlabNotConnected') }}
              <NuxtLink to="/integrations/gitlab" class="text-[var(--lp-accent)] hover:underline">{{ t('integrations.connectGitlab') }}</NuxtLink>
              {{ t('import.gitlabPasteUrl') }}
            </template>
          </p>

          <div class="grid gap-3 sm:grid-cols-3">
            <label class="block space-y-2 sm:col-span-2">
              <span class="lp-label">{{ gitHost === 'github' ? t('import.repoUrlGithub') : t('import.repoUrlGitlab') }}</span>
              <input
                v-model="repoUrl"
                class="lp-input font-mono text-xs"
                :placeholder="urlPlaceholder"
                autocomplete="off"
              >
            </label>
            <label class="block space-y-2">
              <span class="lp-label">{{ t('common.branch') }}</span>
              <input v-model="branch" class="lp-input font-mono text-xs" placeholder="main">
            </label>
          </div>

          <label class="block space-y-2">
            <span class="lp-label">{{ t('import.workspaceName') }}</span>
            <input
              v-model="workspaceName"
              class="lp-input"
              placeholder="my-service"
              autocomplete="off"
            >
          </label>

          <label v-if="launchpadProjects.length" class="block space-y-2">
            <span class="lp-label">{{ t('provision.launchpadProject') }}</span>
            <select v-model="selectedLaunchpadProjectId" class="lp-input">
              <option
                v-for="proj in launchpadProjects"
                :key="proj.id"
                :value="proj.id"
              >
                {{ proj.name }}
              </option>
            </select>
            <p class="text-[11px] text-[var(--lp-muted)]">{{ t('provision.launchpadProjectBlurb') }}</p>
          </label>

          <div class="flex justify-end gap-2">
            <button type="button" class="lp-btn-ghost" @click="close">{{ t('common.cancel') }}</button>
            <button
              type="button"
              class="lp-btn-primary"
              :disabled="loading || !canAnalyze"
              @click="analyze"
            >
              {{ loading ? t('import.cloningDetecting') : t('import.analyzeRepo') }}
            </button>
          </div>
        </template>

        <template v-else-if="session">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="text-base font-semibold">{{ t('import.confirmStack') }}</h3>
              <p class="mt-1 font-mono text-xs text-[var(--lp-muted)]">
                {{ session.git_repo_url }} @ {{ session.git_branch }}
                <span v-if="session.commit_sha"> · {{ session.commit_sha.slice(0, 8) }}</span>
              </p>
            </div>
            <button type="button" class="lp-btn-ghost text-xs" @click="reset">{{ t('import.startOver') }}</button>
          </div>

          <label class="block space-y-2">
            <span class="lp-label">{{ t('import.workspaceName') }}</span>
            <input v-model="workspaceName" class="lp-input" autocomplete="off">
          </label>

          <label v-if="launchpadProjects.length" class="block space-y-2">
            <span class="lp-label">{{ t('provision.launchpadProject') }}</span>
            <select v-model="selectedLaunchpadProjectId" class="lp-input">
              <option
                v-for="proj in launchpadProjects"
                :key="proj.id"
                :value="proj.id"
              >
                {{ proj.name }}
              </option>
            </select>
          </label>

          <div class="grid gap-3 sm:grid-cols-2">
            <label class="block space-y-2">
              <span class="lp-label">{{ t('import.runtimeMode') }}</span>
              <select v-model="runtimeMode" class="lp-input">
                <option value="kubernetes">{{ t('provision.runtimeMode.modes.kubernetes.title') }}</option>
                <option value="docker_compose">{{ t('provision.runtimeMode.modes.docker_compose.title') }}</option>
                <option value="running_instance">{{ t('provision.runtimeMode.modes.running_instance.title') }}</option>
              </select>
            </label>
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.iacEngine') }}</span>
              <select
                v-model="iacEngine"
                class="lp-input"
                :disabled="!enableIac || runtimeMode === 'kubernetes'"
              >
                <option value="terraform">Terraform</option>
                <option value="opentofu">OpenTofu</option>
                <option value="pulumi">Pulumi</option>
              </select>
            </label>
          </div>
          <div
            v-if="runtimeMode === 'running_instance'"
            class="grid gap-3 sm:grid-cols-2"
          >
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.runtimeMode.attach.processStrategy') }}</span>
              <select v-model="processStrategy" class="lp-input">
                <option value="docker">{{ t('provision.runtimeMode.attach.strategies.docker') }}</option>
                <option value="systemd">{{ t('provision.runtimeMode.attach.strategies.systemd') }}</option>
                <option value="pm2">{{ t('provision.runtimeMode.attach.strategies.pm2') }}</option>
              </select>
              <p class="text-[11px] text-[var(--lp-muted)]">
                {{ t(`provision.runtimeMode.attach.strategyHints.${processStrategy}`) }}
              </p>
            </label>
            <label class="block space-y-2">
              <span class="lp-label">{{ t('provision.runtimeMode.attach.reverseProxy') }}</span>
              <select v-model="reverseProxy" class="lp-input">
                <option value="none">{{ t('provision.runtimeMode.attach.proxies.none') }}</option>
                <option value="nginx">{{ t('provision.runtimeMode.attach.proxies.nginx') }}</option>
                <option value="caddy">{{ t('provision.runtimeMode.attach.proxies.caddy') }}</option>
              </select>
            </label>
          </div>
          <div
            v-if="session.detection.has_kubernetes || session.detection.has_compose"
            class="rounded-lg border border-[var(--lp-accent)]/30 bg-[var(--lp-accent)]/8 px-3 py-2 text-xs text-[var(--lp-text)]"
          >
            <p v-if="session.detection.has_kubernetes" class="font-medium">
              {{ t('import.detectedKubernetes') }}
            </p>
            <p v-if="session.detection.has_compose" class="font-medium" :class="session.detection.has_kubernetes ? 'mt-1' : ''">
              {{ t('import.detectedCompose') }}
            </p>
            <p class="mt-1 text-[var(--lp-muted)]">{{ t('import.runtimeModeOverrideHint') }}</p>
          </div>
          <div class="flex flex-wrap gap-4 text-sm">
            <label class="flex items-center gap-2 cursor-pointer">
              <input
                v-model="enableIac"
                type="checkbox"
                class="accent-[var(--lp-accent)]"
                :disabled="runtimeMode === 'kubernetes'"
              >
              <span>{{ t('import.enableIac') }}</span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input v-model="enableCicd" type="checkbox" class="accent-[var(--lp-accent)]">
              <span>{{ t('import.enableCicd') }}</span>
            </label>
            <label v-if="enableCicd" class="flex items-center gap-2">
              <span class="lp-label">{{ t('import.cicdPlatform') }}</span>
              <select v-model="cicdPlatform" class="lp-input w-36">
                <option value="github">GitHub</option>
                <option value="gitlab">GitLab</option>
              </select>
            </label>
          </div>
          <p class="text-xs text-[var(--lp-muted)]">
            {{ t('import.runtimeModeHint') }}
          </p>

          <ImportEnvConfigPanel
            v-model:env-vars="envVars"
            v-model:datastore-configs="datastoreConfigs"
            :env-example="session.detection.env_example || []"
            :detected-datastores="session.detection.datastores || []"
            :suggestions="session.datastore_suggestions || {}"
            :runtime-mode="runtimeMode"
          />

          <DetectedStackPreview
            :detection="session.detection"
            :services="services"
            @update:services="services = $event"
          />

          <div class="flex flex-wrap justify-between gap-3 pt-2">
            <button type="button" class="lp-btn-ghost" :disabled="saving" @click="close">{{ t('common.cancel') }}</button>
            <button type="button" class="lp-btn-primary" :disabled="saving" @click="save">
              {{ saving ? t('import.savingWorkspace') : t('import.saveAsWorkspace') }}
            </button>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>

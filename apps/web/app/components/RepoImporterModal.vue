<script setup lang="ts">
import type { DetectedService, RepoImportSession } from '~/types/repoImport'
import type { GitHost } from '~/types/git'
import type {
  GitHubInstallationItem,
  GitHubRepositoryItem,
  GitHubRepositorySearchItem,
} from '~/types/provisioning'
import { githubCloneUrl } from '~/utils/githubAccount'

const open = defineModel<boolean>('open', { default: false })

const emit = defineEmits<{
  saved: [workspaceId: string]
}>()

const { startImport, saveImport, discardImport } = useRepoImport()
const {
  listGithubInstallations,
  listGitlabProjects,
  getGitlabStatus,
} = useProvisioning()
const { t } = useI18n()

const step = ref<'url' | 'preview'>('url')
const gitHost = ref<GitHost>('github')
const repoUrl = ref('')
const branch = ref('main')
const workspaceName = ref('')
const session = ref<RepoImportSession | null>(null)
const services = ref<DetectedService[]>([])
const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

const githubInstallations = ref<GitHubInstallationItem[]>([])
const selectedInstallationId = ref<number | null>(null)
const selectedRepoFullName = ref('')
const gitlabProjects = ref<Array<{ path: string; clone_url: string; default_branch: string }>>([])
const loadingAccounts = ref(false)
const gitlabConnected = ref(false)

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
      const status = await getGitlabStatus()
      gitlabConnected.value = Boolean(status.connected)
      if (!status.connected) {
        gitlabProjects.value = []
        return
      }
      const projects = await listGitlabProjects()
      gitlabProjects.value = projects.map((p) => ({
        path: p.path_with_namespace,
        clone_url: p.http_url_to_repo || `${status.base_url.replace(/\/$/, '')}/${p.path_with_namespace}.git`,
        default_branch: p.default_branch || 'main',
      }))
    }
  } catch {
    githubInstallations.value = []
    gitlabProjects.value = []
  } finally {
    loadingAccounts.value = false
  }
}

function selectGitlabProject(path: string) {
  const match = gitlabProjects.value.find((p) => p.path === path)
  if (!match) return
  repoUrl.value = match.clone_url
  branch.value = match.default_branch || 'main'
  workspaceName.value = deriveName(match.path)
}

watch(gitHost, () => {
  repoUrl.value = ''
  selectedRepoFullName.value = ''
  void loadConnectedAccounts()
})

watch(open, (isOpen) => {
  if (isOpen) {
    error.value = null
    void loadConnectedAccounts()
  }
})

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

          <label v-if="gitHost === 'gitlab' && gitlabProjects.length" class="block space-y-2">
            <span class="lp-label">{{ t('import.connectedGitlabProject') }}</span>
            <select
              class="lp-input"
              :disabled="loadingAccounts"
              @change="selectGitlabProject(($event.target as HTMLSelectElement).value)"
            >
              <option value="">{{ t('import.selectProject') }}</option>
              <option v-for="p in gitlabProjects" :key="p.path" :value="p.path">
                {{ p.path }}
              </option>
            </select>
          </label>
          <p v-else-if="gitHost === 'gitlab' && !loadingAccounts" class="text-xs text-[var(--lp-muted)]">
            <template v-if="!gitlabConnected">
              {{ t('import.gitlabNotConnected') }}
              <NuxtLink to="/integrations/gitlab" class="text-[var(--lp-accent)] hover:underline">{{ t('integrations.connectGitlab') }}</NuxtLink>
              {{ t('import.gitlabPasteUrl') }}
            </template>
            <template v-else>
              {{ t('import.noGitlabProjects') }}
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

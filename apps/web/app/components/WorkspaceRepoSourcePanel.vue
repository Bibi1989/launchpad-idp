<script setup lang="ts">
/**
 * Link a GitHub/GitLab app repo for branch-based auto deploy, or import a repo.
 * Works with or without a workspace id (pending link emitted until create/save).
 */
import type { GitHost } from '~/types/git'
import type {
  GitHubAppStatus,
  WorkspaceCdMode,
  WorkspaceLinkedAppRepoResponse,
  WorkspaceLinkedRepoItem,
} from '~/types/provisioning'
import type { PendingWorkspaceRepoLink, WorkspaceSourceMode } from '~/types/workspaceRepo'
import { toastError } from '~/composables/useToast'
import { githubCloneUrl } from '~/utils/githubAccount'

const props = withDefaults(
  defineProps<{
    workspaceId?: string | null
    disabled?: boolean
    /** Hide outer title when nested under a parent heading. */
    embedded?: boolean
    /** Parent controls Link vs Import (hides internal tabs). */
    forceMode?: Extract<WorkspaceSourceMode, 'link' | 'import'> | null
    /** Prefill Launchpad project for import modal. */
    launchpadProjectId?: string | null
  }>(),
  {
    workspaceId: null,
    disabled: false,
    embedded: false,
    forceMode: null,
    launchpadProjectId: null,
  },
)

const emit = defineEmits<{
  pendingLink: [value: PendingWorkspaceRepoLink | null]
  imported: [workspaceId: string]
}>()

const pendingLink = defineModel<PendingWorkspaceRepoLink | null>('pendingLink', {
  default: null,
})
// Multi-repo: full list of repos to link. In create mode (no workspace id) this is
// staged and applied post-create; with a workspace id each change persists immediately.
const pendingLinks = defineModel<WorkspaceLinkedRepoItem[]>('pendingLinks', {
  default: () => [],
})

const { t } = useI18n()
const toast = useToast()
const {
  getGithubAppStatus,
  getGitlabStatus,
  getLinkedAppRepo,
  getWorkspaceLinkedRepos,
  setWorkspaceLinkedRepos,
  getWizardConfig,
} = useProvisioning()

// The authoritative set of linked repos shown as chips.
const linkedList = ref<WorkspaceLinkedRepoItem[]>([])

const mode = ref<'link' | 'import'>(props.forceMode ?? 'link')
const gitHost = ref<GitHost>('github')
const loading = ref(true)
const saving = ref(false)
const formError = ref<string | null>(null)
const status = ref<WorkspaceLinkedAppRepoResponse | null>(null)
const githubApp = ref<GitHubAppStatus | null>(null)
const gitlabConnected = ref(false)
const installationId = ref<number | null>(null)
const fullName = ref('')
const gitlabPath = ref('')
const gitBranch = ref('main')
const cdMode = ref<WorkspaceCdMode>('webhook')
const importOpen = ref(false)

const hasWorkspace = computed(() => Boolean(props.workspaceId?.trim()))
const activeMode = computed(() => props.forceMode ?? mode.value)

watch(
  () => props.forceMode,
  (next) => {
    if (next === 'link' || next === 'import') mode.value = next
  },
)

async function load() {
  loading.value = true
  formError.value = null
  try {
    const [gh, gl] = await Promise.all([
      getGithubAppStatus().catch(() => null),
      getGitlabStatus().catch(() => null),
    ])
    githubApp.value = gh
    gitlabConnected.value = Boolean(gl?.connected)

    if (!hasWorkspace.value) {
      status.value = null
      linkedList.value = [...pendingLinks.value]
      if (pendingLink.value?.kind === 'github') {
        gitHost.value = 'github'
        installationId.value = pendingLink.value.installation_id
        fullName.value = pendingLink.value.full_name
        gitBranch.value = pendingLink.value.git_branch
        cdMode.value = pendingLink.value.cd_mode
      } else if (pendingLink.value?.kind === 'gitlab') {
        gitHost.value = 'gitlab'
        gitBranch.value = pendingLink.value.git_branch
        gitlabPath.value = pendingLink.value.git_repo_url
          .replace(/^https?:\/\/[^/]+\//i, '')
          .replace(/\.git$/i, '')
          .replace(/\/$/, '')
      } else if (gh) {
        installationId.value =
          gh.default_installation_id ?? gh.installations[0]?.id ?? null
      }
      return
    }

    const workspaceId = props.workspaceId!.trim()
    try {
      linkedList.value = (await getWorkspaceLinkedRepos(workspaceId)).repos
    } catch {
      linkedList.value = []
    }
    const linkRes = await getLinkedAppRepo(workspaceId)
    status.value = linkRes
    if (linkRes.linked) {
      gitHost.value = 'github'
      installationId.value = linkRes.linked.installation_id
      fullName.value = linkRes.linked.full_name
      gitBranch.value = linkRes.linked.git_branch
      cdMode.value = linkRes.linked.cd_mode
    } else {
      try {
        const cfg = await getWizardConfig(workspaceId)
        const url = String(cfg.git_repo_url || '').trim()
        const branch = String(cfg.git_branch || '').trim()
        if (url) {
          if (url.includes('gitlab')) {
            gitHost.value = 'gitlab'
            gitlabPath.value = url
              .replace(/^https?:\/\/[^/]+\//i, '')
              .replace(/\.git$/i, '')
              .replace(/\/$/, '')
          }
          if (branch) gitBranch.value = branch
        }
      } catch {
        // optional
      }
      if (gh) {
        installationId.value =
          gh.default_installation_id ?? gh.installations[0]?.id ?? null
      }
    }
  } catch (err) {
    formError.value = toastError(err, t('scaffold.repoSource.loadFailed'))
  } finally {
    loading.value = false
  }
}

// --- Multi-repo linking ----------------------------------------------------

function currentItem(): WorkspaceLinkedRepoItem | null {
  if (gitHost.value === 'github') {
    if (!installationId.value || !fullName.value.trim()) {
      formError.value = t('workspaces.linkedRepo.repoRequired')
      return null
    }
    return {
      kind: 'github',
      git_repo_url: githubCloneUrl(fullName.value.trim()),
      git_branch: gitBranch.value.trim() || 'main',
      full_name: fullName.value.trim(),
      installation_id: installationId.value,
      cd_mode: cdMode.value,
    }
  }
  const path = gitlabPath.value.trim()
  if (!path) {
    formError.value = t('scaffold.repoSource.gitlabRepoRequired')
    return null
  }
  const cloneUrl = path.startsWith('http')
    ? path
    : `https://gitlab.com/${path.replace(/^\//, '')}.git`
  return {
    kind: 'gitlab',
    git_repo_url: cloneUrl,
    git_branch: gitBranch.value.trim() || 'main',
  }
}

function itemAsPending(item: WorkspaceLinkedRepoItem): PendingWorkspaceRepoLink {
  if (item.kind === 'github') {
    return {
      kind: 'github',
      installation_id: item.installation_id ?? 0,
      full_name: item.full_name ?? '',
      git_branch: item.git_branch,
      cd_mode: item.cd_mode ?? 'webhook',
    }
  }
  return { kind: 'gitlab', git_repo_url: item.git_repo_url, git_branch: item.git_branch }
}

function syncPendingModels() {
  pendingLinks.value = [...linkedList.value]
  const first = linkedList.value[0]
  pendingLink.value = first ? itemAsPending(first) : null
  emit('pendingLink', pendingLink.value)
}

function clearCurrentSelection() {
  fullName.value = ''
  gitlabPath.value = ''
  gitBranch.value = 'main'
}

async function onAddRepo() {
  if (saving.value || props.disabled) return
  const item = currentItem()
  if (!item) return
  if (linkedList.value.some((r) => r.git_repo_url === item.git_repo_url)) {
    clearCurrentSelection()
    return
  }
  const next = [...linkedList.value, item]
  if (!hasWorkspace.value) {
    linkedList.value = next
    syncPendingModels()
    toast.success(
      t('scaffold.repoSource.pendingSaved'),
      t('scaffold.repoSource.pendingSavedBlurb'),
    )
    clearCurrentSelection()
    return
  }
  saving.value = true
  formError.value = null
  try {
    const res = await setWorkspaceLinkedRepos(props.workspaceId!, next)
    linkedList.value = res.repos
    status.value = await getLinkedAppRepo(props.workspaceId!).catch(() => status.value)
    toast.success(t('workspaces.linkedRepo.saved'), res.message)
    clearCurrentSelection()
  } catch (err) {
    formError.value = toastError(err, t('workspaces.linkedRepo.saveFailed'))
    toast.error(t('workspaces.linkedRepo.saveFailed'), formError.value)
  } finally {
    saving.value = false
  }
}

async function removeRepoAt(index: number) {
  if (saving.value || props.disabled) return
  const next = linkedList.value.filter((_, i) => i !== index)
  if (!hasWorkspace.value) {
    linkedList.value = next
    syncPendingModels()
    return
  }
  saving.value = true
  formError.value = null
  try {
    const res = await setWorkspaceLinkedRepos(props.workspaceId!, next)
    linkedList.value = res.repos
    status.value = await getLinkedAppRepo(props.workspaceId!).catch(() => status.value)
  } catch (err) {
    formError.value = toastError(err, t('workspaces.linkedRepo.saveFailed'))
  } finally {
    saving.value = false
  }
}

function repoLabel(item: WorkspaceLinkedRepoItem): string {
  return item.full_name || item.git_repo_url.replace(/^https?:\/\//i, '').replace(/\.git$/i, '')
}

/** Which repo is primary: the flagged one, else the first (server marks exactly one). */
function isPrimary(item: WorkspaceLinkedRepoItem, index: number): boolean {
  const anyFlagged = linkedList.value.some((r) => r.primary)
  return anyFlagged ? Boolean(item.primary) : index === 0
}

async function setPrimary(index: number) {
  if (saving.value || props.disabled) return
  const next = linkedList.value.map((r, i) => ({ ...r, primary: i === index }))
  if (!hasWorkspace.value) {
    linkedList.value = next
    syncPendingModels()
    return
  }
  saving.value = true
  formError.value = null
  try {
    const res = await setWorkspaceLinkedRepos(props.workspaceId!, next)
    linkedList.value = res.repos
    status.value = await getLinkedAppRepo(props.workspaceId!).catch(() => status.value)
    toast.success(t('workspaces.linkedRepo.saved'), res.message)
  } catch (err) {
    formError.value = toastError(err, t('workspaces.linkedRepo.saveFailed'))
    toast.error(t('workspaces.linkedRepo.saveFailed'), formError.value)
  } finally {
    saving.value = false
  }
}

/** Provider from the actual URL host, not the stored kind (which can be wrong). */
function providerLabel(item: WorkspaceLinkedRepoItem): string {
  const url = (item.git_repo_url || '').toLowerCase()
  if (url.includes('gitlab')) return 'gitlab'
  if (url.includes('github')) return 'github'
  return item.kind
}

function onImportSaved(newWorkspaceId: string) {
  importOpen.value = false
  emit('imported', newWorkspaceId)
  if (newWorkspaceId && newWorkspaceId !== props.workspaceId) {
    void navigateTo(`/workspaces/${newWorkspaceId}`)
  }
}

onMounted(() => {
  void load()
})

watch(
  () => props.workspaceId,
  () => {
    void load()
  },
)
</script>

<template>
  <div
    class="space-y-3"
    :class="embedded ? '' : 'rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/30 p-4'"
  >
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div v-if="!embedded" class="min-w-0 space-y-1">
        <p class="flex items-center gap-2 text-sm font-semibold text-[var(--lp-text)]">
          <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">hub</span>
          {{ t('scaffold.repoSource.title') }}
        </p>
        <p class="text-xs text-[var(--lp-muted)]">
          {{ t('scaffold.repoSource.blurb') }}
        </p>
      </div>
      <div
        v-if="!forceMode"
        class="flex rounded-lg border border-[var(--lp-line)] p-0.5 text-xs"
        :class="embedded ? 'ml-auto' : ''"
      >
        <button
          type="button"
          class="rounded-md px-2.5 py-1.5 transition"
          :class="mode === 'link' ? 'bg-[var(--lp-accent)] text-[var(--lp-on-accent)]' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          :disabled="disabled"
          @click="mode = 'link'"
        >
          {{ t('scaffold.repoSource.modeLink') }}
        </button>
        <button
          type="button"
          class="rounded-md px-2.5 py-1.5 transition"
          :class="mode === 'import' ? 'bg-[var(--lp-accent)] text-[var(--lp-on-accent)]' : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
          :disabled="disabled"
          @click="mode = 'import'"
        >
          {{ t('scaffold.repoSource.modeImport') }}
        </button>
      </div>
    </div>

    <p
      v-if="!hasWorkspace && activeMode === 'link'"
      class="rounded-lg border border-[var(--lp-accent)]/25 bg-[var(--lp-accent)]/5 px-3 py-2 text-xs text-[var(--lp-muted)]"
    >
      {{ t('scaffold.repoSource.pendingHint') }}
    </p>

    <p v-if="loading" class="text-xs text-[var(--lp-muted)]">{{ t('common.loading') }}</p>
    <p v-else-if="formError" class="text-xs text-[var(--lp-danger)]">{{ formError }}</p>

    <template v-else-if="activeMode === 'link'">
      <div class="flex gap-2">
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-xs font-medium transition"
          :class="gitHost === 'github' ? 'border-[var(--lp-accent)] text-[var(--lp-accent)]' : 'border-[var(--lp-line)] text-[var(--lp-muted)]'"
          :disabled="disabled"
          @click="gitHost = 'github'"
        >
          GitHub
        </button>
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-xs font-medium transition"
          :class="gitHost === 'gitlab' ? 'border-[var(--lp-accent)] text-[var(--lp-accent)]' : 'border-[var(--lp-line)] text-[var(--lp-muted)]'"
          :disabled="disabled"
          @click="gitHost = 'gitlab'"
        >
          GitLab
        </button>
      </div>

      <template v-if="gitHost === 'github'">
        <div
          v-if="!githubApp?.configured"
          class="rounded-lg border border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 px-3 py-2 text-xs text-[var(--lp-warn)]"
        >
          {{ t('workspaces.linkedRepo.connectGithub') }}
          <NuxtLink to="/integrations/github" class="ml-1 underline">
            {{ t('nav.integrations') }}
          </NuxtLink>
        </div>
        <template v-else>
          <GithubInstallationPicker
            v-model="installationId"
            :installations="githubApp.installations"
            :disabled="disabled"
          />
          <GithubRepoPicker
            v-model="fullName"
            :installation-id="installationId"
            :disabled="disabled"
          />
          <GitBranchPicker
            v-model="gitBranch"
            host="github"
            :installation-id="installationId"
            :full-name="fullName"
            :disabled="disabled"
            :label="t('workspaces.linkedRepo.branch')"
          />
          <fieldset class="space-y-2">
            <legend class="lp-label">{{ t('workspaces.linkedRepo.cdMode') }}</legend>
            <label class="flex cursor-pointer items-start gap-2 rounded-lg border border-[var(--lp-line)] px-3 py-2 hover:border-[var(--lp-accent)]/40">
              <input v-model="cdMode" type="radio" value="webhook" class="mt-1" :disabled="disabled">
              <span>
                <span class="block text-sm font-medium">{{ t('workspaces.linkedRepo.modeWebhook') }}</span>
                <span class="block text-xs text-[var(--lp-muted)]">{{ t('workspaces.linkedRepo.modeWebhookBlurb') }}</span>
              </span>
            </label>
            <label class="flex cursor-pointer items-start gap-2 rounded-lg border border-[var(--lp-line)] px-3 py-2 hover:border-[var(--lp-accent)]/40">
              <input v-model="cdMode" type="radio" value="github_actions" class="mt-1" :disabled="disabled">
              <span>
                <span class="block text-sm font-medium">{{ t('workspaces.linkedRepo.modeActions') }}</span>
                <span class="block text-xs text-[var(--lp-muted)]">{{ t('workspaces.linkedRepo.modeActionsBlurb') }}</span>
              </span>
            </label>
          </fieldset>
          <div class="flex flex-wrap gap-2">
            <button
              type="button"
              class="lp-btn-primary text-xs"
              :disabled="disabled || saving || !installationId || !fullName.trim()"
              @click="onAddRepo"
            >
              {{ saving ? t('common.saving') : t('scaffold.repoSource.addRepo') }}
            </button>
          </div>
        </template>
      </template>

      <template v-else>
        <div
          v-if="!gitlabConnected"
          class="rounded-lg border border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 px-3 py-2 text-xs text-[var(--lp-warn)]"
        >
          {{ t('scaffold.repoSource.connectGitlab') }}
          <NuxtLink to="/integrations/gitlab" class="ml-1 underline">
            {{ t('nav.integrations') }}
          </NuxtLink>
        </div>
        <template v-else>
          <GitlabRepoPicker v-model="gitlabPath" :disabled="disabled" />
          <GitBranchPicker
            v-model="gitBranch"
            host="gitlab"
            :project-path="gitlabPath"
            :disabled="disabled"
            :label="t('workspaces.linkedRepo.branch')"
          />
          <p class="text-xs text-[var(--lp-muted)]">
            {{ t('scaffold.repoSource.gitlabTrackBlurb') }}
          </p>
          <div class="flex flex-wrap gap-2">
            <button
              type="button"
              class="lp-btn-primary text-xs"
              :disabled="disabled || saving || !gitlabPath.trim()"
              @click="onAddRepo"
            >
              {{ saving ? t('common.saving') : t('scaffold.repoSource.addRepo') }}
            </button>
          </div>
        </template>
      </template>

      <!-- Linked repositories (multi-repo). The primary repo is the one that deploys. -->
      <div v-if="linkedList.length" class="space-y-1 border-t border-[var(--lp-line)] pt-3">
        <p class="lp-label">
          {{ t('scaffold.repoSource.linkedTitle') }}
          <span v-if="!hasWorkspace" class="text-[var(--lp-accent)]">({{ t('scaffold.repoSource.pendingBadge') }})</span>
        </p>
        <ul class="space-y-1">
          <li
            v-for="(repo, i) in linkedList"
            :key="repo.git_repo_url"
            class="flex items-center justify-between gap-2 rounded-md border px-2 py-1"
            :class="isPrimary(repo, i) ? 'border-[var(--lp-accent)]/50' : 'border-[var(--lp-line)]'"
          >
            <span class="min-w-0 truncate font-mono text-[11px] text-[var(--lp-text)]">
              <span
                class="mr-1 rounded px-1 text-[9px] uppercase"
                :class="providerLabel(repo) === 'gitlab' ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]' : 'bg-[var(--lp-line)] text-[var(--lp-muted)]'"
              >{{ providerLabel(repo) }}</span>
              {{ repoLabel(repo) }} @ {{ repo.git_branch }}
              <span
                v-if="isPrimary(repo, i)"
                class="ml-1 rounded bg-[var(--lp-accent)]/15 px-1 text-[9px] uppercase text-[var(--lp-accent)]"
              >{{ t('scaffold.repoSource.primary') }}</span>
            </span>
            <span class="flex shrink-0 items-center gap-1">
              <button
                v-if="!isPrimary(repo, i)"
                type="button"
                class="lp-btn-ghost px-1.5 text-[10px]"
                :disabled="disabled || saving"
                @click="setPrimary(i)"
              >
                {{ t('scaffold.repoSource.makePrimary') }}
              </button>
              <button
                type="button"
                class="lp-btn-ghost px-1 text-xs text-[var(--lp-danger)]"
                :aria-label="t('workspaces.linkedRepo.unlink')"
                :disabled="disabled || saving"
                @click="removeRepoAt(i)"
              >
                <span class="material-symbols-outlined text-sm">close</span>
              </button>
            </span>
          </li>
        </ul>
        <p class="text-[10px] text-[var(--lp-muted)]">{{ t('scaffold.repoSource.primaryHint') }}</p>
      </div>
    </template>

    <template v-else>
      <p class="text-xs text-[var(--lp-muted)]">
        {{ t('scaffold.repoSource.importBlurb') }}
      </p>
      <button
        type="button"
        class="lp-btn-primary inline-flex items-center gap-2 text-xs"
        :disabled="disabled"
        @click="importOpen = true"
      >
        <span class="material-symbols-outlined text-base">download</span>
        {{ t('scaffold.repoSource.openImport') }}
      </button>
      <ClientOnly>
        <RepoImporterModal
          v-model:open="importOpen"
          :launchpad-project-id="launchpadProjectId"
          @saved="onImportSaved"
        />
      </ClientOnly>
    </template>
  </div>
</template>

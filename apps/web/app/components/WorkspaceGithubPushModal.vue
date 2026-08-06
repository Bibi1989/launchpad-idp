<script setup lang="ts">
import type { GitHubAppStatus, GitlabProjectItem, GitlabStatus } from '~/types/provisioning'
import { isPersonalGithubInstallation } from '~/utils/githubAccount'
import { syncWorkspaceCicdToPlatform } from '~/utils/syncWorkspaceCicd'

const props = defineProps<{
  open: boolean
  workspaceId: string
  workspaceName?: string
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  pushed: [fullName: string]
  error: [message: string]
  converted: [message: string]
}>()

const { t } = useI18n()

const {
  getGithubAppStatus,
  getGitlabStatus,
  listGitlabProjects,
  listWorkspaceFiles,
  readWorkspaceFile,
  writeWorkspaceFile,
  deleteWorkspacePath,
  pushWorkspaceToGithub,
  pushWorkspaceToGitlab,
  createGithubRepo,
  createGitlabRepo,
} = useProvisioning()

const provider = ref<'github' | 'gitlab'>('github')
const githubRepoMode = ref<'create' | 'existing'>('existing')
const gitlabRepoMode = ref<'create' | 'existing'>('existing')
const githubApp = ref<GitHubAppStatus | null>(null)
const gitlabStatus = ref<GitlabStatus | null>(null)
const gitlabProjects = ref<GitlabProjectItem[]>([])
const pushInstallationId = ref<number | null>(null)
const pushRepo = ref('')
const newRepoName = ref('')
const newRepoPrivate = ref(true)
const gitlabProject = ref('')
const pushMessage = ref('chore: update Launchpad workspace files')
const pushing = ref(false)
const loadingStatus = ref(false)
const convertNote = ref<string | null>(null)

function sanitizeRepoName(raw: string): string {
  const cleaned = raw
    .trim()
    .replace(/[^A-Za-z0-9_.-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 100)
  return cleaned || 'app'
}

function defaultRepoName(): string {
  return sanitizeRepoName(props.workspaceName || 'app')
}

const selectedInstallation = computed(() =>
  githubApp.value?.installations.find((item) => item.id === pushInstallationId.value) ?? null,
)

const isPersonalGithubAccount = computed(() =>
  isPersonalGithubInstallation(selectedInstallation.value),
)

async function loadStatus() {
  loadingStatus.value = true
  convertNote.value = null
  try {
    const ghResult = await getGithubAppStatus().catch((err: unknown) => {
      emit('error', err instanceof Error ? err.message : 'Failed to load GitHub status')
      return null
    })
    if (ghResult) {
      githubApp.value = ghResult
      const defaultId =
        ghResult.default_installation_id ?? ghResult.installations[0]?.id ?? null
      pushInstallationId.value = defaultId
    }

    const glResult = await getGitlabStatus().catch((err: unknown) => {
      console.warn('gitlab status failed', err)
      return null
    })
    if (glResult) {
      gitlabStatus.value = glResult
      if (glResult.connected) {
        try {
          gitlabProjects.value = await listGitlabProjects()
        } catch {
          gitlabProjects.value = []
        }
      } else {
        gitlabProjects.value = []
      }
    } else {
      gitlabStatus.value = {
        connected: false,
        oauth_configured: false,
        authorize_url: null,
        base_url: 'https://gitlab.com',
        username: null,
        token_type: null,
        message: 'GitLab unavailable - connect later from Integrations.',
      }
      gitlabProjects.value = []
    }
  } finally {
    loadingStatus.value = false
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      pushMessage.value = 'chore: update Launchpad workspace files'
      convertNote.value = null
      githubRepoMode.value = 'existing'
      gitlabRepoMode.value = 'existing'
      newRepoName.value = defaultRepoName()
      newRepoPrivate.value = true
      pushRepo.value = ''
      gitlabProject.value = ''
      void loadStatus()
    }
  },
)

watch(provider, () => {
  convertNote.value = null
})

watch(
  () => props.workspaceName,
  () => {
    if (props.open && (githubRepoMode.value === 'create' || gitlabRepoMode.value === 'create')) {
      if (!newRepoName.value || newRepoName.value === 'app') {
        newRepoName.value = defaultRepoName()
      }
    }
  },
)

async function doPush() {
  pushing.value = true
  convertNote.value = null
  try {
    const result = await syncWorkspaceCicdToPlatform(
      {
        listWorkspaceFiles,
        readWorkspaceFile,
        writeWorkspaceFile,
        deleteWorkspacePath,
      },
      props.workspaceId,
      provider.value,
      { appName: props.workspaceName || newRepoName.value || 'app' },
    )
    if (result.converted) {
      const msg =
        provider.value === 'github'
          ? 'Converted GitLab CI → GitHub Actions before publish'
          : 'Converted GitHub Actions → GitLab CI before publish'
      convertNote.value = msg
      emit('converted', msg)
    }

    if (provider.value === 'github') {
      if (!pushInstallationId.value) return

      if (githubRepoMode.value === 'create') {
        const name = sanitizeRepoName(newRepoName.value)
        if (!name) return
        const created = await createGithubRepo({
          name,
          description: `Launchpad workspace ${props.workspaceName || name}`,
          private: newRepoPrivate.value,
          installation_id: pushInstallationId.value,
          workspace_id: props.workspaceId,
          set_cloud_secrets: false,
          include_workflow: false,
          include_dockerfiles: false,
        })
        emit('update:open', false)
        emit('pushed', created.full_name)
        return
      }

      if (!pushRepo.value.trim()) return
      const push = await pushWorkspaceToGithub(props.workspaceId, {
        installation_id: pushInstallationId.value,
        existing_full_name: pushRepo.value.trim(),
        commit_message: pushMessage.value,
      })
      emit('update:open', false)
      emit('pushed', push.full_name)
      return
    }

    if (!gitlabStatus.value?.connected) return

    if (gitlabRepoMode.value === 'create') {
      const name = sanitizeRepoName(newRepoName.value)
      if (!name) return
      const created = await createGitlabRepo({
        name,
        description: `Launchpad workspace ${props.workspaceName || name}`,
        private: newRepoPrivate.value,
        workspace_id: props.workspaceId,
        include_ci: false,
      })
      emit('update:open', false)
      emit('pushed', created.path_with_namespace)
      return
    }

    if (!gitlabProject.value.trim()) return
    const push = await pushWorkspaceToGitlab(props.workspaceId, {
      project_path: gitlabProject.value.trim(),
      commit_message: pushMessage.value,
    })
    emit('update:open', false)
    emit('pushed', push.path_with_namespace)
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'Push failed')
  } finally {
    pushing.value = false
  }
}

function close() {
  emit('update:open', false)
}

function useWorkspaceName() {
  newRepoName.value = defaultRepoName()
}

const canPush = computed(() => {
  if (provider.value === 'github') {
    if (!pushInstallationId.value) return false
    if (githubRepoMode.value === 'create') {
      return Boolean(sanitizeRepoName(newRepoName.value))
    }
    return Boolean(pushRepo.value.trim())
  }
  if (!gitlabStatus.value?.connected) return false
  if (gitlabRepoMode.value === 'create') {
    return Boolean(sanitizeRepoName(newRepoName.value))
  }
  return Boolean(gitlabProject.value.trim())
})

const publishLabel = computed(() => {
  if (provider.value === 'github') {
    return githubRepoMode.value === 'create'
      ? t('workspaceIde.push.createPushGithub')
      : t('workspaceIde.push.pushGithub')
  }
  return gitlabRepoMode.value === 'create'
    ? t('workspaceIde.push.createPushGitlab')
    : t('workspaceIde.push.pushGitlab')
})

const showCommitMessage = computed(() => {
  if (provider.value === 'github') return githubRepoMode.value === 'existing'
  return gitlabRepoMode.value === 'existing'
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[90] flex items-center justify-center bg-black/55 p-4"
      @click.self="close"
    >
      <div class="w-full max-w-lg space-y-5 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5 shadow-2xl">
        <header class="space-y-1">
          <h2 class="text-lg font-semibold">{{ t('workspaceIde.push.title') }}</h2>
          <p class="text-sm text-[var(--lp-muted)]">
            {{ t('workspaceIde.push.blurb') }}
          </p>
        </header>

        <div class="flex gap-2">
          <button
            type="button"
            class="rounded-lg px-3 py-1.5 text-sm transition"
            :class="provider === 'github' ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
            @click="provider = 'github'"
          >
            GitHub
          </button>
          <button
            type="button"
            class="rounded-lg px-3 py-1.5 text-sm transition"
            :class="provider === 'gitlab' ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
            @click="provider = 'gitlab'"
          >
            GitLab
          </button>
        </div>

        <p v-if="loadingStatus" class="text-sm text-[var(--lp-muted)]">{{ t('common.loading') }}</p>
        <p v-if="convertNote" class="text-sm text-[var(--lp-ok)]">{{ convertNote }}</p>

        <template v-if="!loadingStatus && provider === 'github'">
          <p v-if="!githubApp?.installations.length" class="text-sm text-[var(--lp-warn)]">
            {{ t('workspaceIde.push.connectGithub') }}
            <NuxtLink to="/integrations/github" class="text-[var(--lp-accent)] hover:underline">{{ t('workspaceIde.push.integrations') }}</NuxtLink>
          </p>
          <template v-else>
            <GithubInstallationPicker
              v-model="pushInstallationId"
              :installations="githubApp.installations"
              :label="t('workspaceIde.push.installation')"
            />

            <div class="flex flex-wrap gap-2">
              <button
                type="button"
                class="rounded-lg px-3 py-1.5 text-sm transition"
                :class="githubRepoMode === 'create' ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
                @click="githubRepoMode = 'create'; newRepoName = defaultRepoName()"
              >
                {{ t('workspaceIde.push.createRepo') }}
              </button>
              <button
                type="button"
                class="rounded-lg px-3 py-1.5 text-sm transition"
                :class="githubRepoMode === 'existing' ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
                @click="githubRepoMode = 'existing'"
              >
                {{ t('workspaceIde.push.existingRepo') }}
              </button>
            </div>

            <template v-if="githubRepoMode === 'create'">
              <p
                v-if="isPersonalGithubAccount"
                class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 px-3 py-2 text-xs leading-5 text-[var(--lp-muted)]"
              >
                {{ t('workspaceIde.push.personalGithubWarning') }}
              </p>
              <label class="block space-y-2">
                <span class="lp-label">{{ t('workspaceIde.push.newRepoName') }}</span>
                <div class="flex gap-2">
                  <input
                    v-model="newRepoName"
                    class="lp-input font-mono text-xs"
                    placeholder="my-service"
                    maxlength="100"
                  >
                  <button
                    type="button"
                    class="lp-btn-ghost shrink-0 text-xs uppercase tracking-wide"
                    @click="useWorkspaceName"
                  >
                    {{ t('workspaceIde.push.useWorkspaceName') }}
                  </button>
                </div>
                <p class="text-[11px] text-[var(--lp-muted)]">
                  {{ t('workspaceIde.push.createsUnder') }}
                  <span class="font-mono text-[var(--lp-text)]">
                    {{ selectedInstallation?.account_login || '…' }}/{{ sanitizeRepoName(newRepoName) || '…' }}
                  </span>
                </p>
              </label>
              <label class="flex items-center gap-2 text-sm text-[var(--lp-muted)]">
                <input v-model="newRepoPrivate" type="checkbox" class="accent-[var(--lp-accent)]">
                {{ t('workspaceIde.push.privateRepo') }}
              </label>
            </template>

            <label v-else class="block space-y-2">
              <span class="lp-label">{{ t('workspaceIde.push.repository') }}</span>
              <GithubRepoPicker
                v-model="pushRepo"
                :installation-id="pushInstallationId"
              />
            </label>
          </template>
        </template>

        <template v-else-if="!loadingStatus">
          <p v-if="!gitlabStatus?.connected" class="text-sm text-[var(--lp-warn)]">
            {{ t('workspaceIde.push.connectGitlab') }}
            <NuxtLink to="/integrations/gitlab" class="text-[var(--lp-accent)] hover:underline">{{ t('workspaceIde.push.integrations') }}</NuxtLink>
          </p>
          <template v-else>
            <div class="flex flex-wrap gap-2">
              <button
                type="button"
                class="rounded-lg px-3 py-1.5 text-sm transition"
                :class="gitlabRepoMode === 'create' ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
                @click="gitlabRepoMode = 'create'; newRepoName = defaultRepoName()"
              >
                {{ t('workspaceIde.push.createProject') }}
              </button>
              <button
                type="button"
                class="rounded-lg px-3 py-1.5 text-sm transition"
                :class="gitlabRepoMode === 'existing' ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
                @click="gitlabRepoMode = 'existing'"
              >
                {{ t('workspaceIde.push.existingProject') }}
              </button>
            </div>

            <template v-if="gitlabRepoMode === 'create'">
              <label class="block space-y-2">
                <span class="lp-label">{{ t('workspaceIde.push.newProjectName') }}</span>
                <div class="flex gap-2">
                  <input
                    v-model="newRepoName"
                    class="lp-input font-mono text-xs"
                    placeholder="my-service"
                    maxlength="100"
                  >
                  <button
                    type="button"
                    class="lp-btn-ghost shrink-0 text-xs uppercase tracking-wide"
                    @click="useWorkspaceName"
                  >
                    {{ t('workspaceIde.push.useWorkspaceName') }}
                  </button>
                </div>
              </label>
              <label class="flex items-center gap-2 text-sm text-[var(--lp-muted)]">
                <input v-model="newRepoPrivate" type="checkbox" class="accent-[var(--lp-accent)]">
                {{ t('workspaceIde.push.privateProject') }}
              </label>
            </template>

            <template v-else>
              <label class="block space-y-2">
                <span class="lp-label">{{ t('workspaceIde.push.project') }}</span>
                <select v-model="gitlabProject" class="lp-input font-mono text-xs">
                  <option value="" disabled>{{ t('workspaceIde.push.selectProject') }}</option>
                  <option
                    v-for="item in gitlabProjects"
                    :key="item.id"
                    :value="item.path_with_namespace"
                  >
                    {{ item.path_with_namespace }}
                  </option>
                </select>
              </label>
              <label class="block space-y-2">
                <span class="lp-label">{{ t('workspaceIde.push.orPath') }}</span>
                <input
                  v-model="gitlabProject"
                  class="lp-input font-mono text-xs"
                  placeholder="group/my-service"
                >
              </label>
            </template>
          </template>
        </template>

        <label v-if="showCommitMessage" class="block space-y-2">
          <span class="lp-label">{{ t('workspaceIde.push.commitMessage') }}</span>
          <input v-model="pushMessage" class="lp-input">
        </label>

        <div class="flex justify-end gap-2">
          <button type="button" class="lp-btn-ghost text-xs uppercase tracking-wide" @click="close">
            {{ t('common.cancel') }}
          </button>
          <button
            type="button"
            class="lp-btn-primary text-xs uppercase tracking-wide"
            :disabled="pushing || !canPush"
            @click="doPush"
          >
            {{ pushing ? t('workspaceIde.push.publishing') : publishLabel }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

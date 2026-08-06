<script setup lang="ts">
import type { GitHubAppStatus, GitHubRepositoryItem } from '~/types/provisioning'
import { isPersonalGithubInstallation } from '~/utils/githubAccount'

const props = withDefaults(
  defineProps<{
    compact?: boolean
    modelInstallationId?: number | null
    modelRepoName?: string
    modelRepoMode?: 'create' | 'existing'
    modelRepoFullName?: string
    showRepoPicker?: boolean
  }>(),
  {
    compact: false,
    modelInstallationId: null,
    modelRepoName: '',
    modelRepoMode: 'create',
    modelRepoFullName: '',
    showRepoPicker: false,
  },
)

const emit = defineEmits<{
  updated: [status: GitHubAppStatus]
  'update:modelInstallationId': [value: number | null]
  'update:modelRepoName': [value: string]
  'update:modelRepoMode': [value: 'create' | 'existing']
  'update:modelRepoFullName': [value: string]
}>()

const { getGithubAppStatus, listGithubRepositories } = useProvisioning()
const { t } = useI18n()
const route = useRoute()

const status = ref<GitHubAppStatus | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const justConnected = ref(false)
const repos = ref<GitHubRepositoryItem[]>([])
const reposLoading = ref(false)

const repoMode = computed({
  get: () => props.modelRepoMode,
  set: (value: 'create' | 'existing') => emit('update:modelRepoMode', value),
})

const selectedFullName = computed({
  get: () => props.modelRepoFullName,
  set: (value: string) => emit('update:modelRepoFullName', value),
})

const connected = computed(
  () => Boolean(status.value?.configured && status.value.installations.length > 0),
)

const selectedInstallation = computed(() => {
  const id = props.modelInstallationId
  if (!id || !status.value) return null
  return status.value.installations.find((item) => item.id === id) ?? null
})

const isPersonalAccount = computed(() =>
  isPersonalGithubInstallation(selectedInstallation.value),
)

async function refresh() {
  loading.value = true
  error.value = null
  try {
    status.value = await getGithubAppStatus()
    emit('updated', status.value)
    if (!props.modelInstallationId) {
      const defaultId = status.value.default_installation_id
      if (defaultId) {
        emit('update:modelInstallationId', defaultId)
      } else if (status.value.installations.length === 1) {
        emit('update:modelInstallationId', status.value.installations[0]!.id)
      }
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load GitHub status'
    status.value = null
  } finally {
    loading.value = false
  }
}

async function loadRepos(installationId: number | null) {
  repos.value = []
  const previousFullName = selectedFullName.value
  selectedFullName.value = ''
  if (!installationId || !props.showRepoPicker) return
  reposLoading.value = true
  try {
    repos.value = await listGithubRepositories(installationId)
    if (previousFullName && repos.value.some((item) => item.full_name === previousFullName)) {
      selectedFullName.value = previousFullName
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load repositories'
  } finally {
    reposLoading.value = false
  }
}

function connectGithub() {
  if (!status.value?.install_url) return
  // Return to integrations page (or current provision) after GitHub authorize.
  const returnTo =
    typeof window !== 'undefined'
      ? `${window.location.origin}/integrations/github`
      : '/integrations/github'
  const url = new URL(status.value.install_url)
  // state is already set by API; keep redirect via App Setup URL
  void returnTo
  window.location.href = url.toString()
}

function selectInstallation(id: number) {
  emit('update:modelInstallationId', id)
}

function onRepoModeCreate() {
  repoMode.value = 'create'
  selectedFullName.value = ''
}

function onRepoModeExisting() {
  repoMode.value = 'existing'
}

function onSelectExistingRepo(fullName: string) {
  selectedFullName.value = fullName
  const repo = repos.value.find((item) => item.full_name === fullName)
  if (repo) {
    emit('update:modelRepoName', repo.name)
    repoMode.value = 'existing'
  }
}

watch(
  () => props.modelInstallationId,
  (id) => {
    void loadRepos(id)
  },
)

onMounted(async () => {
  await refresh()
  const setupAction = route.query.setup_action
  const installationId = route.query.installation_id
  if (setupAction === 'install' || setupAction === 'update' || installationId) {
    justConnected.value = true
    await refresh()
  }
  if (props.modelInstallationId) {
    await loadRepos(props.modelInstallationId)
  }
})
</script>

<template>
  <section
    class="overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/80"
    :class="compact ? 'p-4' : 'p-6'"
  >
    <!-- Not connected - Vercel-style CTA -->
    <div v-if="!connected && !loading" class="space-y-5">
      <div class="flex items-start gap-4">
        <div
          class="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-[#24292f] text-white"
        >
          <svg viewBox="0 0 16 16" class="h-6 w-6 fill-current" aria-hidden="true">
            <path
              d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
            />
          </svg>
        </div>
        <div class="min-w-0 flex-1 space-y-1">
          <h2 class="text-lg font-semibold">{{ t('integrations.connectGithub') }}</h2>
          <p class="text-sm text-[var(--lp-muted)]">
            {{ t('integrations.connectGithubBlurb') }}
          </p>
        </div>
      </div>

      <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
      <p v-else-if="status && !status.configured" class="text-sm text-[var(--lp-warn)]">
        {{ status.message || t('integrations.needAppId') }}
        <NuxtLink to="/docs#github" class="text-[var(--lp-accent)] hover:underline">{{ t('integrations.setupGuide') }}</NuxtLink>
      </p>
      <p
        v-else-if="status?.configured && !status.install_url"
        class="text-sm text-[var(--lp-warn)]"
      >
        {{ status.message }}
        <NuxtLink to="/docs#github" class="text-[var(--lp-accent)] hover:underline">{{ t('integrations.setupGuide') }}</NuxtLink>
      </p>

      <button
        v-if="status?.install_url"
        type="button"
        class="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-[#24292f] px-4 py-3 text-sm font-semibold text-white transition hover:bg-[#32383f] active:scale-[0.99] sm:w-auto sm:min-w-[220px]"
        @click="connectGithub"
      >
        <svg viewBox="0 0 16 16" class="h-4 w-4 fill-current" aria-hidden="true">
          <path
            d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
          />
        </svg>
        {{ t('integrations.connectGithub') }}
      </button>
      <button
        v-else-if="status?.configured"
        type="button"
        class="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-[var(--lp-line)] px-4 py-3 text-sm font-semibold text-[var(--lp-muted)] sm:w-auto sm:min-w-[220px]"
        disabled
      >
        {{ t('integrations.connectGithub') }}
      </button>
    </div>

    <p v-else-if="loading" class="text-sm text-[var(--lp-muted)]">{{ t('integrations.checkingGithub') }}</p>

    <!-- Connected - account switcher + optional repo picker -->
    <div v-else class="space-y-5">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <div
            class="flex h-10 w-10 items-center justify-center rounded-full bg-[#24292f] text-sm font-bold text-white"
          >
            {{ (selectedInstallation?.account_login || 'GH').slice(0, 2).toUpperCase() }}
          </div>
          <div>
            <p class="text-sm font-semibold text-[var(--lp-text)]">
              {{ t('integrations.connectedGithub') }}
              <span class="ml-2 inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-ok)]">
                <span class="h-1.5 w-1.5 rounded-full bg-[var(--lp-ok)]" />
                {{ t('integrations.live') }}
              </span>
            </p>
            <p class="text-xs text-[var(--lp-muted)]">
              {{ selectedInstallation?.account_login || t('integrations.selectAccount') }}
              <span v-if="justConnected"> · {{ t('integrations.justAuthorized') }}</span>
            </p>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <button type="button" class="lp-btn-ghost py-1.5 text-xs" @click="connectGithub">
            {{ status?.installations.length ? t('integrations.addAccount') : t('integrations.reconnect') }}
          </button>
          <button type="button" class="lp-btn-ghost py-1.5 text-xs" :disabled="loading" @click="refresh">
            {{ t('common.refresh') }}
          </button>
        </div>
      </div>

      <GithubInstallationPicker
        v-if="status?.installations.length"
        :model-value="modelInstallationId ?? null"
        :installations="status.installations"
        @update:model-value="(id) => { if (id != null) selectInstallation(id) }"
      />

      <div v-if="showRepoPicker && modelInstallationId" class="space-y-3 border-t border-[var(--lp-line)] pt-4">
        <p
          v-if="isPersonalAccount"
          class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 px-3 py-2 text-xs leading-5 text-[var(--lp-muted)]"
        >
          {{ t('integrations.personalWarning') }}
        </p>
        <div class="flex flex-wrap gap-3">
          <button
            type="button"
            class="rounded-lg px-3 py-1.5 text-sm transition"
            :class="repoMode === 'create' ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
            @click="onRepoModeCreate"
          >
            {{ t('integrations.createRepo') }}
          </button>
          <button
            type="button"
            class="rounded-lg px-3 py-1.5 text-sm transition"
            :class="repoMode === 'existing' ? 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)]' : 'text-[var(--lp-muted)]'"
            :disabled="!repos.length && !reposLoading"
            @click="onRepoModeExisting"
          >
            {{ t('integrations.importGitRepo') }}
          </button>
        </div>

        <label v-if="repoMode === 'create'" class="block space-y-2">
          <span class="lp-label">{{ t('integrations.newRepoName') }}</span>
          <input
            class="lp-input"
            :value="modelRepoName"
            placeholder="launchpad-demo"
            @input="emit('update:modelRepoName', ($event.target as HTMLInputElement).value)"
          >
        </label>

        <div v-else class="block space-y-2">
          <span class="lp-label">{{ t('integrations.selectRepo') }}</span>
          <GithubRepoPicker
            v-model="selectedFullName"
            :installation-id="modelInstallationId"
            :disabled="reposLoading"
            @select-repo="(repo) => emit('update:modelRepoName', repo.name)"
          />
          <p v-if="!reposLoading && !repos.length" class="text-xs text-[var(--lp-muted)]">
            {{ t('integrations.noReposVisible') }}
          </p>
        </div>
      </div>

      <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
    </div>
  </section>
</template>

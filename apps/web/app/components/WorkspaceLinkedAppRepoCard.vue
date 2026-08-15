<script setup lang="ts">
import type {
  GitHubAppStatus,
  WorkspaceCdMode,
  WorkspaceLinkedAppRepoResponse,
} from '~/types/provisioning'
import { toastError } from '~/composables/useToast'

const props = defineProps<{
  workspaceId: string
}>()

const { t } = useI18n()
const toast = useToast()
const {
  getGithubAppStatus,
  getLinkedAppRepo,
  setLinkedAppRepo,
} = useProvisioning()

const loading = ref(true)
const saving = ref(false)
const status = ref<WorkspaceLinkedAppRepoResponse | null>(null)
const githubApp = ref<GitHubAppStatus | null>(null)
const installationId = ref<number | null>(null)
const fullName = ref('')
const gitBranch = ref('main')
const cdMode = ref<WorkspaceCdMode>('webhook')
const formError = ref<string | null>(null)

const linked = computed(() => status.value?.linked ?? null)

async function load() {
  loading.value = true
  formError.value = null
  try {
    const [linkRes, gh] = await Promise.all([
      getLinkedAppRepo(props.workspaceId),
      getGithubAppStatus().catch(() => null),
    ])
    status.value = linkRes
    githubApp.value = gh
    if (linkRes.linked) {
      installationId.value = linkRes.linked.installation_id
      fullName.value = linkRes.linked.full_name
      gitBranch.value = linkRes.linked.git_branch
      cdMode.value = linkRes.linked.cd_mode
    } else if (gh) {
      installationId.value =
        gh.default_installation_id ?? gh.installations[0]?.id ?? null
    }
  } catch (err) {
    formError.value = toastError(err, t('workspaces.linkedRepo.loadFailed'))
  } finally {
    loading.value = false
  }
}

async function onSave() {
  if (saving.value) return
  if (!installationId.value || !fullName.value.trim()) {
    formError.value = t('workspaces.linkedRepo.repoRequired')
    return
  }
  saving.value = true
  formError.value = null
  try {
    status.value = await setLinkedAppRepo(props.workspaceId, {
      installation_id: installationId.value,
      full_name: fullName.value.trim(),
      git_branch: gitBranch.value.trim() || 'main',
      cd_mode: cdMode.value,
    })
    toast.success(t('workspaces.linkedRepo.saved'), status.value.message)
  } catch (err) {
    formError.value = toastError(err, t('workspaces.linkedRepo.saveFailed'))
    toast.error(t('workspaces.linkedRepo.saveFailed'), formError.value)
  } finally {
    saving.value = false
  }
}

async function onUnlink() {
  if (saving.value) return
  saving.value = true
  formError.value = null
  try {
    status.value = await setLinkedAppRepo(props.workspaceId, { clear: true })
    fullName.value = ''
    gitBranch.value = 'main'
    cdMode.value = 'webhook'
    toast.success(t('workspaces.linkedRepo.unlinked'), status.value.message)
  } catch (err) {
    formError.value = toastError(err, t('workspaces.linkedRepo.saveFailed'))
  } finally {
    saving.value = false
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
  <section class="lp-glass space-y-4 rounded-xl p-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0 space-y-1">
        <h2 class="text-base font-semibold tracking-tight">
          {{ t('workspaces.linkedRepo.title') }}
        </h2>
        <p class="text-sm text-[var(--lp-muted)]">
          {{ t('workspaces.linkedRepo.blurb') }}
        </p>
      </div>
      <span
        v-if="linked"
        class="rounded border border-[var(--lp-ok)]/40 bg-[var(--lp-ok)]/10 px-2 py-1 font-mono text-[10px] uppercase text-[var(--lp-ok)]"
      >
        {{ linked.cd_mode === 'github_actions' ? t('workspaces.linkedRepo.modeActions') : t('workspaces.linkedRepo.modeWebhook') }}
      </span>
    </div>

    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">{{ t('common.loading') }}</p>
    <p v-else-if="formError" class="text-sm text-[var(--lp-danger)]">{{ formError }}</p>

    <template v-else>
      <div v-if="!githubApp?.configured" class="rounded-lg border border-[var(--lp-warn)]/40 bg-[var(--lp-warn)]/10 px-3 py-2 text-sm text-[var(--lp-warn)]">
        {{ t('workspaces.linkedRepo.connectGithub') }}
        <NuxtLink to="/integrations/github" class="ml-1 underline">
          {{ t('nav.integrations') }}
        </NuxtLink>
      </div>

      <template v-else>
        <GithubInstallationPicker
          v-model="installationId"
          :installations="githubApp.installations"
        />
        <GithubRepoPicker
          v-model="fullName"
          :installation-id="installationId"
        />
        <GitBranchPicker
          v-model="gitBranch"
          host="github"
          :installation-id="installationId"
          :full-name="fullName"
          :label="t('workspaces.linkedRepo.branch')"
        />

        <fieldset class="space-y-2">
          <legend class="lp-label">{{ t('workspaces.linkedRepo.cdMode') }}</legend>
          <label class="flex cursor-pointer items-start gap-2 rounded-lg border border-[var(--lp-line)] px-3 py-2 hover:border-[var(--lp-accent)]/40">
            <input v-model="cdMode" type="radio" value="webhook" class="mt-1">
            <span>
              <span class="block text-sm font-medium">{{ t('workspaces.linkedRepo.modeWebhook') }}</span>
              <span class="block text-xs text-[var(--lp-muted)]">{{ t('workspaces.linkedRepo.modeWebhookBlurb') }}</span>
            </span>
          </label>
          <label class="flex cursor-pointer items-start gap-2 rounded-lg border border-[var(--lp-line)] px-3 py-2 hover:border-[var(--lp-accent)]/40">
            <input v-model="cdMode" type="radio" value="github_actions" class="mt-1">
            <span>
              <span class="block text-sm font-medium">{{ t('workspaces.linkedRepo.modeActions') }}</span>
              <span class="block text-xs text-[var(--lp-muted)]">{{ t('workspaces.linkedRepo.modeActionsBlurb') }}</span>
            </span>
          </label>
        </fieldset>

        <div
          v-if="status"
          class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 px-3 py-2 text-xs text-[var(--lp-muted)]"
        >
          <p>{{ status.message }}</p>
          <p v-if="cdMode === 'webhook' || linked?.cd_mode === 'webhook'" class="mt-1 font-mono">
            {{ status.control_plane_url }}{{ status.webhook_path }}
            <span
              :class="status.webhook_configured ? 'text-[var(--lp-ok)]' : 'text-[var(--lp-warn)]'"
            >
              · {{ status.webhook_configured ? t('workspaces.linkedRepo.webhookOk') : t('workspaces.linkedRepo.webhookMissing') }}
            </span>
          </p>
          <p v-if="linked?.workflow_path" class="mt-1 font-mono">
            {{ t('workspaces.linkedRepo.workflow') }}: {{ linked.workflow_path }}
          </p>
        </div>

        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            class="lp-btn-primary"
            :disabled="saving || !installationId || !fullName.trim()"
            @click="onSave"
          >
            {{ saving ? t('common.saving') : t('workspaces.linkedRepo.save') }}
          </button>
          <button
            v-if="linked"
            type="button"
            class="lp-btn-ghost text-[var(--lp-danger)]"
            :disabled="saving"
            @click="onUnlink"
          >
            {{ t('workspaces.linkedRepo.unlink') }}
          </button>
        </div>
      </template>
    </template>
  </section>
</template>

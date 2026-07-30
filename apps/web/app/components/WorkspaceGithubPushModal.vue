<script setup lang="ts">
import type { GitHubAppStatus } from '~/types/provisioning'

const props = defineProps<{
  open: boolean
  workspaceId: string
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  pushed: [fullName: string]
  error: [message: string]
}>()

const { getGithubAppStatus, pushWorkspaceToGithub } = useProvisioning()

const githubApp = ref<GitHubAppStatus | null>(null)
const pushInstallationId = ref<number | null>(null)
const pushRepo = ref('')
const pushMessage = ref('chore: update Launchpad workspace files')
const pushing = ref(false)
const loadingStatus = ref(false)

async function loadStatus() {
  loadingStatus.value = true
  try {
    githubApp.value = await getGithubAppStatus()
    const defaultId =
      githubApp.value.default_installation_id ?? githubApp.value.installations[0]?.id ?? null
    pushInstallationId.value = defaultId
    if (!pushRepo.value && githubApp.value.installations.length) {
      // Leave repo empty until the user searches/selects via GithubRepoPicker.
      pushRepo.value = ''
    }
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'GitHub status failed')
  } finally {
    loadingStatus.value = false
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      pushMessage.value = 'chore: update Launchpad workspace files'
      void loadStatus()
    }
  },
)

async function doPush() {
  if (!pushInstallationId.value || !pushRepo.value.trim()) return
  pushing.value = true
  try {
    const result = await pushWorkspaceToGithub(props.workspaceId, {
      installation_id: pushInstallationId.value,
      existing_full_name: pushRepo.value.trim(),
      commit_message: pushMessage.value,
    })
    emit('update:open', false)
    emit('pushed', result.full_name)
  } catch (err) {
    emit('error', err instanceof Error ? err.message : 'Push failed')
  } finally {
    pushing.value = false
  }
}

function close() {
  emit('update:open', false)
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[90] flex items-center justify-center bg-black/55 p-4"
      @click.self="close"
    >
      <div class="w-full max-w-md space-y-4 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5 shadow-2xl">
        <h3 class="text-base font-semibold text-[var(--lp-text)]">Push to GitHub</h3>
        <p v-if="loadingStatus" class="text-[12px] text-[var(--lp-muted)]">
          Loading GitHub installations…
        </p>
        <p v-else-if="githubApp && !githubApp.configured" class="text-[12px] text-[var(--lp-danger)]">
          {{ githubApp.message }}
        </p>
        <label class="block space-y-1">
          <span class="text-[11px] uppercase tracking-wide text-[var(--lp-muted)]">Installation</span>
          <select
            v-model.number="pushInstallationId"
            class="lp-input w-full"
            :disabled="!githubApp?.installations?.length"
          >
            <option
              v-for="inst in githubApp?.installations || []"
              :key="inst.id"
              :value="inst.id"
            >
              {{ inst.account_login }} ({{ inst.account_type }})
            </option>
          </select>
        </label>
        <div class="block space-y-1">
          <span class="text-[11px] uppercase tracking-wide text-[var(--lp-muted)]">Repository</span>
          <GithubRepoPicker
            v-model="pushRepo"
            :installation-id="pushInstallationId"
            :disabled="!pushInstallationId"
          />
        </div>
        <label class="block space-y-1">
          <span class="text-[11px] uppercase tracking-wide text-[var(--lp-muted)]">Commit message</span>
          <input
            v-model="pushMessage"
            class="lp-input w-full"
          >
        </label>
        <div class="flex justify-end gap-2">
          <button
            type="button"
            class="lp-btn-ghost px-3 py-1.5 text-[12px]"
            @click="close"
          >
            Cancel
          </button>
          <button
            type="button"
            class="lp-btn-primary px-3 py-1.5 text-[12px] disabled:opacity-40"
            :disabled="pushing || !pushRepo.trim() || !pushInstallationId"
            @click="doPush"
          >
            {{ pushing ? 'Pushing…' : 'Push changes' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

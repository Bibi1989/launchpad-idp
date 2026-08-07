<script setup lang="ts">
import type { GitlabStatus } from '~/types/provisioning'

const emit = defineEmits<{
  updated: [status: GitlabStatus]
}>()

const {
  getGitlabStatus,
  connectGitlabPat,
  completeGitlabOAuth,
  disconnectGitlab,
} = useProvisioning()
const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const toast = useToast()

const status = ref<GitlabStatus | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref<string | null>(null)
const patToken = ref('')
const baseUrl = ref('')
const showPatForm = ref(false)

async function refresh() {
  loading.value = true
  error.value = null
  try {
    status.value = await getGitlabStatus()
    if (!baseUrl.value) baseUrl.value = status.value.base_url
    emit('updated', status.value)
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('integrations.gitlabStatusFailed')
    status.value = null
  } finally {
    loading.value = false
  }
}

async function connectOAuth() {
  if (!status.value?.authorize_url) return
  window.location.href = status.value.authorize_url
}

async function connectPat() {
  if (!patToken.value.trim() || saving.value) return
  saving.value = true
  error.value = null
  try {
    status.value = await connectGitlabPat(
      patToken.value.trim(),
      baseUrl.value.trim() || undefined,
    )
    patToken.value = ''
    showPatForm.value = false
    toast.success(t('integrations.gitlabConnectedToast'), status.value.username || undefined)
    emit('updated', status.value)
  } catch (err) {
    const message = err instanceof Error ? err.message : t('integrations.gitlabPatFailed')
    error.value = message
    toast.error(t('integrations.gitlabConnectFailed'), message)
  } finally {
    saving.value = false
  }
}

async function disconnect() {
  saving.value = true
  error.value = null
  try {
    await disconnectGitlab()
    toast.success(t('integrations.gitlabDisconnectedToast'))
    await refresh()
  } catch (err) {
    const message = err instanceof Error ? err.message : t('integrations.gitlabDisconnectFailed')
    error.value = message
    toast.error(t('integrations.gitlabDisconnectFailed'), message)
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await refresh()
  const code = typeof route.query.code === 'string' ? route.query.code : null
  const state = typeof route.query.state === 'string' ? route.query.state : null
  if (code && state) {
    saving.value = true
    try {
      status.value = await completeGitlabOAuth(code, state)
      toast.success(t('integrations.gitlabConnectedToast'), status.value.username || undefined)
      emit('updated', status.value)
      await router.replace({ path: '/integrations/gitlab', query: {} })
    } catch (err) {
      const message = err instanceof Error ? err.message : t('integrations.gitlabOauthFailed')
      error.value = message
      toast.error(t('integrations.gitlabConnectFailed'), message)
    } finally {
      saving.value = false
    }
  }
})
</script>

<template>
  <section class="overflow-hidden rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/80 p-6">
    <div v-if="loading" class="text-sm text-[var(--lp-muted)]">{{ t('integrations.checkingGitlab') }}</div>

    <div v-else-if="!status?.connected" class="space-y-5">
      <div class="flex items-start gap-4">
        <div
          class="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-[#FC6D26] text-white"
        >
          <span class="material-symbols-outlined text-2xl">code</span>
        </div>
        <div class="min-w-0 flex-1 space-y-1">
          <h2 class="text-lg font-semibold">{{ t('integrations.connectGitlab') }}</h2>
          <p class="text-sm text-[var(--lp-muted)]">
            {{ t('integrations.gitlabConnectBlurb') }}
          </p>
        </div>
      </div>

      <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
      <p v-else class="text-sm text-[var(--lp-muted)]">{{ status?.message }}</p>

      <div class="flex flex-wrap gap-2">
        <button
          v-if="status?.authorize_url"
          type="button"
          class="inline-flex items-center justify-center gap-2 rounded-lg bg-[#FC6D26] px-4 py-3 text-sm font-semibold text-white transition hover:bg-[#e24329]"
          :disabled="saving"
          @click="connectOAuth"
        >
          {{ t('integrations.connectOAuth') }}
        </button>
        <button
          type="button"
          class="lp-btn-ghost text-xs uppercase tracking-wide"
          @click="showPatForm = !showPatForm"
        >
          {{ showPatForm ? t('integrations.hidePatForm') : t('integrations.usePat') }}
        </button>
      </div>

      <div v-if="showPatForm" class="space-y-3 rounded-xl border border-[var(--lp-line)] p-4">
        <label class="block space-y-2">
          <span class="lp-label">{{ t('integrations.gitlabBaseUrl') }}</span>
          <input v-model="baseUrl" class="lp-input" placeholder="https://gitlab.com">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('integrations.patToken') }}</span>
          <input
            v-model="patToken"
            class="lp-input"
            type="password"
            autocomplete="off"
            placeholder="glpat-…"
          >
          <span class="block text-xs text-[var(--lp-muted)]">
            {{ t('integrations.patScopes') }}
          </span>
        </label>
        <button
          type="button"
          class="lp-btn-primary text-xs uppercase tracking-wide"
          :disabled="saving || patToken.trim().length < 8"
          @click="connectPat"
        >
          {{ saving ? t('integrations.connecting') : t('integrations.saveToken') }}
        </button>
      </div>
    </div>

    <div v-else class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <div
            class="flex h-10 w-10 items-center justify-center rounded-full bg-[#FC6D26] text-sm font-bold text-white"
          >
            {{ (status.username || 'GL').slice(0, 2).toUpperCase() }}
          </div>
          <div>
            <p class="text-sm font-semibold">
              {{ t('common.connected') }} GitLab
              <span class="ml-2 font-mono text-[10px] uppercase tracking-wide text-[var(--lp-ok)]">{{ t('integrations.live') }}</span>
            </p>
            <p class="text-xs text-[var(--lp-muted)]">
              {{ status.username }} · {{ status.base_url }} · {{ status.token_type }}
            </p>
          </div>
        </div>
        <div class="flex gap-2">
          <button type="button" class="lp-btn-ghost py-1.5 text-xs" :disabled="loading" @click="refresh">
            {{ t('common.refresh') }}
          </button>
          <button
            type="button"
            class="px-3 py-1.5 text-xs uppercase tracking-wide text-[var(--lp-danger)]"
            :disabled="saving"
            @click="disconnect"
          >
            {{ t('integrations.disconnectGitlab') }}
          </button>
        </div>
      </div>
      <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
    </div>
  </section>
</template>

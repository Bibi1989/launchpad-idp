<script setup lang="ts">
import type { KindClusterStatus } from '~/types/environment'
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'
import type { CloudOAuthCapabilities } from '~/composables/useUserCloudCredentials'
import { emptyCloudCredentials } from '~/utils/cloudValidation'

const { user } = useAuth()
const { t } = useI18n()
const { getStatus, save, clearAll, oauthCapabilities, connectWithBrowser } = useUserCloudCredentials()
const { getKindStatus, ensureKindCluster, deleteKindCluster } = useEnvironments()

const status = ref<UserCloudCredentialsStatus | null>(null)
const loading = ref(true)
const saving = ref(false)
const clearing = ref(false)
const connecting = ref(false)
const errorMessage = ref<string | null>(null)
const successMessage = ref<string | null>(null)
const activeProvider = ref<'gcp' | 'aws' | 'azure' | 'cloudflare'>('gcp')
// Parent-owned reactive object; CloudCredentialsFields edits fields in place.
// Do not use defineModel/v-model here (nested ref + sync watches caused prod "2").
const credentials = reactive(emptyCloudCredentials())
const caps = ref<CloudOAuthCapabilities | null>(null)
const credentialsFormError = ref<string | null>(null)
const credentialsFormKey = ref(0)

const awsStartUrl = ref('')
const awsRegion = ref('us-east-1')
const awsAccountId = ref('')
const awsRoleName = ref('')
const azureTenantId = ref('common')
const azureSubscriptionId = ref('')

const kindStatus = ref<KindClusterStatus | null>(null)
const kindLoading = ref(false)
const kindCreating = ref(false)
const kindDeleting = ref(false)
const kindError = ref<string | null>(null)
const kindSuccess = ref<string | null>(null)
const clusterName = ref('launchpad')

function applyStatusPreferences(credStatus: UserCloudCredentialsStatus) {
  if (credStatus.gcp_region) credentials.gcp_region = credStatus.gcp_region
  if (credStatus.aws_region) credentials.aws_region = credStatus.aws_region
  if (credStatus.azure_location) credentials.azure_location = credStatus.azure_location
  if (credStatus.gcp_project_id && !(credentials.gcp_project_id || '').trim()) {
    credentials.gcp_project_id = credStatus.gcp_project_id
  }
}

function resetCredentialsForm() {
  Object.assign(credentials, emptyCloudCredentials())
}

/** Show project id for Connect / WIF; hide when SA JSON embeds project_id. */
const showGcpProjectId = computed(() => {
  const raw = (credentials.gcp_sa_key_json || '').trim()
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as { project_id?: unknown }
      if (typeof parsed.project_id === 'string' && parsed.project_id.trim()) return false
    } catch {
      /* keep showing until JSON is valid */
    }
  }
  if (status.value?.has_gcp_sa && !raw) return false
  return true
})

async function refresh() {
  loading.value = true
  errorMessage.value = null
  try {
    const [credStatus, oauthCaps] = await Promise.all([getStatus(), oauthCapabilities()])
    status.value = credStatus
    caps.value = oauthCaps
    applyStatusPreferences(credStatus)
    if (credStatus.vault_unreadable) {
      errorMessage.value = t('settings.errors.vaultUnreadable')
    }
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : t('settings.errors.load')
  } finally {
    loading.value = false
  }
}

async function refreshKind() {
  kindLoading.value = true
  kindError.value = null
  try {
    kindStatus.value = await getKindStatus()
    if (!clusterName.value.trim() || clusterName.value === 'launchpad') {
      clusterName.value = kindStatus.value.cluster || 'launchpad'
    }
  } catch (err) {
    kindError.value = err instanceof Error ? err.message : t('settings.errors.kindLoad')
  } finally {
    kindLoading.value = false
  }
}

onMounted(() => {
  void refresh()
  void refreshKind()
})

function onCredentialsFormError(err: unknown) {
  console.error('[launchpad] CloudCredentialsFields failed', err)
  // vue-i18n message-compiler uses numeric SyntaxError codes in production
  // (e.g. "2" for bad linked/@ syntax). Vue runtime info "2" is unrelated (watcher getter).
  if (err instanceof SyntaxError) {
    credentialsFormError.value =
      'Credentials form crashed (i18n message parse). Locale text may contain unescaped @ or { }.'
    return
  }
  const raw = err instanceof Error ? err.message : String(err ?? '')
  const vueInfo: Record<string, string> = {
    '2': 'watcher getter',
    '1': 'render function',
    '0': 'setup function',
  }
  credentialsFormError.value = vueInfo[raw]
    ? `Credentials form crashed (${vueInfo[raw]}). Try Refresh, or check the browser console.`
    : (raw || t('settings.errors.load'))
}

function reloadCredentialsForm(clearBoundary?: () => void) {
  credentialsFormError.value = null
  credentialsFormKey.value += 1
  clearBoundary?.()
  void refresh()
}

async function onSave() {
  saving.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    status.value = await save({ ...credentials })
    resetCredentialsForm()
    applyStatusPreferences(status.value)
    successMessage.value = t('settings.credentialsSaved')
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : t('settings.errors.save')
  } finally {
    saving.value = false
  }
}

async function onClearProvider() {
  if (!providers.value.find((p) => p.id === activeProvider.value)?.has()) return
  const ok = window.confirm(
    t('settings.removeKeysConfirm', { provider: activeProvider.value.toUpperCase() }),
  )
  if (!ok) return
  saving.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    status.value = await save(emptyCloudCredentials(), {
      clear_gcp: activeProvider.value === 'gcp',
      clear_aws: activeProvider.value === 'aws',
      clear_azure: activeProvider.value === 'azure',
      clear_cloudflare: activeProvider.value === 'cloudflare',
    })
    resetCredentialsForm()
    applyStatusPreferences(status.value)
    successMessage.value = t('settings.clearedProvider', { provider: activeProvider.value.toUpperCase() })
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : t('settings.errors.clear')
  } finally {
    saving.value = false
  }
}

async function onClearAll() {
  clearing.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    status.value = await clearAll()
    successMessage.value = t('settings.clearedAll')
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : t('settings.errors.clear')
  } finally {
    clearing.value = false
  }
}

const canConnect = computed(() => {
  if (activeProvider.value === 'gcp') return Boolean(caps.value?.gcp)
  if (activeProvider.value === 'aws') return Boolean(caps.value?.aws)
  if (activeProvider.value === 'azure') return Boolean(caps.value?.azure)
  return false
})

async function onConnectBrowser() {
  if (activeProvider.value === 'cloudflare' || !canConnect.value) return
  connecting.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    const result = await connectWithBrowser({
      provider: activeProvider.value,
      aws_start_url: awsStartUrl.value || undefined,
      aws_region: awsRegion.value || undefined,
      aws_account_id: awsAccountId.value || undefined,
      aws_role_name: awsRoleName.value || undefined,
      azure_tenant_id: azureTenantId.value || undefined,
      azure_subscription_id: azureSubscriptionId.value || undefined,
    })
    if (result.status === 'succeeded') {
      status.value = await getStatus()
      applyStatusPreferences(status.value)
      successMessage.value = result.message || t('settings.oauthConnected')
    } else {
      errorMessage.value = result.message || t('settings.errors.oauthFailed')
    }
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : t('settings.errors.oauthFailed')
  } finally {
    connecting.value = false
  }
}

async function onKindCreate() {
  const name = clusterName.value.trim()
  if (!name) return
  kindCreating.value = true
  kindError.value = null
  kindSuccess.value = null
  try {
    const result = await ensureKindCluster(name)
    kindSuccess.value = result.message || t('settings.localKubernetesCreated')
    await refreshKind()
  } catch (err) {
    kindError.value = err instanceof Error ? err.message : t('settings.errors.kindUp')
  } finally {
    kindCreating.value = false
  }
}

async function onKindDelete() {
  const name = clusterName.value.trim()
  if (!name) return
  if (!window.confirm(t('settings.localKubernetesConfirmDelete', { name }))) return
  kindDeleting.value = true
  kindError.value = null
  kindSuccess.value = null
  try {
    const result = await deleteKindCluster(name)
    kindSuccess.value = result.message || t('settings.localKubernetesDeleted')
    await refreshKind()
  } catch (err) {
    kindError.value = err instanceof Error ? err.message : t('settings.errors.kindDown')
  } finally {
    kindDeleting.value = false
  }
}

const providers = computed(() => [
  { id: 'gcp' as const, label: t('launch.targets.gcp'), has: () => status.value?.has_gcp, hint: () => status.value?.gcp_label },
  { id: 'aws' as const, label: t('launch.targets.aws'), has: () => status.value?.has_aws, hint: () => status.value?.aws_label },
  { id: 'azure' as const, label: t('launch.targets.azure'), has: () => status.value?.has_azure, hint: () => status.value?.azure_label },
  { id: 'cloudflare' as const, label: t('launch.targets.cloudflare'), has: () => status.value?.has_cloudflare, hint: () => status.value?.cloudflare_label },
])

const kindBusy = computed(() => kindCreating.value || kindDeleting.value || kindLoading.value)
</script>

<template>
  <div class="w-full animate-fade-up space-y-8">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">{{ t('settings.preferences') }}</p>
      <h1 class="text-3xl font-semibold tracking-tight">{{ t('settings.title') }}</h1>
      <p class="text-sm text-[var(--lp-muted)]">
        {{ t('settings.profileBlurb', { email: user?.email ?? '' }) }}
      </p>
    </header>

    <section class="space-y-4 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-6">
      <h2 class="text-lg font-semibold">{{ t('settings.appearance') }}</h2>
      <p class="text-sm text-[var(--lp-muted)]">{{ t('settings.appearanceBlurb') }}</p>
      <PreferenceControls />
    </section>

    <section class="space-y-4 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-6">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="text-lg font-semibold">{{ t('settings.localKubernetes') }}</h2>
        <button
          type="button"
          class="lp-btn-ghost text-xs uppercase tracking-wide"
          :disabled="kindBusy"
          @click="refreshKind"
        >
          {{ t('settings.localKubernetesRefresh') }}
        </button>
      </div>
      <p class="text-sm text-[var(--lp-muted)]">{{ t('settings.localKubernetesBlurb') }}</p>

      <dl
        v-if="kindStatus"
        class="grid gap-2 text-sm sm:grid-cols-2"
      >
        <div>
          <dt class="text-xs uppercase tracking-wide text-[var(--lp-muted)]">{{ t('settings.localKubernetesStatus') }}</dt>
          <dd class="font-mono text-[var(--lp-text)]">{{ kindStatus.status }}</dd>
        </div>
        <div>
          <dt class="text-xs uppercase tracking-wide text-[var(--lp-muted)]">{{ t('settings.localKubernetesEngine') }}</dt>
          <dd class="font-mono text-[var(--lp-text)]">{{ kindStatus.engine || 'k3s' }} / {{ kindStatus.tool || 'k3d' }}</dd>
        </div>
        <div class="sm:col-span-2">
          <dt class="text-xs uppercase tracking-wide text-[var(--lp-muted)]">{{ t('settings.localKubernetesContext') }}</dt>
          <dd class="font-mono text-[var(--lp-text)]">{{ kindStatus.context || '-' }}</dd>
        </div>
        <p class="sm:col-span-2 text-xs text-[var(--lp-muted)]">{{ kindStatus.message }}</p>
      </dl>

      <label class="block max-w-md">
        <span class="lp-label mb-1 block">{{ t('settings.localKubernetesName') }}</span>
        <input
          v-model="clusterName"
          class="lp-input w-full"
          autocomplete="off"
          spellcheck="false"
          :disabled="kindBusy"
        >
        <span class="mt-1 block text-xs text-[var(--lp-muted)]">{{ t('settings.localKubernetesNameHint') }}</span>
      </label>

      <div class="flex flex-wrap gap-3 pt-1">
        <button
          type="button"
          class="lp-btn-primary"
          :disabled="kindBusy || !clusterName.trim()"
          @click="onKindCreate"
        >
          {{ kindCreating ? t('settings.localKubernetesCreating') : t('settings.localKubernetesCreate') }}
        </button>
        <button
          type="button"
          class="lp-btn-ghost text-xs uppercase tracking-wide text-[var(--lp-danger)]"
          :disabled="kindBusy || !clusterName.trim()"
          @click="onKindDelete"
        >
          {{ kindDeleting ? t('settings.localKubernetesDeleting') : t('settings.localKubernetesDelete') }}
        </button>
      </div>

      <p v-if="kindError" class="text-sm text-[var(--lp-danger)]">{{ kindError }}</p>
      <p v-if="kindSuccess" class="text-sm text-[var(--lp-ok)]">{{ kindSuccess }}</p>
    </section>

    <section class="space-y-4 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-6">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="text-lg font-semibold">{{ t('settings.cloudCredentials') }}</h2>
        <button
          type="button"
          class="lp-btn-ghost text-xs uppercase tracking-wide text-[var(--lp-danger)]"
          :disabled="clearing || loading"
          @click="onClearAll"
        >
          {{ clearing ? t('settings.clearing') : t('settings.clearAll') }}
        </button>
      </div>
      <p class="text-sm text-[var(--lp-muted)]">
        {{ t('settings.credentialsBlurbExtended') }}
      </p>

      <div class="flex flex-wrap gap-2">
        <button
          v-for="p in providers"
          :key="p.id"
          type="button"
          class="rounded-lg border px-3 py-1.5 text-sm transition"
          :class="
            activeProvider === p.id
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
              : 'border-[var(--lp-line)] hover:bg-[var(--lp-panel-2)]'
          "
          @click="activeProvider = p.id"
        >
          {{ p.label }}
          <span
            v-if="p.has()"
            class="ml-1 font-mono text-[10px] text-[var(--lp-ok)]"
          >●</span>
        </button>
      </div>

      <p v-if="status && providers.find((p) => p.id === activeProvider)?.has()" class="flex flex-wrap items-center gap-3 text-xs text-[var(--lp-muted)]">
        <span>
          {{ t('settings.stored') }}
          <span class="font-mono text-[var(--lp-accent)]">
            {{ providers.find((p) => p.id === activeProvider)?.hint() || t('settings.configured') }}
          </span>
          - {{ t('settings.replaceHint') }}
        </span>
        <button
          type="button"
          class="lp-btn-ghost text-xs uppercase tracking-wide text-[var(--lp-danger)]"
          :disabled="saving || loading"
          @click="onClearProvider"
        >
          {{ t('settings.clearProviderNamed', { provider: activeProvider.toUpperCase() }) }}
        </button>
      </p>

      <div
        v-if="activeProvider !== 'cloudflare'"
        class="space-y-3 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/40 p-4"
      >
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p class="text-sm font-medium text-[var(--lp-text)]">{{ t('settings.oauthConnectTitle') }}</p>
            <p class="text-xs text-[var(--lp-muted)]">{{ t('settings.oauthConnectHint') }}</p>
          </div>
          <button
            type="button"
            class="lp-btn-primary"
            :disabled="connecting || !canConnect"
            @click="onConnectBrowser"
          >
            {{ connecting ? t('settings.oauthConnecting') : t('settings.oauthConnect', { provider: activeProvider.toUpperCase() }) }}
          </button>
        </div>
        <p v-if="!canConnect && activeProvider === 'gcp'" class="text-xs text-[var(--lp-muted)]">
          {{ t('settings.oauthNeedGcpClient') }}
        </p>
        <p v-if="!canConnect && activeProvider === 'azure'" class="text-xs text-[var(--lp-muted)]">
          {{ t('settings.oauthNeedAzureClient') }}
        </p>
        <div v-if="activeProvider === 'aws'" class="grid gap-3 sm:grid-cols-2">
          <label class="block space-y-1 sm:col-span-2">
            <span class="lp-label">{{ t('settings.awsStartUrl') }}</span>
            <input v-model="awsStartUrl" class="lp-input font-mono text-xs" placeholder="https://d-xxxxxxxxxx.awsapps.com/start" autocomplete="off">
          </label>
          <label class="block space-y-1">
            <span class="lp-label">{{ t('settings.awsRegion') }}</span>
            <input v-model="awsRegion" class="lp-input font-mono text-xs" placeholder="us-east-1" autocomplete="off">
          </label>
          <label class="block space-y-1">
            <span class="lp-label">{{ t('settings.awsAccountIdOptional') }}</span>
            <input v-model="awsAccountId" class="lp-input font-mono text-xs" placeholder="123456789012" autocomplete="off">
          </label>
          <label class="block space-y-1 sm:col-span-2">
            <span class="lp-label">{{ t('settings.awsRoleNameOptional') }}</span>
            <input v-model="awsRoleName" class="lp-input font-mono text-xs" placeholder="AdministratorAccess" autocomplete="off">
          </label>
        </div>
        <div v-if="activeProvider === 'azure'" class="grid gap-3 sm:grid-cols-2">
          <label class="block space-y-1">
            <span class="lp-label">{{ t('settings.azureTenantOptional') }}</span>
            <input v-model="azureTenantId" class="lp-input font-mono text-xs" placeholder="common" autocomplete="off">
          </label>
          <label class="block space-y-1">
            <span class="lp-label">{{ t('settings.azureSubscriptionOptional') }}</span>
            <input v-model="azureSubscriptionId" class="lp-input font-mono text-xs" autocomplete="off">
          </label>
        </div>
      </div>

      <NuxtErrorBoundary @error="onCredentialsFormError">
        <CloudCredentialsFields
          :key="credentialsFormKey"
          :credentials="credentials"
          :provider="activeProvider"
          :show-gcp-project-id="showGcpProjectId"
        />
        <template #error="{ clearError }">
          <div class="rounded-lg border border-[var(--lp-danger)]/40 bg-[var(--lp-danger)]/5 p-4 text-sm text-[var(--lp-danger)]">
            <p>{{ credentialsFormError || t('settings.errors.load') }}</p>
            <button
              type="button"
              class="lp-btn-ghost mt-2 text-xs uppercase tracking-wide"
              @click="reloadCredentialsForm(clearError)"
            >
              {{ t('settings.localKubernetesRefresh') }}
            </button>
          </div>
        </template>
      </NuxtErrorBoundary>

      <div class="flex flex-wrap gap-3 pt-2">
        <button type="button" class="lp-btn-primary" :disabled="saving" @click="onSave">
          {{ saving ? t('settings.saving') : t('settings.saveProvider', { provider: activeProvider.toUpperCase() }) }}
        </button>
        <button
          type="button"
          class="lp-btn-ghost text-xs uppercase tracking-wide text-[var(--lp-danger)]"
          :disabled="saving || !providers.find((p) => p.id === activeProvider)?.has()"
          @click="onClearProvider"
        >
          {{ t('settings.clearProviderNamed', { provider: activeProvider.toUpperCase() }) }}
        </button>
      </div>

      <p v-if="errorMessage" class="text-sm text-[var(--lp-danger)]">{{ errorMessage }}</p>
      <p v-if="successMessage" class="text-sm text-[var(--lp-ok)]">{{ successMessage }}</p>
    </section>

    <section class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-6 text-sm text-[var(--lp-muted)]">
      <h2 class="text-lg font-semibold text-[var(--lp-text)]">{{ t('settings.alsoConfigure') }}</h2>
      <ul class="mt-3 list-disc space-y-2 pl-5">
        <li>
          <NuxtLink to="/org" class="text-[var(--lp-accent)] hover:underline">{{ t('settings.alsoOrg') }}</NuxtLink>
        </li>
        <li>
          <NuxtLink to="/integrations" class="text-[var(--lp-accent)] hover:underline">{{ t('settings.alsoIntegrations') }}</NuxtLink>
        </li>
      </ul>
    </section>
  </div>
</template>

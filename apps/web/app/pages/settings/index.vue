<script setup lang="ts">
import type { KindClusterStatus } from '~/types/environment'
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'
import { emptyCloudCredentials } from '~/utils/cloudValidation'

const { user } = useAuth()
const { t } = useI18n()
const { getStatus, save, clearAll } = useUserCloudCredentials()
const { getKindStatus, ensureKindCluster, deleteKindCluster } = useEnvironments()

const status = ref<UserCloudCredentialsStatus | null>(null)
const loading = ref(true)
const saving = ref(false)
const clearing = ref(false)
const errorMessage = ref<string | null>(null)
const successMessage = ref<string | null>(null)
const activeProvider = ref<'gcp' | 'aws' | 'azure' | 'cloudflare'>('gcp')
const credentials = reactive(emptyCloudCredentials())

const kindStatus = ref<KindClusterStatus | null>(null)
const kindLoading = ref(false)
const kindCreating = ref(false)
const kindDeleting = ref(false)
const kindError = ref<string | null>(null)
const kindSuccess = ref<string | null>(null)
const clusterName = ref('launchpad')

async function refresh() {
  loading.value = true
  errorMessage.value = null
  try {
    status.value = await getStatus()
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

async function onSave() {
  saving.value = true
  errorMessage.value = null
  successMessage.value = null
  try {
    status.value = await save({ ...credentials })
    Object.assign(credentials, emptyCloudCredentials())
    successMessage.value = t('settings.credentialsSaved')
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : t('settings.errors.save')
  } finally {
    saving.value = false
  }
}

async function onClearProvider() {
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

      <p v-if="status && providers.find((p) => p.id === activeProvider)?.has()" class="text-xs text-[var(--lp-muted)]">
        {{ t('settings.stored') }}
        <span class="font-mono text-[var(--lp-accent)]">
          {{ providers.find((p) => p.id === activeProvider)?.hint() || t('settings.configured') }}
        </span>
        - {{ t('settings.replaceHint') }}
      </p>

      <CloudCredentialsFields v-model:credentials="credentials" :provider="activeProvider" />

      <div class="flex flex-wrap gap-3 pt-2">
        <button type="button" class="lp-btn-primary" :disabled="saving" @click="onSave">
          {{ saving ? t('settings.saving') : t('settings.saveProvider', { provider: activeProvider.toUpperCase() }) }}
        </button>
        <button
          type="button"
          class="lp-btn-ghost text-xs uppercase tracking-wide"
          :disabled="saving || !providers.find((p) => p.id === activeProvider)?.has()"
          @click="onClearProvider"
        >
          {{ t('settings.clearProvider', { provider: activeProvider.toUpperCase() }) }}
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

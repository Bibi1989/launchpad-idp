<script setup lang="ts">
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'
import { emptyCloudCredentials } from '~/utils/cloudValidation'

const { user } = useAuth()
const { t } = useI18n()
const { getStatus, save, clearAll } = useUserCloudCredentials()

const status = ref<UserCloudCredentialsStatus | null>(null)
const loading = ref(true)
const saving = ref(false)
const clearing = ref(false)
const errorMessage = ref<string | null>(null)
const successMessage = ref<string | null>(null)
const activeProvider = ref<'gcp' | 'aws' | 'azure' | 'cloudflare'>('gcp')
const credentials = reactive(emptyCloudCredentials())

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

onMounted(() => {
  void refresh()
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

const providers = computed(() => [
  { id: 'gcp' as const, label: t('launch.targets.gcp'), has: () => status.value?.has_gcp, hint: () => status.value?.gcp_label },
  { id: 'aws' as const, label: t('launch.targets.aws'), has: () => status.value?.has_aws, hint: () => status.value?.aws_label },
  { id: 'azure' as const, label: t('launch.targets.azure'), has: () => status.value?.has_azure, hint: () => status.value?.azure_label },
  { id: 'cloudflare' as const, label: t('launch.targets.cloudflare'), has: () => status.value?.has_cloudflare, hint: () => status.value?.cloudflare_label },
])
</script>

<template>
  <div class="mx-auto max-w-3xl animate-fade-up space-y-8">
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

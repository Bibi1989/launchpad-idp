<script setup lang="ts">
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'
import { emptyCloudCredentials } from '~/utils/cloudValidation'

const { user } = useAuth()
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
    errorMessage.value = err instanceof Error ? err.message : 'Failed to load credentials'
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
    successMessage.value = 'Credentials saved (encrypted at rest).'
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Save failed'
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
    successMessage.value = `Cleared ${activeProvider.value.toUpperCase()} credentials.`
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Clear failed'
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
    successMessage.value = 'All account cloud credentials removed.'
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Clear failed'
  } finally {
    clearing.value = false
  }
}

const providers = [
  { id: 'gcp' as const, label: 'Google Cloud', has: () => status.value?.has_gcp, hint: () => status.value?.gcp_label },
  { id: 'aws' as const, label: 'AWS', has: () => status.value?.has_aws, hint: () => status.value?.aws_label },
  { id: 'azure' as const, label: 'Azure', has: () => status.value?.has_azure, hint: () => status.value?.azure_label },
  { id: 'cloudflare' as const, label: 'Cloudflare', has: () => status.value?.has_cloudflare, hint: () => status.value?.cloudflare_label },
]
</script>

<template>
  <div class="mx-auto max-w-3xl animate-fade-up space-y-8">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">Account</p>
      <h1 class="text-3xl font-semibold tracking-tight">Settings</h1>
      <p class="text-sm text-[var(--lp-muted)]">
        Profile for <strong class="text-[var(--lp-text)]">{{ user?.email }}</strong>.
        Store cloud keys once - Provision and sandbox sessions can reuse them when workspace fields are blank.
      </p>
    </header>

    <section class="space-y-4 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-6">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="text-lg font-semibold">Cloud credentials</h2>
        <button
          type="button"
          class="lp-btn-ghost text-xs uppercase tracking-wide text-[var(--lp-danger)]"
          :disabled="clearing || loading"
          @click="onClearAll"
        >
          {{ clearing ? 'Clearing…' : 'Clear all' }}
        </button>
      </div>
      <p class="text-sm text-[var(--lp-muted)]">
        Keys are Fernet-encrypted at rest. Prefer short-lived credentials or keyless WIF / IAM roles when possible.
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
        Stored:
        <span class="font-mono text-[var(--lp-accent)]">
          {{ providers.find((p) => p.id === activeProvider)?.hint() || 'configured' }}
        </span>
        - paste new values below to replace.
      </p>

      <CloudCredentialsFields v-model:credentials="credentials" :provider="activeProvider" />

      <div class="flex flex-wrap gap-3 pt-2">
        <button type="button" class="lp-btn-primary" :disabled="saving" @click="onSave">
          {{ saving ? 'Saving…' : `Save ${activeProvider.toUpperCase()}` }}
        </button>
        <button
          type="button"
          class="lp-btn-ghost text-xs uppercase tracking-wide"
          :disabled="saving || !providers.find((p) => p.id === activeProvider)?.has()"
          @click="onClearProvider"
        >
          Clear {{ activeProvider.toUpperCase() }}
        </button>
      </div>

      <p v-if="errorMessage" class="text-sm text-[var(--lp-danger)]">{{ errorMessage }}</p>
      <p v-if="successMessage" class="text-sm text-[var(--lp-ok)]">{{ successMessage }}</p>
    </section>

    <section class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-6 text-sm text-[var(--lp-muted)]">
      <h2 class="text-lg font-semibold text-[var(--lp-text)]">Also configure</h2>
      <ul class="mt-3 list-disc space-y-2 pl-5">
        <li>
          <NuxtLink to="/org" class="text-[var(--lp-accent)] hover:underline">Organization</NuxtLink>
          - members, invites, SSO
        </li>
        <li>
          <NuxtLink to="/integrations" class="text-[var(--lp-accent)] hover:underline">Integrations</NuxtLink>
          - GitHub App, GitLab
        </li>
      </ul>
    </section>
  </div>
</template>

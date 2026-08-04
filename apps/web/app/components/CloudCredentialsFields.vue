<script setup lang="ts">
import type { CloudCredentialsForm } from '~/utils/cloudValidation'

const credentials = defineModel<CloudCredentialsForm>('credentials', { required: true })

const props = withDefaults(
  defineProps<{
    provider: 'gcp' | 'aws' | 'azure' | 'cloudflare'
    saPlaceholder?: string
  }>(),
  {
    saPlaceholder: 'Paste service account JSON (encrypted at rest)',
  },
)

const gcpAuthMode = ref<'sa' | 'wif'>('sa')
const awsAuthMode = ref<'keys' | 'oidc'>('keys')

watch(
  () => props.provider,
  () => {
    gcpAuthMode.value = 'sa'
    awsAuthMode.value = 'keys'
  },
)
</script>

<template>
  <div class="space-y-3">
    <template v-if="provider === 'gcp'">
      <div class="flex gap-2">
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-xs font-medium transition"
          :class="
            gcpAuthMode === 'sa'
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
              : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)]'
          "
          @click="gcpAuthMode = 'sa'"
        >
          Service account JSON
        </button>
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-xs font-medium transition"
          :class="
            gcpAuthMode === 'wif'
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
              : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)]'
          "
          @click="gcpAuthMode = 'wif'"
        >
          Keyless (WIF / OIDC)
        </button>
      </div>

      <label v-if="gcpAuthMode === 'sa'" class="block space-y-2">
        <span class="lp-label">GCP SA key JSON</span>
        <textarea
          v-model="credentials.gcp_sa_key_json"
          rows="4"
          class="lp-input font-mono text-xs"
          :placeholder="saPlaceholder"
        />
      </label>

      <div v-else class="grid gap-3 sm:grid-cols-2">
        <p class="sm:col-span-2 text-xs text-[var(--lp-muted)]">
          Minted short-lived OIDC tokens via Workload Identity Federation - no long-lived keys in the sandbox.
        </p>
        <label class="block space-y-2">
          <span class="lp-label">GCP project number</span>
          <input
            v-model="credentials.gcp_wif_project_number"
            class="lp-input font-mono text-xs"
            placeholder="123456789012"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">WIF pool ID</span>
          <input
            v-model="credentials.gcp_wif_pool_id"
            class="lp-input font-mono text-xs"
            placeholder="launchpad-pool"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">WIF provider ID</span>
          <input
            v-model="credentials.gcp_wif_provider_id"
            class="lp-input font-mono text-xs"
            placeholder="launchpad-provider"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Target SA email</span>
          <input
            v-model="credentials.gcp_wif_target_sa_email"
            class="lp-input font-mono text-xs"
            placeholder="deployer@project.iam.gserviceaccount.com"
            autocomplete="off"
          >
        </label>
      </div>
    </template>

    <template v-else-if="provider === 'aws'">
      <div class="flex gap-2">
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-xs font-medium transition"
          :class="
            awsAuthMode === 'keys'
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
              : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)]'
          "
          @click="awsAuthMode = 'keys'"
        >
          Access keys
        </button>
        <button
          type="button"
          class="rounded-lg border px-3 py-1.5 text-xs font-medium transition"
          :class="
            awsAuthMode === 'oidc'
              ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10 text-[var(--lp-text)]'
              : 'border-[var(--lp-line)] text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)]'
          "
          @click="awsAuthMode = 'oidc'"
        >
          Keyless (role ARN / OIDC)
        </button>
      </div>

      <div v-if="awsAuthMode === 'keys'" class="grid gap-3 sm:grid-cols-2">
        <label class="block space-y-2">
          <span class="lp-label">Access key ID</span>
          <input v-model="credentials.aws_access_key_id" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Secret access key</span>
          <input
            v-model="credentials.aws_secret_access_key"
            type="password"
            class="lp-input"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2 sm:col-span-2">
          <span class="lp-label">Session token (optional)</span>
          <input v-model="credentials.aws_session_token" class="lp-input" autocomplete="off">
        </label>
      </div>

      <div v-else class="grid gap-3 sm:grid-cols-2">
        <p class="sm:col-span-2 text-xs text-[var(--lp-muted)]">
          Assume an IAM role with a short-lived Launchpad OIDC web-identity token.
        </p>
        <label class="block space-y-2 sm:col-span-2">
          <span class="lp-label">IAM role ARN</span>
          <input
            v-model="credentials.aws_role_arn"
            class="lp-input font-mono text-xs"
            placeholder="arn:aws:iam::123456789012:role/LaunchpadExec"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2 sm:col-span-2">
          <span class="lp-label">Role session name (optional)</span>
          <input
            v-model="credentials.aws_role_session_name"
            class="lp-input font-mono text-xs"
            placeholder="launchpad-exec"
            autocomplete="off"
          >
        </label>
      </div>
    </template>

    <template v-else-if="provider === 'azure'">
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="block space-y-2">
          <span class="lp-label">Client ID</span>
          <input v-model="credentials.azure_client_id" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Client secret</span>
          <input
            v-model="credentials.azure_client_secret"
            type="password"
            class="lp-input"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Tenant ID</span>
          <input v-model="credentials.azure_tenant_id" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">Subscription ID</span>
          <input v-model="credentials.azure_subscription_id" class="lp-input" autocomplete="off">
        </label>
      </div>
    </template>

    <template v-else-if="provider === 'cloudflare'">
      <label class="block space-y-2">
        <span class="lp-label">API token</span>
        <input
          v-model="credentials.cloudflare_api_token"
          type="password"
          class="lp-input"
          autocomplete="off"
        >
      </label>
    </template>
  </div>
</template>

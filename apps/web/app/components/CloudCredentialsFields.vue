<script setup lang="ts">
import type { CloudCredentialsForm } from '~/utils/cloudValidation'
import { AWS_REGIONS, AZURE_LOCATIONS, GCP_REGIONS } from '~/utils/cloudRegions'

const credentials = defineModel<CloudCredentialsForm>('credentials', { required: true })

const props = withDefaults(
  defineProps<{
    provider: 'gcp' | 'aws' | 'azure' | 'cloudflare'
    saPlaceholder?: string
    /** When true, show GCP project id (Connect / WIF). Hidden when SA JSON embeds project_id. */
    showGcpProjectId?: boolean
  }>(),
  {
    saPlaceholder: '',
    showGcpProjectId: undefined,
  },
)

const { t } = useI18n()

const gcpAuthMode = ref<'sa' | 'wif'>('sa')
const awsAuthMode = ref<'keys' | 'oidc'>('keys')

const gcpSaPlaceholder = computed(
  () => props.saPlaceholder || t('credentials.fields.gcpSaKeyJsonPlaceholder'),
)

function projectIdFromSaJson(raw: string): string | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  try {
    const parsed = JSON.parse(trimmed) as { project_id?: unknown }
    if (typeof parsed.project_id === 'string' && parsed.project_id.trim()) {
      return parsed.project_id.trim()
    }
  } catch {
    return null
  }
  return null
}

const saEmbeddedProjectId = computed(() => projectIdFromSaJson(credentials.value.gcp_sa_key_json || ''))

const showProjectIdField = computed(() => {
  if (props.showGcpProjectId === false) return false
  if (props.showGcpProjectId === true) return true
  // Default: hide when SA JSON already includes project_id; show for Connect / WIF.
  if (saEmbeddedProjectId.value) return false
  return gcpAuthMode.value === 'wif'
})

watch(
  () => credentials.value.gcp_sa_key_json,
  (raw) => {
    const project = projectIdFromSaJson(raw || '')
    if (project && !(credentials.value.gcp_project_id || '').trim()) {
      credentials.value.gcp_project_id = project
    }
  },
)

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
      <label v-if="showProjectIdField" class="block space-y-2">
        <span class="lp-label">{{ t('credentials.fields.gcpProjectId') }}</span>
        <input
          v-model="credentials.gcp_project_id"
          class="lp-input font-mono text-xs"
          placeholder="my-gcp-project"
          autocomplete="off"
        >
        <p class="text-xs text-[var(--lp-muted)]">{{ t('credentials.fields.gcpProjectIdHint') }}</p>
      </label>
      <p
        v-else-if="saEmbeddedProjectId"
        class="text-xs text-[var(--lp-muted)]"
      >
        {{ t('credentials.fields.gcpProjectIdFromSa', { project: saEmbeddedProjectId }) }}
      </p>

      <label class="block space-y-2">
        <span class="lp-label">{{ t('credentials.fields.preferredRegion') }}</span>
        <select v-model="credentials.gcp_region" class="lp-input">
          <option value="">{{ t('credentials.fields.preferredRegionPlaceholder') }}</option>
          <option v-for="region in GCP_REGIONS" :key="region.value" :value="region.value">
            {{ region.label }}
          </option>
        </select>
        <p class="text-xs text-[var(--lp-muted)]">{{ t('credentials.fields.preferredRegionHint') }}</p>
      </label>

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
          {{ t('credentials.fields.serviceAccountJson') }}
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
          {{ t('credentials.fields.keylessWif') }}
        </button>
      </div>

      <div v-if="gcpAuthMode === 'sa'" class="space-y-2">
        <label class="block space-y-2">
          <span class="lp-label">{{ t('credentials.fields.gcpSaKeyJson') }}</span>
          <textarea
            v-model="credentials.gcp_sa_key_json"
            rows="5"
            class="lp-input font-mono text-xs"
            :placeholder="gcpSaPlaceholder"
          />
        </label>
        <div class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel-2)]/60 px-3 py-2 text-xs text-[var(--lp-muted)] space-y-1.5">
          <p class="font-medium text-[var(--lp-text)]">{{ t('credentials.fields.gcpSaKeyHowTitle') }}</p>
          <p>{{ t('credentials.fields.gcpSaKeyHowIntro') }}</p>
          <ol class="list-decimal pl-4 space-y-1">
            <li>{{ t('credentials.fields.gcpSaKeyHowStep1') }}</li>
            <li>{{ t('credentials.fields.gcpSaKeyHowStep2') }}</li>
            <li>{{ t('credentials.fields.gcpSaKeyHowStep3') }}</li>
            <li>{{ t('credentials.fields.gcpSaKeyHowStep4') }}</li>
          </ol>
          <p class="font-mono text-[11px] text-[var(--lp-text)] break-all">
            {{ t('credentials.fields.gcpSaKeyHowCli') }}
          </p>
          <p>{{ t('credentials.fields.gcpSaKeyHowNote') }}</p>
        </div>
      </div>

      <div v-else class="grid gap-3 sm:grid-cols-2">
        <p class="sm:col-span-2 text-xs text-[var(--lp-muted)]">
          Minted short-lived OIDC tokens via Workload Identity Federation - no long-lived keys in the sandbox.
        </p>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('credentials.fields.gcpProjectNumber') }}</span>
          <input
            v-model="credentials.gcp_wif_project_number"
            class="lp-input font-mono text-xs"
            placeholder="123456789012"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('credentials.fields.wifPoolId') }}</span>
          <input
            v-model="credentials.gcp_wif_pool_id"
            class="lp-input font-mono text-xs"
            placeholder="launchpad-pool"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('credentials.fields.wifProviderId') }}</span>
          <input
            v-model="credentials.gcp_wif_provider_id"
            class="lp-input font-mono text-xs"
            placeholder="launchpad-provider"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('credentials.fields.targetSaEmail') }}</span>
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
      <label class="block space-y-2">
        <span class="lp-label">{{ t('credentials.fields.preferredRegion') }}</span>
        <select v-model="credentials.aws_region" class="lp-input">
          <option value="">{{ t('credentials.fields.preferredRegionPlaceholder') }}</option>
          <option v-for="region in AWS_REGIONS" :key="region.value" :value="region.value">
            {{ region.label }}
          </option>
        </select>
        <p class="text-xs text-[var(--lp-muted)]">{{ t('credentials.fields.preferredRegionHint') }}</p>
      </label>

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
          {{ t('credentials.fields.accessKeys') }}
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
          {{ t('credentials.fields.keylessOidc') }}
        </button>
      </div>

      <div v-if="awsAuthMode === 'keys'" class="grid gap-3 sm:grid-cols-2">
        <label class="block space-y-2">
          <span class="lp-label">{{ t('credentials.fields.accessKeyId') }}</span>
          <input v-model="credentials.aws_access_key_id" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('credentials.fields.secretAccessKey') }}</span>
          <input
            v-model="credentials.aws_secret_access_key"
            type="password"
            class="lp-input"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2 sm:col-span-2">
          <span class="lp-label">{{ t('credentials.fields.sessionTokenOptional') }}</span>
          <input v-model="credentials.aws_session_token" class="lp-input" autocomplete="off">
        </label>
      </div>

      <div v-else class="grid gap-3 sm:grid-cols-2">
        <p class="sm:col-span-2 text-xs text-[var(--lp-muted)]">
          Assume an IAM role with a short-lived Launchpad OIDC web-identity token.
        </p>
        <label class="block space-y-2 sm:col-span-2">
          <span class="lp-label">{{ t('credentials.fields.iamRoleArn') }}</span>
          <input
            v-model="credentials.aws_role_arn"
            class="lp-input font-mono text-xs"
            placeholder="arn:aws:iam::123456789012:role/LaunchpadExec"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2 sm:col-span-2">
          <span class="lp-label">{{ t('credentials.fields.roleSessionNameOptional') }}</span>
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
      <label class="block space-y-2">
        <span class="lp-label">{{ t('credentials.fields.preferredRegion') }}</span>
        <select v-model="credentials.azure_location" class="lp-input">
          <option value="">{{ t('credentials.fields.preferredRegionPlaceholder') }}</option>
          <option v-for="region in AZURE_LOCATIONS" :key="region.value" :value="region.value">
            {{ region.label }}
          </option>
        </select>
        <p class="text-xs text-[var(--lp-muted)]">{{ t('credentials.fields.preferredRegionHint') }}</p>
      </label>

      <div class="grid gap-3 sm:grid-cols-2">
        <label class="block space-y-2">
          <span class="lp-label">{{ t('credentials.fields.clientId') }}</span>
          <input v-model="credentials.azure_client_id" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('credentials.fields.clientSecret') }}</span>
          <input
            v-model="credentials.azure_client_secret"
            type="password"
            class="lp-input"
            autocomplete="off"
          >
        </label>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('credentials.fields.tenantId') }}</span>
          <input v-model="credentials.azure_tenant_id" class="lp-input" autocomplete="off">
        </label>
        <label class="block space-y-2">
          <span class="lp-label">{{ t('credentials.fields.subscriptionId') }}</span>
          <input v-model="credentials.azure_subscription_id" class="lp-input" autocomplete="off">
        </label>
      </div>
    </template>

    <template v-else-if="provider === 'cloudflare'">
      <label class="block space-y-2">
        <span class="lp-label">{{ t('credentials.fields.apiToken') }}</span>
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

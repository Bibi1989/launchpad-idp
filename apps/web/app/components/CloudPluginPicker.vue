<script setup lang="ts">
import type { CloudProviderCatalogEntry } from '~/types/cloudProviders'
import type { CloudPluginSelection } from '~/types/cloudPluginSelection'
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'
import { defaultRegionForPluginEntry } from '~/utils/pluginRegionDefaults'
import { isDeployPluginEntry, parentCloudOf, pluginIsConnected } from '~/utils/pluginParentCloud'

/**
 * Reusable "deploy target" picker for the cloud plugins, embeddable in the workspace,
 * environment and launch create/update flows. Lets the user pick a connected cloud, then
 * its service (grouped by runtime), region and size. Credentials are managed in Settings;
 * this only records the selection via v-model.
 */
const props = withDefaults(
  defineProps<{
    /** When true, hide built-in clouds (AWS/GCP/...) so the host page can keep its own cards. */
    manifestOnly?: boolean
  }>(),
  { manifestOnly: false },
)

const model = defineModel<CloudPluginSelection>({
  default: () => ({ provider: null, service: null, region: null, tier: null }),
})

const { catalog, load: loadCatalog, getProvider } = useCloudProviders()
const { getStatus: getProviderStatus } = useProviderCredentials()
const { getStatus: getTypedStatus } = useUserCloudCredentials()

const typedStatus = ref<UserCloudCredentialsStatus | null>(null)
const pluginConnected = ref<Record<string, boolean>>({})

const selected = computed<CloudProviderCatalogEntry | null>(() => getProvider(model.value.provider))

function isConnected(providerId: string): boolean {
  const entry = getProvider(providerId) ?? { id: providerId }
  return pluginIsConnected(entry, typedStatus.value, pluginConnected.value)
}

// Connected clouds first, then the rest (so the user sees what they can use now).
const orderedProviders = computed(() =>
  [...catalog.value].sort((a, b) => Number(isConnected(b.id)) - Number(isConnected(a.id))),
)

const groupedProviders = computed(() => {
  const builtin: CloudProviderCatalogEntry[] = []
  const mine: CloudProviderCatalogEntry[] = []
  const org: CloudProviderCatalogEntry[] = []
  const published: CloudProviderCatalogEntry[] = []
  for (const provider of orderedProviders.value) {
    if (provider.source !== 'manifest') {
      if (!props.manifestOnly && isDeployPluginEntry(provider, catalog.value)) builtin.push(provider)
      continue
    }
    else if (provider.owner === 'user') mine.push(provider)
    else if (provider.owner === 'organization' && provider.can_edit !== false) org.push(provider)
    else published.push(provider)
  }
  return { builtin, mine, org, published }
})

onMounted(async () => {
  await loadCatalog()
  try {
    typedStatus.value = await getTypedStatus()
  } catch {
    // best-effort
  }
  await Promise.all(
    [...new Set(catalog.value.map((p) => parentCloudOf(p)).filter((id) => id !== 'gcp' && id !== 'aws' && id !== 'azure' && id !== 'cloudflare'))].map(
      async (parentId) => {
        try {
          const fields = await getProviderStatus(parentId)
          pluginConnected.value = { ...pluginConnected.value, [parentId]: fields.length > 0 }
        } catch {
          // ignore
        }
      },
    ),
  )
})

function selectProvider(id: string) {
  const entry = getProvider(id)
  model.value = {
    provider: id,
    service: entry?.services?.[0]?.services?.[0]?.id ?? null,
    region: entry ? defaultRegionForPluginEntry(entry, typedStatus.value) : null,
    tier: entry?.tiers[0]?.id ?? null,
  }
}

function clearSelection() {
  model.value = { provider: null, service: null, region: null, tier: null }
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="flex items-center justify-between">
      <label class="text-xs font-medium text-[var(--lp-text)]">{{ $t('cloudPluginPicker.label') }}</label>
      <NuxtLink to="/cloud-plugins" class="text-[11px] text-[var(--lp-accent)] hover:underline">
        {{ $t('cloudPluginPicker.manage') }}
      </NuxtLink>
    </div>

    <select
      :value="model.provider ?? ''"
      class="lp-input text-xs"
      @change="(e) => {
        const v = (e.target as HTMLSelectElement).value
        v ? selectProvider(v) : clearSelection()
      }"
    >
      <option value="">{{ $t('cloudPluginPicker.none') }}</option>
      <optgroup v-if="groupedProviders.mine.length" :label="$t('cloudPluginPicker.groupYours')">
        <option v-for="p in groupedProviders.mine" :key="p.id" :value="p.id">
          {{ p.label }}{{ isConnected(p.id) ? ' ✓' : '' }}
        </option>
      </optgroup>
      <optgroup v-if="groupedProviders.org.length" :label="$t('cloudPluginPicker.groupOrg')">
        <option v-for="p in groupedProviders.org" :key="p.id" :value="p.id">
          {{ p.label }}{{ isConnected(p.id) ? ' ✓' : '' }}
        </option>
      </optgroup>
      <optgroup v-if="groupedProviders.published.length" :label="$t('cloudPluginPicker.groupPublic')">
        <option v-for="p in groupedProviders.published" :key="p.id" :value="p.id">
          {{ p.label }}{{ isConnected(p.id) ? ' ✓' : '' }}
        </option>
      </optgroup>
      <optgroup v-if="groupedProviders.builtin.length" :label="$t('cloudPluginPicker.groupBuiltin')">
        <option v-for="p in groupedProviders.builtin" :key="p.id" :value="p.id">
          {{ p.label }}{{ isConnected(p.id) ? ' ✓' : '' }}
        </option>
      </optgroup>
    </select>

    <template v-if="selected">
      <p v-if="!isConnected(selected.id)" class="flex items-center gap-1 text-[11px] text-[var(--lp-muted)]">
        <span class="material-symbols-outlined text-sm text-[var(--lp-accent)]">info</span>
        {{ $t('cloudPluginPicker.notConnected') }}
      </p>

      <div class="grid gap-3 sm:grid-cols-3">
        <div v-if="selected.services && selected.services.length" class="flex flex-col gap-1">
          <span class="text-[11px] text-[var(--lp-muted)]">{{ $t('cloudPluginPicker.service') }}</span>
          <select v-model="model.service" class="lp-input text-xs">
            <optgroup v-for="group in selected.services" :key="group.runtime" :label="group.label">
              <option v-for="svc in group.services" :key="svc.id" :value="svc.id">{{ svc.label }}</option>
            </optgroup>
          </select>
        </div>
        <div v-if="selected.regions.length" class="flex flex-col gap-1">
          <span class="text-[11px] text-[var(--lp-muted)]">{{ $t('cloudPluginPicker.region') }}</span>
          <select v-model="model.region" class="lp-input text-xs">
            <option v-for="r in selected.regions" :key="r.value" :value="r.value">{{ r.label }}</option>
          </select>
        </div>
        <div v-if="selected.tiers.length" class="flex flex-col gap-1">
          <span class="text-[11px] text-[var(--lp-muted)]">{{ $t('cloudPluginPicker.size') }}</span>
          <select v-model="model.tier" class="lp-input text-xs">
            <option v-for="tr in selected.tiers" :key="tr.id" :value="tr.id">
              {{ tr.label }}<template v-if="tr.monthly_usd"> - ~${{ tr.monthly_usd }}/mo</template>
            </option>
          </select>
        </div>
      </div>
    </template>
  </div>
</template>

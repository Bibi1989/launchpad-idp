<script setup lang="ts">
import type { CloudProviderCatalogEntry, RuntimeTarget } from '~/types/cloudProviders'
import type { CloudPluginSelection } from '~/types/cloudPluginSelection'
import { emptyCloudPluginSelection } from '~/types/cloudPluginSelection'
import type { CloudProvider } from '~/types/provisioning'
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'
import { defaultRegionForPluginEntry } from '~/utils/pluginRegionDefaults'
import {
  isDeployPluginEntry,
  isTypedParentCloud,
  parentCloudOf,
  pluginIsConnected,
} from '~/utils/pluginParentCloud'

/**
 * Local sandbox + per-service cloud plugins (GKE, EKS, Cloud Run, user plugins).
 * Parent clouds (GCP/AWS/Azure) stay in Settings as accounts; tiles here are deploy targets.
 */
const props = withDefaults(
  defineProps<{
    includeLocal?: boolean
    /** Launch / provision / promote only accept typed parents in the existing API union. */
    typedOnly?: boolean
    /** When set, only show deploy plugins whose runtime_targets overlap this list. */
    allowedRuntimeTargets?: RuntimeTarget[] | null
    /** Skip picking the first parent-cloud tile when plugin is unset (workspace-driven launch). */
    disableAutoSelect?: boolean
  }>(),
  { includeLocal: true, typedOnly: true, allowedRuntimeTargets: null, disableAutoSelect: false },
)

const typedProvider = defineModel<CloudProvider>('typedProvider', { required: true })
const plugin = defineModel<CloudPluginSelection>('plugin', {
  default: () => emptyCloudPluginSelection(),
})

const { t } = useI18n()
const { catalog, load: loadCatalog, getProvider } = useCloudProviders()
const { getStatus: getProviderStatus } = useProviderCredentials()
const { getStatus: getTypedStatus } = useUserCloudCredentials()

const typedStatus = ref<UserCloudCredentialsStatus | null>(null)
const pluginConnected = ref<Record<string, boolean>>({})

const selectedEntry = computed<CloudProviderCatalogEntry | null>(() => getProvider(plugin.value.provider))

function entryMatchesRuntime(entry: CloudProviderCatalogEntry): boolean {
  const allowed = props.allowedRuntimeTargets
  if (!allowed?.length) return true
  const targets = entry.runtime_targets ?? []
  return targets.some((target) => allowed.includes(target))
}

const tiles = computed(() => {
  const plugins = catalog.value.filter((entry) => {
    if (!isDeployPluginEntry(entry, catalog.value)) return false
    if (!props.typedOnly) return true
    if (!isTypedParentCloud(parentCloudOf(entry))) return false
    return entryMatchesRuntime(entry)
  })
  return plugins
})

const selectedId = computed(() => {
  if (typedProvider.value === 'local' && !plugin.value.provider) return 'local'
  return plugin.value.provider
})

onMounted(async () => {
  await loadCatalog()
  try {
    typedStatus.value = await getTypedStatus()
  } catch {
    // best-effort
  }
  const parents = new Set(
    catalog.value
      .map((entry) => parentCloudOf(entry))
      .filter((id) => id && !isTypedParentCloud(id)),
  )
  await Promise.all(
    [...parents].map(async (parentId) => {
      try {
        const fields = await getProviderStatus(parentId)
        pluginConnected.value = { ...pluginConnected.value, [parentId]: fields.length > 0 }
      } catch {
        // ignore
      }
    }),
  )
  syncSelectionFromPlugin()
  if (
    !props.disableAutoSelect
    && typedProvider.value !== 'local'
    && !plugin.value.provider
  ) {
    const match = tiles.value.find((entry) => parentCloudOf(entry) === typedProvider.value)
    if (match) selectPlugin(match)
  }
})

function syncSelectionFromPlugin() {
  const id = plugin.value.provider
  if (!id || id === 'local') return
  const match = tiles.value.find((entry) => entry.id === id)
  if (!match) return
  const parent = parentCloudOf(match)
  if (isTypedParentCloud(parent)) typedProvider.value = parent
}

watch(
  () => [plugin.value.provider, tiles.value.length] as const,
  () => syncSelectionFromPlugin(),
)

function isConnected(entry: CloudProviderCatalogEntry): boolean {
  return pluginIsConnected(entry, typedStatus.value, pluginConnected.value)
}

function selectLocal() {
  typedProvider.value = 'local'
  plugin.value = emptyCloudPluginSelection()
}

function selectPlugin(entry: CloudProviderCatalogEntry) {
  const parent = parentCloudOf(entry)
  if (isTypedParentCloud(parent)) typedProvider.value = parent
  plugin.value = {
    provider: entry.id,
    service: entry.service ?? entry.services?.[0]?.services?.[0]?.id ?? null,
    region: defaultRegionForPluginEntry(entry, typedStatus.value),
    tier: entry.tiers[0]?.id ?? null,
  }
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="flex items-center justify-between">
      <span class="text-xs font-medium text-[var(--lp-text)]">{{ t('cloudDeployGrid.label') }}</span>
      <NuxtLink to="/cloud-plugins" class="text-[11px] text-[var(--lp-accent)] hover:underline">
        {{ t('cloudDeployGrid.manage') }}
      </NuxtLink>
    </div>

    <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      <button
        v-if="includeLocal"
        type="button"
        class="rounded-lg border p-3 text-left transition"
        :class="
          selectedId === 'local'
            ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
            : 'border-[var(--lp-line)] hover:bg-[var(--lp-panel-2)]'
        "
        @click="selectLocal"
      >
        <p class="text-sm font-medium">{{ t('launch.targets.local') }}</p>
        <p class="text-xs text-[var(--lp-muted)]">{{ t('launch.hints.local') }}</p>
      </button>
      <button
        v-for="entry in tiles"
        :key="entry.id"
        type="button"
        class="rounded-lg border p-3 text-left transition"
        :class="
          selectedId === entry.id
            ? 'border-[var(--lp-accent)] bg-[var(--lp-accent)]/10'
            : 'border-[var(--lp-line)] hover:bg-[var(--lp-panel-2)]'
        "
        @click="selectPlugin(entry)"
      >
        <div class="mb-1 flex items-center justify-between gap-2">
          <span class="material-symbols-outlined text-base text-[var(--lp-accent)]">{{ entry.icon || 'cloud' }}</span>
          <span
            v-if="isConnected(entry)"
            class="rounded-full bg-[var(--lp-success-soft,rgba(34,197,94,0.15))] px-1.5 py-0.5 text-[10px] font-medium text-[var(--lp-success,#22c55e)]"
          >
            {{ t('cloudPlugins.connected') }}
          </span>
        </div>
        <p class="text-sm font-medium">{{ entry.label }}</p>
        <p class="text-xs text-[var(--lp-muted)]">
          {{ entry.description || parentCloudOf(entry).toUpperCase() }}
        </p>
      </button>
    </div>

    <template v-if="selectedEntry">
      <p v-if="!isConnected(selectedEntry)" class="flex items-center gap-1 text-[11px] text-[var(--lp-muted)]">
        <span class="material-symbols-outlined text-sm text-[var(--lp-accent)]">info</span>
        {{ t('cloudPluginPicker.notConnected') }}
      </p>
      <div class="grid gap-3 sm:grid-cols-3">
        <div v-if="(selectedEntry.services?.length ?? 0) > 1" class="flex flex-col gap-1">
          <span class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPluginPicker.service') }}</span>
          <select v-model="plugin.service" class="lp-input text-xs">
            <optgroup v-for="group in selectedEntry.services" :key="group.runtime" :label="group.label">
              <option v-for="svc in group.services" :key="svc.id" :value="svc.id">{{ svc.label }}</option>
            </optgroup>
          </select>
        </div>
        <div v-if="selectedEntry.regions.length" class="flex flex-col gap-1">
          <span class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPluginPicker.region') }}</span>
          <select v-model="plugin.region" class="lp-input text-xs">
            <option v-for="r in selectedEntry.regions" :key="r.value" :value="r.value">{{ r.label }}</option>
          </select>
        </div>
        <div v-if="selectedEntry.tiers.length" class="flex flex-col gap-1">
          <span class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPluginPicker.size') }}</span>
          <select v-model="plugin.tier" class="lp-input text-xs">
            <option v-for="tr in selectedEntry.tiers" :key="tr.id" :value="tr.id">
              {{ tr.label }}<template v-if="tr.monthly_usd"> - ~${{ tr.monthly_usd }}/mo</template>
            </option>
          </select>
        </div>
      </div>
    </template>
  </div>
</template>

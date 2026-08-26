<script setup lang="ts">
import type { CloudProviderCatalogEntry, ScaffoldFile } from '~/types/cloudProviders'
import type { WorkspaceListItem } from '~/types/provisioning'
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'
import { isDeployPluginEntry, isTypedParentCloud, parentCloudOf, pluginIsConnected } from '~/utils/pluginParentCloud'

const { t } = useI18n()
const { catalog, loading: catalogLoading, load: loadCatalog, getProvider } = useCloudProviders()
const { load: loadTools, toolsForCloud } = useProvisioningTools()
const { getStatus: getProviderStatus } = useProviderCredentials()
const { getStatus: getTypedStatus } = useUserCloudCredentials()
const { preview, scaffoldToWorkspace, previewing, scaffolding } = useProviderProvisioning()
const { listWorkspaces } = useProvisioning()
const { remove: removePlugin, uploadBundle, get: getPlugin } = useUserPlugins()
const bundleNotice = ref<string | null>(null)
const uploadingBundle = ref(false)
const modalOpen = ref(false)
const editManifest = shallowRef<Record<string, unknown> | null>(null)
// const editManifest = ref<Record<string, unknown> | null>(null)
const modalError = ref<string | null>(null)

function openCreateModal() {
  editManifest.value = null
  modalError.value = null
  modalOpen.value = true
}

async function openEditModal(pluginId: string) {
  modalError.value = null
  try {
    if (!pluginId) return
    const loaded = await getPlugin(pluginId)
    if (!loaded) {
      modalError.value = 'Plugin not found'
      return
    }
    const manifest = {
      ...loaded.manifest,
      owner: loaded.owner,
      visibility: loaded.visibility,
    }
    editManifest.value = markRaw(manifest)
    modalOpen.value = true
  } catch (err) {
    modalError.value = err instanceof Error ? err.message : 'Failed to load plugin'
  }
}

async function onPluginRegistered() {
  await loadCatalog(true)
}

async function onUploadBundle(pluginId: string, event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  bundleNotice.value = null
  uploadingBundle.value = true
  try {
    const res = await uploadBundle(pluginId, file)
    bundleNotice.value = t('cloudPlugins.bundleUploaded', { count: res.files })
  } catch (err) {
    bundleNotice.value = err instanceof Error ? err.message : 'Upload failed'
  } finally {
    uploadingBundle.value = false
    input.value = ''
  }
}

async function onRemovePlugin(pluginId: string) {
  await removePlugin(pluginId)
  if (selectedId.value === pluginId) selectedId.value = null
  await loadCatalog(true)
}

const platformFilter = ref<'all' | string>('all')
const categoryFilter = ref<'all' | string>('all')

const deployCatalog = computed(() =>
  catalog.value.filter((p) => isDeployPluginEntry(p, catalog.value)),
)

const platformOptions = computed(() => {
  const values = new Set<string>()
  for (const item of deployCatalog.value) {
    values.add(parentCloudOf(item))
  }
  return Array.from(values).sort((a, b) => a.localeCompare(b))
})

const categoryOptions = computed(() => {
  const values = new Set<string>()
  for (const item of deployCatalog.value) {
    values.add((item.category || 'cloud-provider').toString())
  }
  return Array.from(values).sort((a, b) => a.localeCompare(b))
})

const visibleCatalog = computed(() =>
  deployCatalog.value.filter((p) => {
    if (platformFilter.value !== 'all' && parentCloudOf(p) !== platformFilter.value) {
      return false
    }
    const category = (p.category || 'cloud-provider').toString()
    if (categoryFilter.value !== 'all' && category !== categoryFilter.value) {
      return false
    }
    return true
  }),
)

const selectedId = ref<string | null>(null)
const selectedService = ref<string | null>(null)
const region = ref<string | null>(null)
const tier = ref<string | null>(null)
const selectedIac = ref<string | null>(null)
const selectedConfig = ref<string | null>(null)
const advancedOpen = ref(false)

// Connection status (read-only here; credentials are edited in Settings).
const typedStatus = ref<UserCloudCredentialsStatus | null>(null)
const pluginConnected = ref<Record<string, boolean>>({})

// Provisioning artifact state.
const specImage = ref('')
const specPort = ref(8080)
const workspaces = ref<WorkspaceListItem[]>([])
const selectedWorkspaceId = ref<string | null>(null)
const previewFiles = ref<ScaffoldFile[]>([])
const activePreviewPath = ref<string | null>(null)
const scaffoldNotice = ref<string | null>(null)

const selected = computed<CloudProviderCatalogEntry | null>(() => getProvider(selectedId.value))
const cloudTools = computed(() => toolsForCloud(selectedId.value))
const activePreviewFile = computed(
  () => previewFiles.value.find((f) => f.path === activePreviewPath.value) ?? null,
)

function isConnected(providerId: string): boolean {
  const entry = getProvider(providerId) ?? { id: providerId }
  return pluginIsConnected(entry, typedStatus.value, pluginConnected.value)
}

function defaultIacTool(id: string): string | null {
  const iac = toolsForCloud(id).iac
  return iac.find((tool) => tool.default)?.id ?? iac[0]?.id ?? null
}

onMounted(async () => {
  const [, , ws] = await Promise.all([loadCatalog(), loadTools(), listWorkspaces()])
  workspaces.value = ws ?? []
  try {
    typedStatus.value = await getTypedStatus()
  } catch {
    // ignore - status is best-effort
  }
  // Plugin clouds (not in the typed vault) get their connected state from provider creds.
  await Promise.all(
    [
      ...new Set(
        catalog.value
          .map((p) => parentCloudOf(p))
          .filter((id) => !isTypedParentCloud(id)),
      ),
    ].map(async (parentId) => {
      try {
        const fields = await getProviderStatus(parentId)
        pluginConnected.value = { ...pluginConnected.value, [parentId]: fields.length > 0 }
      } catch {
        // ignore per-provider status errors
      }
    }),
  )
})

watch([visibleCatalog, selectedId], ([items, current]) => {
  if (!current) return
  if (!items.some((item) => item.id === current)) {
    selectedId.value = null
  }
})

function defaultConfigTool(id: string): string | null {
  const config = toolsForCloud(id).config
  return config.find((tool) => tool.default)?.id ?? config[0]?.id ?? null
}

function selectProvider(id: string) {
  selectedId.value = id
  const entry = getProvider(id)
  selectedService.value = entry?.services?.[0]?.services?.[0]?.id ?? null
  region.value = entry?.regions[0]?.value ?? null
  tier.value = entry?.tiers[0]?.id ?? null
  selectedIac.value = defaultIacTool(id)
  selectedConfig.value = defaultConfigTool(id)
  previewFiles.value = []
  activePreviewPath.value = null
  scaffoldNotice.value = null
  advancedOpen.value = false
}

function buildSpec() {
  return {
    image: specImage.value.trim() || null,
    app_port: specPort.value || 8080,
    region: region.value,
    tier: tier.value,
    runtime_target: 'docker_host',
    env_vars: {},
  }
}

async function onPreview() {
  if (!selectedId.value || !selectedIac.value) return
  scaffoldNotice.value = null
  previewFiles.value = await preview(selectedId.value, selectedIac.value, buildSpec())
  activePreviewPath.value = previewFiles.value[0]?.path ?? null
}

async function onScaffold() {
  if (!selectedId.value || !selectedIac.value || !selectedWorkspaceId.value) return
  scaffoldNotice.value = null
  const written = await scaffoldToWorkspace(
    selectedId.value,
    selectedWorkspaceId.value,
    selectedIac.value,
    buildSpec(),
  )
  previewFiles.value = written
  activePreviewPath.value = written[0]?.path ?? null
  scaffoldNotice.value = t('cloudPlugins.scaffoldDone', { count: written.length })
}
</script>

<template>
  <div class="mx-auto flex max-w-5xl flex-col gap-6 p-4 sm:p-6">
    <header class="flex flex-col gap-1">
      <h1 class="text-lg font-semibold text-[var(--lp-text)]">{{ t('cloudPlugins.title') }}</h1>
      <p class="text-sm text-[var(--lp-muted)]">{{ t('cloudPlugins.subtitleServices') }}</p>
    </header>

    <section class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[var(--lp-line)] p-3">
      <div>
        <p class="text-xs font-medium text-[var(--lp-text)]">{{ t('cloudPlugins.addPluginTitle') }}</p>
        <p class="mt-1 text-[11px] text-[var(--lp-muted)]">{{ t('cloudPlugins.addPluginHint') }}</p>
        <p
          v-if="modalError"
          class="mt-2 rounded-md border border-[var(--lp-danger,#e5484d)]/40 bg-[var(--lp-danger,#e5484d)]/10 px-2 py-1.5 text-[11px] text-[var(--lp-danger,#e5484d)]"
        >
          {{ modalError }}
        </p>
      </div>
      <button type="button" class="lp-btn-primary text-xs" @click="openCreateModal">
        {{ t('cloudPlugins.openRegister') }}
      </button>
    </section>

    <section class="grid gap-3 rounded-lg border border-[var(--lp-line)] p-3 sm:grid-cols-2">
      <label class="flex flex-col gap-1">
        <span class="text-[11px] font-medium text-[var(--lp-muted)]">Platform</span>
        <select v-model="platformFilter" class="lp-input text-xs">
          <option value="all">All</option>
          <option v-for="platform in platformOptions" :key="platform" :value="platform">
            {{ platform }}
          </option>
        </select>
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-[11px] font-medium text-[var(--lp-muted)]">Category</span>
        <select v-model="categoryFilter" class="lp-input text-xs">
          <option value="all">All</option>
          <option v-for="category in categoryOptions" :key="category" :value="category">
            {{ category }}
          </option>
        </select>
      </label>
    </section>

    <p v-if="catalogLoading && !catalog.length" class="text-xs text-[var(--lp-muted)]">
      {{ t('cloudPlugins.loading') }}
    </p>

    <!-- Provider grid -->
    <section class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      <button
        v-for="p in visibleCatalog"
        :key="p.id"
        type="button"
        class="flex flex-col gap-1 rounded-lg border p-3 text-left transition"
        :class="selectedId === p.id
          ? 'border-[var(--lp-accent)] bg-[var(--lp-accent-soft,rgba(99,102,241,0.1))]'
          : 'border-[var(--lp-line)] hover:border-[var(--lp-accent)]'"
        @click="selectProvider(p.id)"
      >
        <div class="flex items-center justify-between">
          <span class="material-symbols-outlined text-[var(--lp-muted)]">{{ p.icon || 'cloud' }}</span>
          <span
            v-if="isConnected(p.id)"
            class="inline-flex items-center gap-0.5 rounded-full bg-[var(--lp-success-soft,rgba(34,197,94,0.15))] px-2 py-0.5 text-[10px] font-medium text-[var(--lp-success,#22c55e)]"
          >
            <span class="material-symbols-outlined text-[0.8rem]">check_circle</span>
            {{ t('cloudPlugins.connected') }}
          </span>
        </div>
        <span class="text-sm font-medium text-[var(--lp-text)]">{{ p.label }}</span>
        <span class="text-[10px] text-[var(--lp-muted)]">{{ p.description || p.runtime_targets.join(', ') }}</span>
      </button>
    </section>

    <section v-if="selected" class="flex flex-col gap-5 rounded-lg border border-[var(--lp-line)] p-4">
      <div class="flex items-center justify-between">
        <h2 class="text-base font-semibold text-[var(--lp-text)]">
          {{ selected.label }}
          <span
            v-if="selected.source === 'manifest'"
            class="ml-2 rounded-full bg-[var(--lp-accent-soft,rgba(99,102,241,0.1))] px-2 py-0.5 text-[10px] font-medium text-[var(--lp-accent)]"
          >          {{ t('cloudPlugins.pluginBadge') }}</span>
          <span
            v-if="selected.owner === 'user'"
            class="ml-1 rounded-full bg-[var(--lp-panel-2,rgba(0,0,0,0.06))] px-2 py-0.5 text-[10px] font-medium text-[var(--lp-muted)]"
          >{{ t('cloudPlugins.ownerUser') }}</span>
          <span
            v-else-if="selected.owner === 'organization'"
            class="ml-1 rounded-full bg-[var(--lp-panel-2,rgba(0,0,0,0.06))] px-2 py-0.5 text-[10px] font-medium text-[var(--lp-muted)]"
          >{{ t('cloudPlugins.ownerOrg') }}</span>
          <span
            v-if="selected.visibility === 'public'"
            class="ml-1 rounded-full px-2 py-0.5 text-[10px] font-medium text-[var(--lp-accent)]"
          >{{ t('cloudPlugins.published') }}</span>
        </h2>
        <div class="flex items-center gap-3">
          <button
            v-if="selected.source === 'manifest' && selected.can_edit !== false"
            type="button"
            class="text-[11px] text-[var(--lp-accent)] hover:underline"
            @click="openEditModal(selected.id)"
          >
            {{ t('cloudPlugins.editPlugin') }}
          </button>
          <button
            v-if="selected.source === 'manifest' && selected.can_edit !== false"
            type="button"
            class="text-[11px] text-[var(--lp-danger,#e5484d)] hover:underline"
            @click="onRemovePlugin(selected.id)"
          >
            {{ t('cloudPlugins.removePlugin') }}
          </button>
          <a
            v-if="selected.docs_url"
            :href="selected.docs_url"
            target="_blank"
            rel="noopener noreferrer"
            class="text-[11px] text-[var(--lp-accent)] hover:underline"
          >
            {{ t('cloudPlugins.docs') }}
          </a>
        </div>
      </div>

      <!-- Bundle upload (manifest plugins only) -->
      <div
        v-if="selected.source === 'manifest' && selected.can_edit !== false"
        class="flex flex-wrap items-center gap-2 rounded-md border border-dashed border-[var(--lp-line)] p-2 text-[11px]"
      >
        <span class="material-symbols-outlined text-sm text-[var(--lp-accent)]">folder_zip</span>
        <span class="text-[var(--lp-muted)]">{{ t('cloudPlugins.bundleHint') }}</span>
        <label class="lp-btn-ghost cursor-pointer text-xs">
          {{ uploadingBundle ? t('cloudPlugins.uploading') : t('cloudPlugins.uploadBundle') }}
          <input
            type="file"
            accept=".zip,.tar,.tar.gz,.tgz"
            class="hidden"
            :disabled="uploadingBundle"
            @change="(e) => onUploadBundle(selected!.id, e)"
          >
        </label>
        <span v-if="bundleNotice" class="text-[var(--lp-success,#22c55e)]">{{ bundleNotice }}</span>
      </div>

      <!-- Credentials live in Settings, not here -->
      <div class="flex items-center gap-2 rounded-md border border-[var(--lp-line)] bg-[var(--lp-panel-2,rgba(0,0,0,0.03))] p-2 text-[11px]">
        <span class="material-symbols-outlined text-sm text-[var(--lp-accent)]">key</span>
        <span class="text-[var(--lp-muted)]">
          <template v-if="isConnected(selected.id)">{{ t('cloudPlugins.credsConnected') }}</template>
          <template v-else>{{ t('cloudPlugins.credsMissing') }}</template>
        </span>
        <NuxtLink to="/settings" class="ml-auto text-[var(--lp-accent)] hover:underline">
          {{ t('cloudPlugins.manageInSettings') }}
        </NuxtLink>
      </div>

      <!-- Services dropdown (grouped by runtime) -->
      <div v-if="selected.services && selected.services.length" class="flex flex-col gap-1">
        <label class="text-xs font-medium text-[var(--lp-text)]">{{ t('cloudPlugins.serviceLabel') }}</label>
        <select v-model="selectedService" class="lp-input text-xs">
          <optgroup v-for="group in selected.services" :key="group.runtime" :label="group.label">
            <option v-for="svc in group.services" :key="svc.id" :value="svc.id">
              {{ svc.label }} - {{ svc.description }}
            </option>
          </optgroup>
        </select>
      </div>

      <!-- Region + size -->
      <div v-if="selected.regions.length || selected.tiers.length" class="grid gap-3 sm:grid-cols-2">
        <div v-if="selected.regions.length" class="flex flex-col gap-1">
          <label class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPlugins.region') }}</label>
          <select v-model="region" class="lp-input text-xs">
            <option v-for="r in selected.regions" :key="r.value" :value="r.value">{{ r.label }}</option>
          </select>
        </div>
        <div v-if="selected.tiers.length" class="flex flex-col gap-1">
          <label class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPlugins.size') }}</label>
          <select v-model="tier" class="lp-input text-xs">
            <option v-for="tr in selected.tiers" :key="tr.id" :value="tr.id">
              {{ tr.label }}<template v-if="tr.monthly_usd"> - ~${{ tr.monthly_usd }}/mo</template>
            </option>
          </select>
        </div>
      </div>

      <!-- Advanced: optional file preview / scaffold (not required for Create Workspace) -->
      <div class="rounded-lg border border-[var(--lp-line)]">
        <button
          type="button"
          class="flex w-full items-center justify-between px-4 py-3 text-left"
          @click="advancedOpen = !advancedOpen"
        >
          <span>
            <span class="text-xs font-semibold uppercase tracking-wide text-[var(--lp-text)]">
              {{ t('cloudPlugins.advancedTitle') }}
            </span>
            <span class="mt-0.5 block text-[10px] font-normal text-[var(--lp-muted)]">
              {{ t('cloudPlugins.advancedSubtitle') }}
            </span>
          </span>
          <span class="material-symbols-outlined text-base text-[var(--lp-muted)]">
            {{ advancedOpen ? 'expand_less' : 'expand_more' }}
          </span>
        </button>

        <div v-if="advancedOpen" class="flex flex-col gap-5 border-t border-[var(--lp-line)] p-4">
          <p class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPlugins.advancedBlurb') }}</p>

          <div class="grid gap-4 sm:grid-cols-2">
            <div class="flex flex-col gap-2">
              <h3 class="text-xs font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
                {{ t('cloudPlugins.provisioningTool') }}
              </h3>
              <p class="text-[10px] text-[var(--lp-muted)]">{{ t('cloudPlugins.provisioningToolHint') }}</p>
              <label
                v-for="tool in cloudTools.iac"
                :key="tool.id"
                class="flex cursor-pointer items-start gap-2 rounded-md border p-2 text-xs"
                :class="selectedIac === tool.id ? 'border-[var(--lp-accent)]' : 'border-[var(--lp-line)]'"
              >
                <input v-model="selectedIac" type="radio" :value="tool.id" class="mt-0.5">
                <span class="flex flex-col">
                  <span class="font-medium text-[var(--lp-text)]">{{ tool.label }}</span>
                  <span class="text-[10px] text-[var(--lp-muted)]">{{ tool.description }}</span>
                </span>
              </label>
            </div>

            <div class="flex flex-col gap-2">
              <h3 class="text-xs font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
                {{ t('cloudPlugins.configTool') }}
              </h3>
              <p class="text-[10px] text-[var(--lp-muted)]">{{ t('cloudPlugins.configToolHint') }}</p>
              <label
                v-for="tool in cloudTools.config"
                :key="tool.id"
                class="flex items-start gap-2 rounded-md border p-2 text-xs"
                :class="[
                  selectedConfig === tool.id ? 'border-[var(--lp-accent)]' : 'border-[var(--lp-line)]',
                  tool.implemented === false ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
                ]"
              >
                <input
                  v-model="selectedConfig"
                  type="radio"
                  :value="tool.id"
                  class="mt-0.5"
                  :disabled="tool.implemented === false"
                >
                <span class="flex flex-col">
                  <span class="font-medium text-[var(--lp-text)]">{{ tool.label }}</span>
                  <span class="text-[10px] text-[var(--lp-muted)]">{{ tool.description }}</span>
                  <span v-if="tool.implemented === false" class="text-[10px] text-[var(--lp-warn,#eab308)]">
                    {{ t('cloudPlugins.registerConfigPlugin') }}
                  </span>
                </span>
              </label>
            </div>
          </div>

          <div class="flex flex-col gap-3 border-t border-[var(--lp-line)] pt-4">
            <h3 class="text-xs font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
              {{ t('cloudPlugins.artifactTitle') }}
            </h3>
            <p class="text-[10px] text-[var(--lp-muted)]">{{ t('cloudPlugins.artifactHint') }}</p>

            <div class="grid gap-3 sm:grid-cols-3">
              <div class="flex flex-col gap-1">
                <label class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPlugins.image') }}</label>
                <input v-model="specImage" class="lp-input font-mono text-xs" placeholder="ghcr.io/acme/app:latest">
              </div>
              <div class="flex flex-col gap-1">
                <label class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPlugins.port') }}</label>
                <input v-model.number="specPort" type="number" class="lp-input text-xs" placeholder="8080">
              </div>
              <div class="flex flex-col gap-1">
                <label class="text-[11px] text-[var(--lp-muted)]">{{ t('cloudPlugins.targetWorkspace') }}</label>
                <select v-model="selectedWorkspaceId" class="lp-input text-xs">
                  <option :value="null">{{ t('cloudPlugins.chooseWorkspace') }}</option>
                  <option v-for="ws in workspaces" :key="ws.id" :value="ws.id">{{ ws.name }}</option>
                </select>
              </div>
            </div>

            <div class="flex flex-wrap items-center gap-2">
              <button type="button" class="lp-btn-ghost text-xs" :disabled="previewing" @click="onPreview">
                {{ previewing ? t('cloudPlugins.previewing') : t('cloudPlugins.preview') }}
              </button>
              <button
                type="button"
                class="lp-btn-primary text-xs"
                :disabled="scaffolding || !selectedWorkspaceId"
                @click="onScaffold"
              >
                {{ scaffolding ? t('cloudPlugins.scaffolding') : t('cloudPlugins.scaffold') }}
              </button>
              <span v-if="scaffoldNotice" class="text-[11px] text-[var(--lp-success,#22c55e)]">
                {{ scaffoldNotice }}
              </span>
            </div>

            <div v-if="previewFiles.length" class="rounded-md border border-[var(--lp-line)]">
              <div class="flex flex-wrap gap-1 border-b border-[var(--lp-line)] p-2">
                <button
                  v-for="f in previewFiles"
                  :key="f.path"
                  type="button"
                  class="rounded px-2 py-1 font-mono text-[10px]"
                  :class="activePreviewPath === f.path
                    ? 'bg-[var(--lp-accent-soft,rgba(99,102,241,0.1))] text-[var(--lp-text)]'
                    : 'text-[var(--lp-muted)] hover:text-[var(--lp-text)]'"
                  @click="activePreviewPath = f.path"
                >
                  {{ f.path }}
                </button>
              </div>
              <pre
                v-if="activePreviewFile"
                class="max-h-80 overflow-auto p-3 font-mono text-[11px] leading-relaxed text-[var(--lp-text)]"
              >{{ activePreviewFile.content }}</pre>
            </div>
          </div>
        </div>
      </div>
    </section>

    <ClientOnly>
      <CreatePluginModal
        :open="modalOpen"
        :initial-manifest="editManifest"
        @close="modalOpen = false; editManifest = null"
        @registered="onPluginRegistered"
      />
    </ClientOnly>
  </div>
</template>

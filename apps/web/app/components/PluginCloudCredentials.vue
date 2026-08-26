<script setup lang="ts">
import type { CloudProviderCatalogEntry } from '~/types/cloudProviders'
import { isLegacyCloudId, isTypedParentCloud, parentCloudOf } from '~/utils/pluginParentCloud'

/**
 * Settings section for the plugin-only clouds (Hetzner, DigitalOcean, Railway, ...) that
 * are not part of the typed GCP/AWS/Azure/Cloudflare vault. Credentials are stored in the
 * encrypted provider-credentials vault via useProviderCredentials.
 */
const { t } = useI18n()
const { catalog, load: loadCatalog } = useCloudProviders()
const { getStatus, save, remove, validate, saving, validating } = useProviderCredentials()

const pluginClouds = computed<CloudProviderCatalogEntry[]>(() =>
  catalog.value.filter((p) => {
    if (isLegacyCloudId(p.id) || p.source === 'builtin-plugin') return false
    const parent = parentCloudOf(p)
    if (isTypedParentCloud(parent)) return false
    if (p.parent_cloud && p.parent_cloud !== p.id) return false
    return true
  }),
)
const selectedId = ref<string | null>(null)
const credInputs = ref<Record<string, string>>({})
const revealed = ref<Set<string>>(new Set())
const connected = ref<Record<string, string[]>>({})
const validateResult = ref<{ valid: boolean; message?: string | null } | null>(null)
const savedNotice = ref(false)

const selected = computed<CloudProviderCatalogEntry | null>(
  () => pluginClouds.value.find((p) => p.id === selectedId.value) ?? null,
)

onMounted(async () => {
  await loadCatalog()
  const first = pluginClouds.value[0]
  if (!selectedId.value && first) selectProvider(first.id)
  await Promise.all(
    pluginClouds.value.map(async (p) => {
      try {
        connected.value = { ...connected.value, [p.id]: await getStatus(p.id) }
      } catch {
        // ignore per-provider status errors
      }
    }),
  )
})

function selectProvider(id: string) {
  selectedId.value = id
  credInputs.value = {}
  revealed.value = new Set()
  validateResult.value = null
  savedNotice.value = false
}

function isConnected(id: string): boolean {
  return (connected.value[id]?.length ?? 0) > 0
}

function toggleReveal(name: string) {
  const next = new Set(revealed.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  revealed.value = next
}

async function onSave() {
  if (!selectedId.value) return
  savedNotice.value = false
  const status = await save(selectedId.value, credInputs.value)
  connected.value = { ...connected.value, [selectedId.value]: status[selectedId.value] ?? [] }
  savedNotice.value = true
}

async function onValidate() {
  if (!selectedId.value) return
  const hasInput = Object.values(credInputs.value).some((v) => v.trim())
  validateResult.value = await validate(selectedId.value, hasInput ? credInputs.value : undefined)
}

async function onDisconnect() {
  if (!selectedId.value) return
  const status = await remove(selectedId.value)
  connected.value = { ...connected.value, [selectedId.value]: status[selectedId.value] ?? [] }
  credInputs.value = {}
  validateResult.value = null
  savedNotice.value = false
}
</script>

<template>
  <section class="space-y-4 rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-6">
    <div class="flex flex-col gap-1">
      <h2 class="text-base font-semibold text-[var(--lp-text)]">{{ t('pluginCreds.title') }}</h2>
      <p class="text-sm text-[var(--lp-muted)]">{{ t('pluginCreds.subtitle') }}</p>
    </div>

    <!-- Cloud selector -->
    <div class="flex flex-wrap gap-2">
      <button
        v-for="p in pluginClouds"
        :key="p.id"
        type="button"
        class="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-xs"
        :class="selectedId === p.id
          ? 'border-[var(--lp-accent)] bg-[var(--lp-accent-soft,rgba(99,102,241,0.1))]'
          : 'border-[var(--lp-line)] hover:border-[var(--lp-accent)]'"
        @click="selectProvider(p.id)"
      >
        {{ p.label }}
        <span
          v-if="isConnected(p.id)"
          class="material-symbols-outlined text-[0.9rem] text-[var(--lp-success,#22c55e)]"
        >check_circle</span>
      </button>
    </div>

    <div v-if="selected" class="flex flex-col gap-3">
      <div
        v-for="field in selected.credential_fields"
        :key="field.name"
        class="flex flex-col gap-1"
      >
        <label class="text-[11px] text-[var(--lp-muted)]">
          {{ field.label }}
          <span v-if="!field.required">({{ t('pluginCreds.optional') }})</span>
          <span
            v-if="connected[selected.id]?.includes(field.name)"
            class="ml-1 text-[var(--lp-success,#22c55e)]"
          >{{ t('pluginCreds.set') }}</span>
        </label>
        <div class="relative">
          <input
            class="lp-input w-full text-xs"
            :class="{ 'font-mono pr-8': field.secret }"
            :type="field.secret && !revealed.has(field.name) ? 'password' : 'text'"
            :value="credInputs[field.name] ?? ''"
            :placeholder="connected[selected.id]?.includes(field.name)
              ? t('pluginCreds.leaveBlankKeep')
              : (field.placeholder ?? '')"
            autocomplete="off"
            spellcheck="false"
            @input="credInputs[field.name] = ($event.target as HTMLInputElement).value"
          >
          <button
            v-if="field.secret"
            type="button"
            class="absolute inset-y-0 right-1 flex items-center px-1 text-[var(--lp-muted)] hover:text-[var(--lp-text)]"
            :aria-label="revealed.has(field.name) ? t('pluginCreds.hide') : t('pluginCreds.reveal')"
            @click="toggleReveal(field.name)"
          >
            <span class="material-symbols-outlined text-sm">
              {{ revealed.has(field.name) ? 'visibility_off' : 'visibility' }}
            </span>
          </button>
        </div>
        <span v-if="field.help" class="text-[10px] text-[var(--lp-muted)]">{{ field.help }}</span>
      </div>

      <div class="flex flex-wrap items-center gap-2 pt-1">
        <button type="button" class="lp-btn-primary text-xs" :disabled="saving" @click="onSave">
          {{ saving ? t('pluginCreds.saving') : t('pluginCreds.save') }}
        </button>
        <button type="button" class="lp-btn-ghost text-xs" :disabled="validating" @click="onValidate">
          {{ validating ? t('pluginCreds.validating') : t('pluginCreds.validate') }}
        </button>
        <button
          v-if="isConnected(selected.id)"
          type="button"
          class="lp-btn-ghost text-xs text-[var(--lp-danger,#e5484d)]"
          @click="onDisconnect"
        >
          {{ t('pluginCreds.disconnect') }}
        </button>
        <span v-if="savedNotice" class="text-[11px] text-[var(--lp-success,#22c55e)]">
          {{ t('pluginCreds.saved') }}
        </span>
        <span
          v-if="validateResult"
          class="text-[11px]"
          :class="validateResult.valid ? 'text-[var(--lp-success,#22c55e)]' : 'text-[var(--lp-danger,#e5484d)]'"
        >
          {{ validateResult.valid ? t('pluginCreds.valid') : (validateResult.message || t('pluginCreds.invalid')) }}
        </span>
      </div>
    </div>
  </section>
</template>

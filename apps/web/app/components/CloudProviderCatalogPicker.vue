<script setup lang="ts">
import type { CloudProviderSelection, RuntimeTarget } from '~/types/cloudProviders'

/**
 * Catalog-driven cloud provider picker. The provider list, credential fields,
 * regions and compute tiers are all sourced from the backend registry
 * (GET /api/v1/cloud-providers) via useCloudProviders - there is no hardcoded
 * provider table here. Adding a backend plugin surfaces it automatically.
 */
const props = withDefaults(
  defineProps<{
    // Optionally restrict to providers exposing one of these runtime targets.
    runtimeTargets?: RuntimeTarget[]
  }>(),
  { runtimeTargets: () => [] },
)

const model = defineModel<CloudProviderSelection>({
  default: () => ({ provider: '', region: null, tier: null, credentials: {} }),
})

const { catalog, loading, error, load, getProvider } = useCloudProviders()
const revealed = ref<Set<string>>(new Set())

onMounted(() => {
  void load()
})

const providers = computed(() => {
  const targets = props.runtimeTargets
  if (!targets.length) return catalog.value
  return catalog.value.filter((p) => p.runtime_targets.some((t) => targets.includes(t)))
})

const selected = computed(() => getProvider(model.value.provider))

function selectProvider(id: string) {
  const entry = getProvider(id)
  model.value = {
    provider: id,
    region: entry?.regions[0]?.value ?? null,
    tier: entry?.tiers[0]?.id ?? null,
    credentials: {},
  }
  revealed.value = new Set()
}

function setCredential(name: string, value: string) {
  model.value = {
    ...model.value,
    credentials: { ...model.value.credentials, [name]: value },
  }
}

function toggleReveal(name: string) {
  const next = new Set(revealed.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  revealed.value = next
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <p v-if="loading && !catalog.length" class="text-xs text-[var(--lp-muted)]">
      Loading providers...
    </p>
    <p v-else-if="error" class="text-xs text-[var(--lp-danger,#e5484d)]">
      {{ error }}
    </p>

    <!-- Provider selection -->
    <div class="flex flex-col gap-1">
      <label class="text-xs font-medium text-[var(--lp-text)]">Cloud provider</label>
      <div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <button
          v-for="p in providers"
          :key="p.id"
          type="button"
          class="rounded-md border px-3 py-2 text-left text-xs transition"
          :class="model.provider === p.id
            ? 'border-[var(--lp-accent)] bg-[var(--lp-accent-soft,rgba(99,102,241,0.1))]'
            : 'border-[var(--lp-border,#2a2a2a)] hover:border-[var(--lp-accent)]'"
          @click="selectProvider(p.id)"
        >
          <span class="block font-medium text-[var(--lp-text)]">{{ p.label }}</span>
          <span class="block text-[10px] text-[var(--lp-muted)]">
            {{ p.runtime_targets.join(', ') }}
          </span>
        </button>
      </div>
    </div>

    <template v-if="selected">
      <!-- Credential fields -->
      <div v-if="selected.credential_fields.length" class="flex flex-col gap-2">
        <label class="text-xs font-medium text-[var(--lp-text)]">Credentials</label>
        <div
          v-for="field in selected.credential_fields"
          :key="field.name"
          class="flex flex-col gap-1"
        >
          <span class="text-[11px] text-[var(--lp-muted)]">
            {{ field.label }}
            <span v-if="!field.required">(optional)</span>
          </span>
          <div class="relative">
            <input
              class="lp-input w-full text-xs"
              :class="{ 'font-mono pr-8': field.secret }"
              :type="field.secret && !revealed.has(field.name) ? 'password' : 'text'"
              :value="model.credentials[field.name] ?? ''"
              :placeholder="field.placeholder ?? ''"
              autocomplete="off"
              spellcheck="false"
              @input="setCredential(field.name, ($event.target as HTMLInputElement).value)"
            >
            <button
              v-if="field.secret"
              type="button"
              class="absolute inset-y-0 right-1 flex items-center px-1 text-[var(--lp-muted)] hover:text-[var(--lp-text)]"
              :aria-label="revealed.has(field.name) ? 'Hide' : 'Reveal'"
              @click="toggleReveal(field.name)"
            >
              <span class="material-symbols-outlined text-sm">
                {{ revealed.has(field.name) ? 'visibility_off' : 'visibility' }}
              </span>
            </button>
          </div>
          <span v-if="field.help" class="text-[10px] text-[var(--lp-muted)]">{{ field.help }}</span>
        </div>
      </div>

      <!-- Region -->
      <div v-if="selected.regions.length" class="flex flex-col gap-1">
        <label class="text-xs font-medium text-[var(--lp-text)]">Region</label>
        <select v-model="model.region" class="lp-input text-xs">
          <option v-for="r in selected.regions" :key="r.value" :value="r.value">{{ r.label }}</option>
        </select>
      </div>

      <!-- Tier / size -->
      <div v-if="selected.tiers.length" class="flex flex-col gap-1">
        <label class="text-xs font-medium text-[var(--lp-text)]">Size</label>
        <select v-model="model.tier" class="lp-input text-xs">
          <option v-for="tier in selected.tiers" :key="tier.id" :value="tier.id">
            {{ tier.label }}
            <template v-if="tier.monthly_usd"> - ~${{ tier.monthly_usd }}/mo</template>
          </option>
        </select>
      </div>

      <a
        v-if="selected.docs_url"
        :href="selected.docs_url"
        target="_blank"
        rel="noopener noreferrer"
        class="text-[11px] text-[var(--lp-accent)] hover:underline"
      >
        {{ selected.label }} docs
      </a>
    </template>
  </div>
</template>

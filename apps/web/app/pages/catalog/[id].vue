<script setup lang="ts">
import type { CatalogService, ServiceTier } from '~/types/catalog'

const route = useRoute()
const serviceId = computed(() => String(route.params.id))
const { getService, updateService } = useCatalog()

const service = ref<CatalogService | null>(null)
const loading = ref(true)
const saving = ref(false)
const editing = ref(false)
const errorMessage = ref<string | null>(null)
const statusMessage = ref<string | null>(null)

const form = reactive({
  description: '',
  owner: '',
  tier: 'tier-2' as ServiceTier,
  slo_target: '99.5',
  runbook_url: '',
  on_call: '',
})

const tiers: ServiceTier[] = ['critical', 'tier-1', 'tier-2', 'tier-3']

function hydrateForm(row: CatalogService) {
  form.description = row.description
  form.owner = row.owner
  form.tier = row.tier
  form.slo_target = row.slo_target
  form.runbook_url = row.runbook_url ?? ''
  form.on_call = row.on_call ?? ''
}

async function load() {
  loading.value = true
  errorMessage.value = null
  try {
    service.value = await getService(serviceId.value)
    hydrateForm(service.value)
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Failed to load service'
    service.value = null
  } finally {
    loading.value = false
  }
}

function startEdit() {
  if (!service.value) return
  hydrateForm(service.value)
  editing.value = true
  statusMessage.value = null
}

function cancelEdit() {
  if (service.value) hydrateForm(service.value)
  editing.value = false
}

async function saveEdit() {
  if (!service.value || saving.value) return
  saving.value = true
  errorMessage.value = null
  statusMessage.value = null
  try {
    service.value = await updateService(service.value.id, {
      description: form.description,
      owner: form.owner.trim(),
      tier: form.tier,
      slo_target: form.slo_target.trim(),
      runbook_url: form.runbook_url.trim() || null,
      on_call: form.on_call.trim() || null,
    })
    hydrateForm(service.value)
    editing.value = false
    statusMessage.value = 'Service updated'
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : 'Failed to update service'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-3xl animate-fade-up space-y-6 pb-12">
    <NuxtLink to="/catalog" class="font-mono text-xs uppercase tracking-wide text-[var(--lp-muted)] hover:text-[var(--lp-text)]">
      ← Catalog
    </NuxtLink>

    <p v-if="loading" class="text-sm text-[var(--lp-muted)]">Loading…</p>
    <p v-else-if="errorMessage && !service" class="text-sm text-[var(--lp-danger)]">{{ errorMessage }}</p>

    <template v-else-if="service">
      <header class="flex flex-wrap items-start justify-between gap-4">
        <div class="space-y-2">
          <div class="flex flex-wrap items-center gap-3">
            <h1 class="text-2xl font-semibold">{{ service.name }}</h1>
            <span
              class="rounded border px-2 py-1 font-mono text-[10px] uppercase"
              :class="service.scorecard.passed
                ? 'border-[var(--lp-ok)]/40 text-[var(--lp-ok)]'
                : 'border-amber-500/40 text-amber-400'"
            >
              Score {{ service.compliance_score }}/100
            </span>
          </div>
          <p v-if="!editing" class="text-sm text-[var(--lp-muted)]">
            {{ service.description || 'No description' }}
          </p>
        </div>
        <div class="flex flex-wrap gap-2">
          <NuxtLink
            v-if="service.workspace_id"
            :to="`/workspaces/${service.workspace_id}`"
            class="lp-btn-ghost text-xs uppercase tracking-wide"
          >
            Open workspace
          </NuxtLink>
          <button
            v-if="!editing"
            type="button"
            class="lp-btn-primary text-xs uppercase tracking-wide"
            @click="startEdit"
          >
            <span class="material-symbols-outlined text-base">edit</span>
            Update service
          </button>
        </div>
      </header>

      <p v-if="statusMessage" class="text-sm text-[var(--lp-ok)]">{{ statusMessage }}</p>
      <p v-if="errorMessage && service" class="text-sm text-[var(--lp-danger)]">{{ errorMessage }}</p>

      <section
        v-if="editing"
        class="space-y-4 rounded-xl border border-[var(--lp-line)] p-5"
      >
        <h2 class="text-lg font-semibold">Edit service metadata</h2>
        <label class="block space-y-2">
          <span class="lp-label">Description</span>
          <textarea v-model="form.description" rows="3" class="lp-input" maxlength="500" />
        </label>
        <div class="grid gap-4 sm:grid-cols-2">
          <label class="block space-y-2">
            <span class="lp-label">Owner</span>
            <input v-model="form.owner" class="lp-input" required>
          </label>
          <label class="block space-y-2">
            <span class="lp-label">On-call</span>
            <input v-model="form.on_call" class="lp-input" placeholder="team@example.com">
          </label>
          <label class="block space-y-2">
            <span class="lp-label">Tier</span>
            <select v-model="form.tier" class="lp-input">
              <option v-for="t in tiers" :key="t" :value="t">{{ t }}</option>
            </select>
          </label>
          <label class="block space-y-2">
            <span class="lp-label">SLO target (%)</span>
            <input v-model="form.slo_target" class="lp-input" placeholder="99.5">
          </label>
        </div>
        <label class="block space-y-2">
          <span class="lp-label">Runbook URL</span>
          <input v-model="form.runbook_url" class="lp-input" type="url" placeholder="https://…">
        </label>
        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            class="lp-btn-primary text-xs uppercase tracking-wide"
            :disabled="saving || !form.owner.trim()"
            @click="saveEdit"
          >
            {{ saving ? 'Saving…' : 'Save changes' }}
          </button>
          <button
            type="button"
            class="lp-btn-ghost text-xs uppercase tracking-wide"
            :disabled="saving"
            @click="cancelEdit"
          >
            Cancel
          </button>
        </div>
      </section>

      <section v-else class="grid gap-4 rounded-xl border border-[var(--lp-line)] p-5 sm:grid-cols-2">
        <div>
          <p class="lp-label">Owner</p>
          <p class="mt-1 text-sm">{{ service.owner }}</p>
        </div>
        <div>
          <p class="lp-label">Tier / SLO</p>
          <p class="mt-1 font-mono text-sm">{{ service.tier }} · {{ service.slo_target }}%</p>
        </div>
        <div>
          <p class="lp-label">Template</p>
          <p class="mt-1 font-mono text-sm">{{ service.template_id }}@{{ service.template_version }}</p>
        </div>
        <div>
          <p class="lp-label">On-call</p>
          <p class="mt-1 text-sm">{{ service.on_call || '—' }}</p>
        </div>
        <div v-if="service.runbook_url" class="sm:col-span-2">
          <p class="lp-label">Runbook</p>
          <a :href="service.runbook_url" class="mt-1 block text-sm text-[var(--lp-accent)] hover:underline" target="_blank" rel="noreferrer">
            {{ service.runbook_url }}
          </a>
        </div>
      </section>

      <section class="space-y-3 rounded-xl border border-[var(--lp-line)] p-5">
        <h2 class="text-lg font-semibold">Scorecard</h2>
        <p class="text-xs text-[var(--lp-muted)]">
          Gate {{ service.scorecard.gate }} — {{ service.scorecard.passed ? 'passed' : 'needs work' }}
        </p>
        <div
          v-for="item in service.scorecard.items"
          :key="item.id"
          class="flex items-start justify-between gap-3 rounded-lg border border-[var(--lp-line)] px-3 py-2"
        >
          <div>
            <p class="text-sm font-medium">{{ item.title }}</p>
            <p class="text-xs text-[var(--lp-muted)]">{{ item.detail }}</p>
          </div>
          <span
            class="shrink-0 font-mono text-xs"
            :class="item.passed ? 'text-[var(--lp-ok)]' : 'text-[var(--lp-danger)]'"
          >
            {{ item.points }}/{{ item.max_points }}
          </span>
        </div>
      </section>
    </template>
  </div>
</template>

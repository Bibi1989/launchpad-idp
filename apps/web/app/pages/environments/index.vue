<script setup lang="ts">
import type { OrgCostSummary } from '~/types/auth'

const { environments, loading, error, refresh, destroy, retryProvision, pauseEnvironment, resumeEnvironment } = useEnvironments()
const { activeOrgId, fetchOrgCosts } = useOrgs()
const route = useRoute()
const createEnvOpen = useState('lp-create-env-open', () => false)
const destroyingId = ref<string | null>(null)
const confirmDestroyId = ref<string | null>(null)
const retryingId = ref<string | null>(null)
const recentLogLines = ref<string[]>([])
const orgCosts = ref<OrgCostSummary | null>(null)

async function onPause(id: string) {
  try {
    await pauseEnvironment(id)
  } catch (err) {
    console.error('Pause failed:', err)
  }
}

async function onResume(id: string) {
  try {
    await resumeEnvironment(id)
  } catch (err) {
    console.error('Resume failed:', err)
  }
}

const pendingDestroyName = computed(() => {
  const id = confirmDestroyId.value
  if (!id) return ''
  return environments.value.find((env) => env.id === id)?.name ?? 'this environment'
})

const linkedWorkspaceId = computed(() => {
  const raw = route.query.workspace
  return typeof raw === 'string' && raw.length > 0 ? raw : null
})

const activeEnvs = computed(() =>
  environments.value.filter((e) => e.status === 'RUNNING' || e.status === 'PROVISIONING'),
)

const failedEnvs = computed(() => environments.value.filter((e) => e.status === 'FAILED'))

const hourlySpend = computed(() => {
  const sum = activeEnvs.value.reduce((acc, env) => {
    const n = Number.parseFloat(env.cost_estimate_hourly)
    return acc + (Number.isFinite(n) ? n : 0)
  }, 0)
  return sum.toFixed(2)
})

const accruedSpend = computed(() => {
  if (orgCosts.value) return Number.parseFloat(orgCosts.value.cloud_accrued).toFixed(2)
  const sum = activeEnvs.value.reduce((acc, env) => {
    if (env.is_local) return acc
    const n = Number.parseFloat(env.cost_accrued)
    return acc + (Number.isFinite(n) ? n : 0)
  }, 0)
  return sum.toFixed(2)
})

const softCap = computed(() => {
  if (!orgCosts.value) return null
  return Number.parseFloat(orgCosts.value.soft_cost_cap).toFixed(2)
})

const softCapExceeded = computed(() => Boolean(orgCosts.value?.soft_cost_cap_exceeded))

const liveEnvironments = computed(() =>
  environments.value.filter((e) => e.status !== 'DESTROYED').slice(0, 12),
)

const providers = computed(() => {
  const set = new Set(
    environments.value
      .map((e) => e.namespace_name.split('-')[0])
      .filter(Boolean),
  )
  return set.size || 1
})

async function loadOrgCosts() {
  try {
    orgCosts.value = await fetchOrgCosts(activeOrgId.value)
  } catch {
    orgCosts.value = null
  }
}

onMounted(async () => {
  try {
    await refresh()
    await loadOrgCosts()
  } catch {
    // surfaced via error state
  }

  if (linkedWorkspaceId.value) {
    createEnvOpen.value = true
  }

  recentLogLines.value = [
    `[${new Date().toLocaleTimeString()}] INFO: Dashboard synced with control plane`,
    ...activeEnvs.value.slice(0, 4).map(
      (e) =>
        `[${new Date(e.updated_at).toLocaleTimeString()}] ${e.status}: ${e.name} · ${e.namespace_name}`,
    ),
  ]
})

watch(environments, (list) => {
  recentLogLines.value = [
    `[${new Date().toLocaleTimeString()}] INFO: ${list.length} environment(s) loaded`,
    ...list
      .filter((e) => e.status !== 'DESTROYED')
      .slice(0, 6)
      .map(
        (e) =>
          `[${new Date(e.updated_at).toLocaleTimeString()}] ${e.status}: ${e.name} · TTL ${new Date(e.ttl_expires_at).toLocaleString()}`,
      ),
  ]
})

watch(activeOrgId, () => {
  void loadOrgCosts()
})

function requestDestroy(id: string) {
  if (destroyingId.value) return
  confirmDestroyId.value = id
}

async function onDestroy() {
  const id = confirmDestroyId.value
  if (!id || destroyingId.value) return
  confirmDestroyId.value = null
  destroyingId.value = id
  try {
    await destroy(id)
    await refresh()
    await loadOrgCosts()
  } catch {
    // error surfaced on list refresh
  } finally {
    destroyingId.value = null
  }
}

async function onRetry(id: string) {
  if (retryingId.value) return
  retryingId.value = id
  try {
    const updated = await retryProvision(id)
    onCardUpdate({ id: updated.id, status: updated.status })
    await refresh()
  } catch {
    // error surfaced on list refresh
  } finally {
    retryingId.value = null
  }
}

function onCardUpdate(patch: { id?: string; status?: string; latest_commit_sha?: string | null }) {
  if (!patch.id) return
  const target = environments.value.find((env) => env.id === patch.id)
  if (!target) return
  if (patch.status) {
    target.status = patch.status as typeof target.status
  }
  if (patch.latest_commit_sha !== undefined) {
    target.latest_commit_sha = patch.latest_commit_sha
  }
}
</script>

<template>
  <div class="space-y-8">
    <!-- Metrics banner -->
    <section class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5 animate-fade-up">
      <div class="lp-glass rounded-xl p-4">
        <p class="lp-label">Active Environments</p>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-3xl font-bold tracking-tight">{{ activeEnvs.length }}</span>
          <span class="font-mono text-xs text-[var(--lp-ok)]">
            ● {{ environments.length }} total
          </span>
        </div>
      </div>

      <div class="lp-glass rounded-xl border-l-2 border-l-[var(--lp-accent)] p-4">
        <p class="lp-label">Est. Hourly Spend</p>
        <div class="mt-2 flex items-baseline gap-1">
          <span class="text-3xl font-bold tracking-tight text-[var(--lp-accent)]">${{ hourlySpend }}</span>
          <span class="text-sm text-[var(--lp-muted)]">/hr</span>
        </div>
      </div>

      <div
        class="lp-glass rounded-xl p-4"
        :class="softCapExceeded ? 'border-l-2 border-l-[var(--lp-danger)]' : ''"
      >
        <p class="lp-label">Org Cloud Accrued</p>
        <div class="mt-2 flex items-baseline gap-1">
          <span
            class="text-3xl font-bold tracking-tight"
            :class="softCapExceeded ? 'text-[var(--lp-danger)]' : 'text-[var(--lp-text)]'"
          >
            ${{ accruedSpend }}
          </span>
          <span v-if="softCap" class="text-sm text-[var(--lp-muted)]">/ ${{ softCap }}</span>
        </div>
      </div>

      <div class="lp-glass rounded-xl p-4">
        <p class="lp-label">Attention</p>
        <div class="mt-2 flex items-baseline gap-2">
          <span
            class="text-3xl font-bold tracking-tight"
            :class="failedEnvs.length ? 'text-[var(--lp-danger)]' : 'text-[var(--lp-ok)]'"
          >
            {{ failedEnvs.length }}
          </span>
          <span class="text-sm text-[var(--lp-muted)]">failed</span>
        </div>
      </div>

      <div class="lp-glass rounded-xl p-4">
        <p class="lp-label">Control Plane</p>
        <div class="mt-2 flex items-center gap-3">
          <div class="flex h-8 w-8 items-center justify-center rounded-full border border-[var(--lp-line)] bg-[var(--lp-panel-2)]">
            <span class="material-symbols-outlined text-[var(--lp-accent)] text-base">cloud</span>
          </div>
          <div>
            <p class="text-sm font-medium">{{ providers }} namespace group{{ providers === 1 ? '' : 's' }}</p>
            <p class="text-xs text-[var(--lp-muted)]">Ephemeral K8s targets</p>
          </div>
        </div>
      </div>
    </section>

    <!-- Live environments -->
    <section class="space-y-4 animate-fade-up [animation-delay:80ms]">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="text-xl font-semibold">Live Environments</h2>
        <div class="flex items-center gap-2">
          <button type="button" class="lp-btn-ghost py-1.5 text-xs uppercase tracking-wide" @click="refresh()">
            <span class="material-symbols-outlined text-sm">refresh</span>
            Refresh
          </button>
          <button type="button" class="lp-btn-primary py-1.5 text-xs uppercase tracking-wide" @click="navigateTo('/launch')">
            <span class="material-symbols-outlined text-sm">rocket_launch</span>
            Launch preview
          </button>
        </div>
      </div>

      <p v-if="error" class="text-sm text-[var(--lp-danger)]">{{ error }}</p>
      <p v-else-if="loading" class="text-sm text-[var(--lp-muted)]">Loading environments…</p>
      <div
        v-else-if="liveEnvironments.length === 0"
        class="rounded-xl border border-dashed border-[var(--lp-line)] px-6 py-12 text-center"
      >
        <span class="material-symbols-outlined mb-3 text-4xl text-[var(--lp-muted)]">deployed_code</span>
        <p class="text-sm text-[var(--lp-muted)]">No active environments yet.</p>
        <NuxtLink to="/launch" class="lp-btn-primary mt-4 inline-flex">
          Launch your first preview
        </NuxtLink>
        <button type="button" class="mt-3 block w-full text-xs text-[var(--lp-muted)] hover:underline" @click="createEnvOpen = true">
          Or use advanced git form
        </button>
      </div>

      <div v-else class="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <EnvironmentCard
          v-for="env in liveEnvironments"
          :key="env.id"
          :environment="env"
          :retrying="retryingId === env.id"
          @destroy="requestDestroy"
          @retry="onRetry"
          @pause="onPause"
          @resume="onResume"
          @update="onCardUpdate"
        />
      </div>
    </section>

    <ConfirmDialog
      :open="confirmDestroyId !== null"
      title="Destroy environment?"
      :message="`Destroy preview “${pendingDestroyName}”? Kubernetes resources for this environment will be torn down. This cannot be undone.`"
      confirm-label="Yes, destroy"
      cancel-label="No"
      :busy="destroyingId !== null"
      @update:open="(value) => { if (!value) confirmDestroyId = null }"
      @confirm="onDestroy"
    />

    <!-- System logs panel -->
    <section class="lp-glass overflow-hidden rounded-xl animate-fade-up [animation-delay:140ms]">
      <div class="flex items-center justify-between bg-[var(--lp-panel-2)] px-4 py-2">
        <div class="flex items-center gap-4">
          <span class="lp-label text-white/60">System Logs</span>
          <div class="flex gap-1.5">
            <span class="h-2 w-2 rounded-full bg-[var(--lp-danger)]" />
            <span class="h-2 w-2 rounded-full bg-[var(--lp-warn)]" />
            <span class="h-2 w-2 rounded-full bg-[var(--lp-ok)]" />
          </div>
        </div>
        <span class="font-mono text-[10px] text-[var(--lp-muted)]">control-plane</span>
      </div>
      <div class="h-44 overflow-y-auto bg-black/70 p-4 font-mono text-xs leading-6">
        <p
          v-for="(line, idx) in recentLogLines"
          :key="idx"
          :class="{
            'text-[var(--lp-ok)]': line.includes('RUNNING') || line.includes('SUCCESS'),
            'text-[var(--lp-danger)]': line.includes('FAILED') || line.includes('WARN'),
            'text-[var(--lp-accent)]': line.includes('PROVISIONING') || line.includes('SYNC'),
            'text-[var(--lp-muted)]': !line.includes('RUNNING') && !line.includes('FAILED') && !line.includes('PROVISIONING'),
          }"
        >
          {{ line }}
        </p>
        <p class="animate-pulse text-[var(--lp-muted)]">_</p>
      </div>
    </section>
  </div>
</template>

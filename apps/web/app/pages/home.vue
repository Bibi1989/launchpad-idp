<script setup lang="ts">
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'

const { t } = useI18n()
const { user } = useAuth()
const { environments, refresh: refreshEnvs } = useEnvironments()
const { listWorkspaces } = useProvisioning()
const { getStatus } = useUserCloudCredentials()

const credStatus = ref<UserCloudCredentialsStatus | null>(null)
const workspaceCount = ref(0)
const loading = ref(true)

onMounted(async () => {
  loading.value = true
  try {
    await refreshEnvs({ soft: environments.value.length > 0 })
    const [ws, status] = await Promise.all([
      listWorkspaces().catch(() => []),
      getStatus().catch(() => null),
    ])
    workspaceCount.value = ws.length
    credStatus.value = status
  } finally {
    loading.value = false
  }
})

const runningEnvs = computed(() =>
  environments.value.filter((e) => e.status === 'RUNNING' || e.status === 'PROVISIONING').length,
)

const cloudReady = computed(() => {
  const s = credStatus.value
  if (!s) return false
  return s.has_gcp || s.has_aws || s.has_azure || s.has_cloudflare
})

const actions = computed(() => [
  {
    title: t('home.actions.launch.title'),
    blurb: t('home.actions.launch.blurb'),
    to: '/launch',
    icon: 'rocket_launch',
  },
  {
    title: t('home.actions.provision.title'),
    blurb: t('home.actions.provision.blurb'),
    to: '/provision',
    icon: 'schema',
  },
  {
    title: t('home.actions.catalog.title'),
    blurb: t('home.actions.catalog.blurb'),
    to: '/catalog',
    icon: 'inventory_2',
  },
  {
    title: t('home.actions.credentials.title'),
    blurb: t('home.actions.credentials.blurb'),
    to: '/settings',
    icon: 'key',
  },
])
</script>

<template>
  <div class="animate-fade-up space-y-10">
    <header class="space-y-3">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        {{ t('home.eyebrow') }}
      </p>
      <h1 class="text-3xl font-semibold tracking-tight md:text-4xl">
        {{
          user?.display_name
            ? t('home.welcomeNamed', { name: user.display_name })
            : t('home.welcome')
        }}
      </h1>
      <p class="max-w-2xl text-[var(--lp-muted)]">
        {{ t('home.blurb') }}
      </p>
    </header>

    <section class="grid gap-4 sm:grid-cols-3">
      <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5">
        <p class="lp-label">{{ t('home.activeEnvironments') }}</p>
        <p class="mt-2 font-mono text-3xl text-[var(--lp-text)]">
          {{ loading ? '-' : runningEnvs }}
        </p>
        <NuxtLink to="/environments" class="mt-3 inline-block text-sm text-[var(--lp-accent)] hover:underline">
          {{ t('common.viewAll') }}
        </NuxtLink>
      </div>
      <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5">
        <p class="lp-label">{{ t('home.workspaces') }}</p>
        <p class="mt-2 font-mono text-3xl text-[var(--lp-text)]">
          {{ loading ? '-' : workspaceCount }}
        </p>
        <NuxtLink to="/workspaces" class="mt-3 inline-block text-sm text-[var(--lp-accent)] hover:underline">
          {{ t('home.openWorkspaces') }}
        </NuxtLink>
      </div>
      <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5">
        <p class="lp-label">{{ t('home.cloudKeys') }}</p>
        <p class="mt-2 text-lg font-medium text-[var(--lp-text)]">
          {{ loading ? '…' : cloudReady ? t('common.configured') : t('common.notSet') }}
        </p>
        <p v-if="credStatus && !loading" class="mt-1 text-xs text-[var(--lp-muted)]">
          <span v-if="credStatus.has_gcp">GCP </span>
          <span v-if="credStatus.has_aws">AWS </span>
          <span v-if="credStatus.has_azure">Azure </span>
          <span v-if="credStatus.has_cloudflare">Cloudflare </span>
          <span v-if="!cloudReady">{{ t('home.addKeys') }}</span>
        </p>
        <NuxtLink to="/settings" class="mt-3 inline-block text-sm text-[var(--lp-accent)] hover:underline">
          {{ t('home.manageCredentials') }}
        </NuxtLink>
      </div>
    </section>

    <HomeObservabilityPanel />

    <section class="space-y-4">
      <h2 class="text-lg font-semibold">{{ t('home.quickActions') }}</h2>
      <div class="grid gap-4 sm:grid-cols-2">
        <NuxtLink
          v-for="action in actions"
          :key="action.to"
          :to="action.to"
          class="group rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5 transition hover:border-[var(--lp-accent)]/40 hover:bg-[var(--lp-panel-2)]"
        >
          <div class="flex items-start gap-3">
            <span class="material-symbols-outlined text-[var(--lp-accent)]">{{ action.icon }}</span>
            <div>
              <p class="font-medium text-[var(--lp-text)] group-hover:text-[var(--lp-accent)]">
                {{ action.title }}
              </p>
              <p class="mt-1 text-sm text-[var(--lp-muted)]">{{ action.blurb }}</p>
            </div>
          </div>
        </NuxtLink>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import type { UserCloudCredentialsStatus } from '~/types/userCredentials'

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
    await refreshEnvs()
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

const actions = [
  {
    title: 'Launch preview',
    blurb: 'Spin up an ephemeral app environment from a template or git repo.',
    to: '/launch',
    icon: 'rocket_launch',
  },
  {
    title: 'Provision infra',
    blurb: 'Generate Terraform or Pulumi for GCP, AWS, Azure, or kind.',
    to: '/provision',
    icon: 'schema',
  },
  {
    title: 'Browse catalog',
    blurb: 'Golden-path services with scorecards and onboarding.',
    to: '/catalog',
    icon: 'inventory_2',
  },
  {
    title: 'Cloud credentials',
    blurb: 'Store GCP, AWS, Azure, or Cloudflare keys for your account.',
    to: '/settings',
    icon: 'key',
  },
] as const
</script>

<template>
  <div class="animate-fade-up space-y-10">
    <header class="space-y-3">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        Launchpad
      </p>
      <h1 class="text-3xl font-semibold tracking-tight md:text-4xl">
        Welcome{{ user?.display_name ? `, ${user.display_name}` : '' }}
      </h1>
      <p class="max-w-2xl text-[var(--lp-muted)]">
        Governed ephemeral environments and multi-cloud infrastructure from one portal.
        Start a preview, provision a stack, or manage account cloud credentials.
      </p>
    </header>

    <section class="grid gap-4 sm:grid-cols-3">
      <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5">
        <p class="lp-label">Active environments</p>
        <p class="mt-2 font-mono text-3xl text-[var(--lp-text)]">
          {{ loading ? '-' : runningEnvs }}
        </p>
        <NuxtLink to="/environments" class="mt-3 inline-block text-sm text-[var(--lp-accent)] hover:underline">
          View all →
        </NuxtLink>
      </div>
      <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5">
        <p class="lp-label">Workspaces</p>
        <p class="mt-2 font-mono text-3xl text-[var(--lp-text)]">
          {{ loading ? '-' : workspaceCount }}
        </p>
        <NuxtLink to="/workspaces" class="mt-3 inline-block text-sm text-[var(--lp-accent)] hover:underline">
          Open workspaces →
        </NuxtLink>
      </div>
      <div class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5">
        <p class="lp-label">Account cloud keys</p>
        <p class="mt-2 text-lg font-medium text-[var(--lp-text)]">
          {{ loading ? '…' : cloudReady ? 'Configured' : 'Not set' }}
        </p>
        <p v-if="credStatus && !loading" class="mt-1 text-xs text-[var(--lp-muted)]">
          <span v-if="credStatus.has_gcp">GCP </span>
          <span v-if="credStatus.has_aws">AWS </span>
          <span v-if="credStatus.has_azure">Azure </span>
          <span v-if="credStatus.has_cloudflare">Cloudflare </span>
          <span v-if="!cloudReady">Add keys in Settings</span>
        </p>
        <NuxtLink to="/settings" class="mt-3 inline-block text-sm text-[var(--lp-accent)] hover:underline">
          Manage credentials →
        </NuxtLink>
      </div>
    </section>

    <section class="space-y-4">
      <h2 class="text-lg font-semibold">Quick actions</h2>
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

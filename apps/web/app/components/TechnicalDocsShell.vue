<script setup lang="ts">
export interface TechnicalNavItem {
  label: string
  to: string
  description: string
}

const items: TechnicalNavItem[] = [
  {
    label: 'Overview',
    to: '/bibirinbuluaremieye/technical',
    description: 'What Launchpad is and how the pieces fit',
  },
  {
    label: 'Architecture',
    to: '/bibirinbuluaremieye/technical/architecture',
    description: 'Monorepo, processes, and data stores',
  },
  {
    label: 'Frontend',
    to: '/bibirinbuluaremieye/technical/frontend',
    description: 'Nuxt app, pages, and composables',
  },
  {
    label: 'Backend',
    to: '/bibirinbuluaremieye/technical/backend',
    description: 'FastAPI layers, routers, and services',
  },
  {
    label: 'Core flows',
    to: '/bibirinbuluaremieye/technical/flows',
    description: 'Launch, rebuild, teardown, drift',
  },
  {
    label: 'Auth & orgs',
    to: '/bibirinbuluaremieye/technical/auth-orgs',
    description: 'JWT, OIDC, invites, and tenancy',
  },
  {
    label: 'Kubernetes',
    to: '/bibirinbuluaremieye/technical/kubernetes',
    description: 'kind, preview vs manifest deploy',
  },
  {
    label: 'Operations',
    to: '/bibirinbuluaremieye/technical/operations',
    description: 'Config, workers, and local runbook',
  },
]

const route = useRoute()

function isActive(path: string): boolean {
  if (path === '/bibirinbuluaremieye/technical') {
    return route.path === path
  }
  return route.path === path || route.path.startsWith(`${path}/`)
}
</script>

<template>
  <div class="grid gap-8 lg:grid-cols-[240px_1fr] animate-fade-up">
    <aside class="lg:sticky lg:top-24 lg:self-start space-y-4">
      <div>
        <p class="lp-label mb-1">Private notes</p>
        <p class="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--lp-muted)]">
          /bibirinbuluaremieye/technical
        </p>
      </div>
      <nav class="space-y-1">
        <NuxtLink
          v-for="item in items"
          :key="item.to"
          :to="item.to"
          class="block rounded-lg px-3 py-2 transition"
          :class="
            isActive(item.to)
              ? 'bg-[var(--lp-accent)]/10 text-[var(--lp-accent)]'
              : 'text-[var(--lp-muted)] hover:bg-[var(--lp-panel-2)] hover:text-[var(--lp-text)]'
          "
        >
          <span class="block text-sm font-medium">{{ item.label }}</span>
          <span class="mt-0.5 block text-xs leading-snug opacity-80">{{ item.description }}</span>
        </NuxtLink>
      </nav>
      <p class="text-[11px] leading-5 text-[var(--lp-muted)]">
        Not linked from the main sidebar. Bookmark this path if you want it back.
      </p>
    </aside>

    <article class="technical-prose min-w-0 space-y-10">
      <slot />
    </article>
  </div>
</template>

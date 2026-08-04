<script setup lang="ts">
const route = useRoute()
const { ready } = useAuth()

const bareShell = computed(() => {
  const path = route.path
  return path === '/' || path === '/login' || path.startsWith('/invite/')
})
</script>

<template>
  <div v-if="!ready" class="fixed inset-0 z-[9999] flex flex-col items-center justify-center bg-[var(--lp-ink)]">
    <div class="relative flex flex-col items-center animate-pulse">
      <div class="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-[var(--lp-accent)] to-blue-600 shadow-[0_0_40px_rgba(var(--lp-accent-rgb),0.4)]">
        <span class="material-symbols-outlined text-3xl text-[var(--lp-ink)]">rocket_launch</span>
      </div>
      <h1 class="text-3xl font-bold tracking-tight text-[var(--lp-text)]">Launchpad</h1>
      <p class="mt-2 font-mono text-xs uppercase tracking-[0.3em] text-[var(--lp-muted)]">Initializing Workspace</p>
    </div>
  </div>
  <template v-else>
    <div v-if="bareShell">
      <NuxtPage />
    </div>
    <AppShell v-else>
      <NuxtPage />
    </AppShell>
  </template>
</template>

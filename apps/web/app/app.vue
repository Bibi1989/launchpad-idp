<script setup lang="ts">
const route = useRoute()
const { ready } = useAuth()

const bareShell = computed(() => {
  const path = route.path
  return path === '/' || path === '/login' || path.startsWith('/invite/')
})
</script>

<template>
  <div v-if="!ready" class="lp-splash fixed inset-0 z-[9999] flex flex-col items-center justify-center">
    <div class="relative flex flex-col items-center animate-pulse">
      <BrandLogo
        size="lg"
        :show-wordmark="false"
        class="mb-6"
        :style="{ boxShadow: '0 0 40px var(--lp-splash-glow)' }"
      />
      <h1 class="text-3xl font-bold tracking-tight text-[var(--lp-text)]">{{ $t('brand.name') }}</h1>
      <p class="mt-2 font-mono text-xs uppercase tracking-[0.3em] text-[var(--lp-muted)]">{{ $t('shell.initializing') }}</p>
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
  <ToastHost />
</template>

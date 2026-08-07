<script setup lang="ts">
const route = useRoute()
const { ready } = useAuth()

const bareShell = computed(() => {
  const path = route.path
  return path === '/' || path === '/login' || path.startsWith('/invite/')
})
</script>

<template>
  <AppSplash v-if="!ready" fullscreen />
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

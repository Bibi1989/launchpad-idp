<script setup lang="ts">
const route = useRoute()

const bareShell = computed(() => {
  const path = route.path
  return path === '/' || path === '/login' || path.startsWith('/invite/')
})
</script>

<template>
  <div v-if="bareShell">
    <NuxtPage />
  </div>
  <AppShell v-else>
    <NuxtPage />
  </AppShell>
</template>

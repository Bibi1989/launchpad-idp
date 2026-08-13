<script setup lang="ts">
const route = useRoute()
const { ready } = useAuth()

const bareShell = computed(() => {
  const path = route.path
  return (
    path === '/'
    || path === '/login'
    || path.startsWith('/onboarding/')
    || path.startsWith('/invite/')
    || path.startsWith('/auth/')
    || path.startsWith('/p/')
  )
})
</script>

<template>
  <!--
    One AppShell + one NuxtPage for the whole app.
    bare = public routes only (login, invite, landing). Auth boot uses AppSplash
    overlay - do not flip bare on !ready or the main column loses its sidebar offset.
  -->
  <AppShell :bare="bareShell">
    <NuxtPage :key="route.fullPath" />
  </AppShell>
  <AppSplash v-if="!ready" fullscreen />
  <ToastHost />
</template>

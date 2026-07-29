<template>
  <div v-if="$route.path === '/login' || $route.path.startsWith('/invite/')">
    <NuxtPage />
  </div>
  <AppShell v-else>
    <NuxtPage />
  </AppShell>
</template>

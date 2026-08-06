<script setup lang="ts">
const { t } = useI18n()

// Import lives on the Workspaces page (GitHub / GitLab picker + detect + save).
await navigateTo('/workspaces?import=1')
</script>

<template>
  <div class="p-8 text-sm text-[var(--lp-muted)]">{{ t('import.redirecting') }}</div>
</template>

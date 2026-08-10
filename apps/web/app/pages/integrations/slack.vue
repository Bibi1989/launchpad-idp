<script setup lang="ts">
const { t } = useI18n()
</script>

<template>
  <div class="w-full space-y-8 animate-fade-up">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        {{ t('nav.integrations') }}
      </p>
      <h1 class="text-3xl font-semibold tracking-tight">{{ t('integrations.slack') }}</h1>
      <p class="text-sm text-[var(--lp-muted)]">
        {{ t('integrations.slackPageBlurb') }}
      </p>
    </header>

    <SlackConnectCard />

    <p class="text-sm text-[var(--lp-muted)]">
      <NuxtLink to="/integrations" class="text-[var(--lp-accent)] hover:underline">
        {{ t('integrations.backToHub') }}
      </NuxtLink>
    </p>
  </div>
</template>

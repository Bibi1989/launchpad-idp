<script setup lang="ts">
const { t } = useI18n()
</script>

<template>
  <div class="w-full space-y-8 animate-fade-up">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        {{ t('nav.integrations') }}
      </p>
      <h1 class="text-3xl font-semibold tracking-tight">{{ t('integrations.sourceControl') }}</h1>
      <p class="text-sm text-[var(--lp-muted)]">
        {{ t('integrations.sourceControlBlurb') }}
      </p>
    </header>

    <div class="grid gap-4 sm:grid-cols-2">
      <NuxtLink
        to="/integrations/github"
        class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-5 transition hover:border-[var(--lp-accent)]/40"
      >
        <p class="text-lg font-semibold">{{ t('integrations.github') }}</p>
        <p class="mt-2 text-sm text-[var(--lp-muted)]">
          {{ t('integrations.githubCardBlurb') }}
        </p>
      </NuxtLink>
      <NuxtLink
        to="/integrations/gitlab"
        class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-5 transition hover:border-[var(--lp-accent)]/40"
      >
        <p class="text-lg font-semibold">{{ t('integrations.gitlab') }}</p>
        <p class="mt-2 text-sm text-[var(--lp-muted)]">
          {{ t('integrations.gitlabCardBlurb') }}
        </p>
      </NuxtLink>
    </div>
  </div>
</template>

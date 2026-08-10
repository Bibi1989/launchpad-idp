<script setup lang="ts">
const { t } = useI18n()

const cards = [
  {
    to: '/integrations/github',
    titleKey: 'integrations.github',
    blurbKey: 'integrations.githubCardBlurb',
    brand: 'github' as const,
  },
  {
    to: '/integrations/gitlab',
    titleKey: 'integrations.gitlab',
    blurbKey: 'integrations.gitlabCardBlurb',
    brand: 'gitlab' as const,
  },
  {
    to: '/integrations/slack',
    titleKey: 'integrations.slack',
    blurbKey: 'integrations.slackCardBlurb',
    brand: 'slack' as const,
  },
  {
    to: '/integrations/jira',
    titleKey: 'integrations.jira',
    blurbKey: 'integrations.jiraCardBlurb',
    brand: 'jira' as const,
  },
]

const sourceCards = cards.slice(0, 2)
const collabCards = cards.slice(2)
</script>

<template>
  <div class="w-full space-y-8 animate-fade-up">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        {{ t('nav.integrations') }}
      </p>
      <h1 class="text-3xl font-semibold tracking-tight">{{ t('integrations.hubTitle') }}</h1>
      <p class="text-sm text-[var(--lp-muted)]">
        {{ t('integrations.hubBlurb') }}
      </p>
    </header>

    <div>
      <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
        {{ t('integrations.sourceControl') }}
      </h2>
      <div class="grid gap-4 sm:grid-cols-2">
        <NuxtLink
          v-for="card in sourceCards"
          :key="card.to"
          :to="card.to"
          class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-5 transition hover:border-[var(--lp-accent)]/40"
        >
          <div class="flex items-start gap-3">
            <IntegrationBrandIcon :brand="card.brand" />
            <div class="min-w-0">
              <p class="text-lg font-semibold">{{ t(card.titleKey) }}</p>
              <p class="mt-2 text-sm text-[var(--lp-muted)]">
                {{ t(card.blurbKey) }}
              </p>
            </div>
          </div>
        </NuxtLink>
      </div>
    </div>

    <div>
      <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--lp-muted)]">
        {{ t('integrations.collaboration') }}
      </h2>
      <div class="grid gap-4 sm:grid-cols-2">
        <NuxtLink
          v-for="card in collabCards"
          :key="card.to"
          :to="card.to"
          class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-5 transition hover:border-[var(--lp-accent)]/40"
        >
          <div class="flex items-start gap-3">
            <IntegrationBrandIcon :brand="card.brand" />
            <div class="min-w-0">
              <p class="text-lg font-semibold">{{ t(card.titleKey) }}</p>
              <p class="mt-2 text-sm text-[var(--lp-muted)]">
                {{ t(card.blurbKey) }}
              </p>
            </div>
          </div>
        </NuxtLink>
      </div>
    </div>
  </div>
</template>

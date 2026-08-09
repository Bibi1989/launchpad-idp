<script setup lang="ts">
import type { GitHubAppStatus } from '~/types/provisioning'

const { t } = useI18n()
const status = ref<GitHubAppStatus | null>(null)

function onUpdated(next: GitHubAppStatus) {
  status.value = next
}
</script>

<template>
  <div class="w-full space-y-8 animate-fade-up">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        {{ t('nav.integrations') }}
      </p>
      <h1 class="text-3xl font-semibold tracking-tight">{{ t('integrations.github') }}</h1>
      <p class="text-sm text-[var(--lp-muted)]">
        {{ t('integrations.githubBlurb') }}
      </p>
    </header>

    <GithubConnectCard @updated="onUpdated" />

    <section v-if="status?.configured" class="lp-panel space-y-3 p-5 text-sm text-[var(--lp-muted)]">
      <h2 class="text-base font-semibold text-[var(--lp-text)]">{{ t('integrations.nextSteps') }}</h2>
      <ol class="list-decimal space-y-2 pl-5">
        <li>
          {{ t('integrations.githubStep1') }}
          <NuxtLink to="/provision" class="text-[var(--lp-accent)] hover:underline">{{ t('nav.provision') }}</NuxtLink>.
        </li>
        <li>{{ t('integrations.githubStep2') }}</li>
        <li>
          {{ t('integrations.githubStep3') }}
          <NuxtLink to="/workspaces" class="text-[var(--lp-accent)] hover:underline">{{ t('nav.workspaces') }}</NuxtLink>.
        </li>
      </ol>
    </section>

    <p class="text-sm text-[var(--lp-muted)]">
      {{ t('integrations.needCloudCreds') }}
      <NuxtLink to="/docs" class="text-[var(--lp-accent)] hover:underline">{{ t('common.readDocs') }}</NuxtLink>.
    </p>
  </div>
</template>

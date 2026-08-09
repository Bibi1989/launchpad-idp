<script setup lang="ts">
import type { GitlabStatus } from '~/types/provisioning'

const { t } = useI18n()
const status = ref<GitlabStatus | null>(null)

function onUpdated(next: GitlabStatus) {
  status.value = next
}
</script>

<template>
  <div class="w-full space-y-8 animate-fade-up">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        {{ t('nav.integrations') }}
      </p>
      <h1 class="text-3xl font-semibold tracking-tight">{{ t('integrations.gitlab') }}</h1>
      <p class="text-sm text-[var(--lp-muted)]">
        {{ t('integrations.gitlabPageBlurb') }}
      </p>
    </header>

    <GitlabConnectCard @updated="onUpdated" />

    <section v-if="status?.connected" class="lp-panel space-y-3 p-5 text-sm text-[var(--lp-muted)]">
      <h2 class="text-base font-semibold text-[var(--lp-text)]">{{ t('integrations.nextSteps') }}</h2>
      <ol class="list-decimal space-y-2 pl-5">
        <li>
          {{ t('integrations.gitlabStep1') }}
          <NuxtLink to="/workspaces" class="text-[var(--lp-accent)] hover:underline">{{ t('nav.workspaces') }}</NuxtLink>
          {{ t('integrations.gitlabPublish') }}
        </li>
        <li>{{ t('integrations.gitlabStep2') }}</li>
      </ol>
    </section>

    <p class="text-sm text-[var(--lp-muted)]">
      {{ t('integrations.preferGithub') }}
      <NuxtLink to="/integrations/github" class="text-[var(--lp-accent)] hover:underline">{{ t('integrations.connectGithub') }}</NuxtLink>
    </p>
  </div>
</template>

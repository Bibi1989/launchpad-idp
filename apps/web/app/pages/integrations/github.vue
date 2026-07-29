<script setup lang="ts">
import type { GitHubAppStatus } from '~/types/provisioning'

const status = ref<GitHubAppStatus | null>(null)

function onUpdated(next: GitHubAppStatus) {
  status.value = next
}
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-8 animate-fade-up">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        Integrations
      </p>
      <h1 class="text-3xl font-semibold tracking-tight">GitHub</h1>
      <p class="text-sm text-[var(--lp-muted)]">
        Authorize Launchpad to create repositories and push CI workflows on your behalf. You will be
        redirected to GitHub to install the application, then returned here.
      </p>
    </header>

    <GithubConnectCard @updated="onUpdated" />

    <section v-if="status?.configured" class="lp-panel space-y-3 p-5 text-sm text-[var(--lp-muted)]">
      <h2 class="text-base font-semibold text-[var(--lp-text)]">Next steps</h2>
      <ol class="list-decimal space-y-2 pl-5">
        <li>
          Create a cloud workspace in
          <NuxtLink to="/provision" class="text-[var(--lp-accent)] hover:underline">Provision</NuxtLink>.
        </li>
        <li>On the GitHub step, pick an installation and create the repo + workflow.</li>
        <li>
          Or reopen an existing workspace from
          <NuxtLink to="/workspaces" class="text-[var(--lp-accent)] hover:underline">Workspaces</NuxtLink>.
        </li>
      </ol>
    </section>

    <p class="text-sm text-[var(--lp-muted)]">
      Need credentials for GCP/AWS first?
      <NuxtLink to="/docs" class="text-[var(--lp-accent)] hover:underline">Read the docs</NuxtLink>.
    </p>
  </div>
</template>

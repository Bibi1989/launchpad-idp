<script setup lang="ts">
import type { GitlabStatus } from '~/types/provisioning'

const status = ref<GitlabStatus | null>(null)

function onUpdated(next: GitlabStatus) {
  status.value = next
}
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-8 animate-fade-up">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        Integrations
      </p>
      <h1 class="text-3xl font-semibold tracking-tight">GitLab</h1>
      <p class="text-sm text-[var(--lp-muted)]">
        Connect GitLab via OAuth or a Personal Access Token to create projects and push workspace
        files with the same directory layout you see in Launchpad.
      </p>
    </header>

    <GitlabConnectCard @updated="onUpdated" />

    <section v-if="status?.connected" class="lp-panel space-y-3 p-5 text-sm text-[var(--lp-muted)]">
      <h2 class="text-base font-semibold text-[var(--lp-text)]">Next steps</h2>
      <ol class="list-decimal space-y-2 pl-5">
        <li>
          Open a
          <NuxtLink to="/workspaces" class="text-[var(--lp-accent)] hover:underline">workspace</NuxtLink>
          and use Publish → GitLab.
        </li>
        <li>Only files visible in the workspace tree are committed (no path remapping).</li>
      </ol>
    </section>

    <p class="text-sm text-[var(--lp-muted)]">
      Prefer GitHub?
      <NuxtLink to="/integrations/github" class="text-[var(--lp-accent)] hover:underline">Connect GitHub</NuxtLink>
    </p>
  </div>
</template>

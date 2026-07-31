<script setup lang="ts">
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-8 animate-fade-up">
    <header class="space-y-2">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        Integrations
      </p>
      <h1 class="text-3xl font-semibold tracking-tight">Source control</h1>
      <p class="text-sm text-[var(--lp-muted)]">
        Connect GitHub or GitLab so Launchpad can create repos and push the exact workspace tree.
      </p>
    </header>

    <div class="grid gap-4 sm:grid-cols-2">
      <NuxtLink
        to="/integrations/github"
        class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-5 transition hover:border-[var(--lp-accent)]/40"
      >
        <p class="text-lg font-semibold">GitHub</p>
        <p class="mt-2 text-sm text-[var(--lp-muted)]">
          GitHub App install for repository create and workflow push.
        </p>
      </NuxtLink>
      <NuxtLink
        to="/integrations/gitlab"
        class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)]/60 p-5 transition hover:border-[var(--lp-accent)]/40"
      >
        <p class="text-lg font-semibold">GitLab</p>
        <p class="mt-2 text-sm text-[var(--lp-muted)]">
          OAuth or Personal Access Token for project create and mirrored file push.
        </p>
      </NuxtLink>
    </div>
  </div>
</template>

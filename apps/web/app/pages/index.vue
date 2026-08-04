<script setup lang="ts">
definePageMeta({
  layout: false,
})

const { token, ready } = useAuth()

const signedIn = computed(() => Boolean(ready.value && token.value))

const primaryCta = computed(() =>
  signedIn.value
    ? { to: '/launch', label: 'Get started' }
    : { to: '/login?next=/launch', label: 'Get started' },
)

const features = [
  {
    title: 'Ephemeral previews',
    blurb: 'Spin up governed app environments from a repo or catalog template, then tear them down on TTL.',
  },
  {
    title: 'Multi-cloud IaC',
    blurb: 'Generate Terraform or Pulumi for GCP, AWS, Azure, Cloudflare, or local Sandbox, then apply from a sandbox.',
  },
  {
    title: 'Kubernetes workloads',
    blurb: 'Edit hardened manifests, apply to your cluster, and operate pods with describe, logs, and exec.',
  },
] as const
</script>

<template>
  <div class="relative min-h-screen overflow-hidden">
    <div
      class="pointer-events-none absolute inset-0 opacity-40"
      aria-hidden="true"
      style="
        background:
          radial-gradient(ellipse 80% 50% at 50% -20%, rgba(45, 212, 191, 0.22), transparent 55%),
          radial-gradient(ellipse 40% 30% at 90% 60%, rgba(15, 118, 110, 0.15), transparent 50%);
      "
    />

    <header class="relative z-10 mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
      <div class="flex items-baseline gap-2">
        <span class="text-xl font-semibold tracking-tight text-[var(--lp-accent)]">Launchpad</span>
        <span class="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--lp-muted)]">IDP</span>
      </div>
      <nav class="flex items-center gap-4 text-sm">
        <NuxtLink
          to="/docs"
          class="text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
        >
          Docs
        </NuxtLink>
        <NuxtLink
          v-if="signedIn"
          to="/home"
          class="text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
        >
          Open hub
        </NuxtLink>
        <NuxtLink
          v-else
          to="/login"
          class="text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
        >
          Sign in
        </NuxtLink>
      </nav>
    </header>

    <main class="relative z-10 mx-auto flex max-w-6xl flex-col px-6 pb-24 pt-16 md:pt-24">
      <section class="max-w-3xl animate-fade-up space-y-8">
        <p class="font-mono text-xs uppercase tracking-[0.28em] text-[var(--lp-accent)]">
          Internal developer portal
        </p>
        <h1 class="text-5xl font-semibold tracking-tight text-[var(--lp-text)] md:text-6xl md:leading-[1.05]">
          Launchpad
        </h1>
        <p class="max-w-xl text-lg leading-relaxed text-[var(--lp-muted)] md:text-xl">
          Governed ephemeral environments and multi-cloud infrastructure from one portal -
          provision, ship manifests, and launch previews without leaving the control plane.
        </p>
        <div class="flex flex-wrap items-center gap-4 pt-2">
          <NuxtLink
            :to="primaryCta.to"
            class="lp-btn-primary inline-flex items-center gap-2 px-6 py-3 text-sm font-medium transition hover:brightness-110"
          >
            {{ primaryCta.label }}
            <span class="material-symbols-outlined text-base">arrow_forward</span>
          </NuxtLink>
          <NuxtLink
            to="/docs"
            class="lp-btn-ghost inline-flex items-center gap-2 px-5 py-3 text-sm"
          >
            Read the docs
          </NuxtLink>
        </div>
      </section>

      <section class="mt-24 grid gap-10 border-t border-[var(--lp-line)] pt-16 md:grid-cols-3 md:gap-12">
        <div
          v-for="(feature, index) in features"
          :key="feature.title"
          class="animate-fade-up space-y-3"
          :style="{ animationDelay: `${(index + 1) * 80}ms` }"
        >
          <h2 class="text-lg font-semibold text-[var(--lp-text)]">{{ feature.title }}</h2>
          <p class="text-sm leading-relaxed text-[var(--lp-muted)]">{{ feature.blurb }}</p>
        </div>
      </section>
    </main>

    <footer class="relative z-10 mx-auto max-w-6xl border-t border-[var(--lp-line)] px-6 py-8">
      <div class="flex flex-wrap items-center justify-between gap-4 text-sm text-[var(--lp-muted)]">
        <p class="font-mono text-xs">Launchpad IDP</p>
        <div class="flex gap-6">
          <NuxtLink to="/docs#getting-started" class="hover:text-[var(--lp-text)]">Getting started</NuxtLink>
          <a
            href="http://localhost:8000/docs"
            target="_blank"
            rel="noopener noreferrer"
            class="hover:text-[var(--lp-text)]"
          >
            API reference
          </a>
        </div>
      </div>
    </footer>
  </div>
</template>

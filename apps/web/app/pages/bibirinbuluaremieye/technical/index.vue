<script setup lang="ts">
useHead({
  title: 'Technical notes · Launchpad',
  meta: [{ name: 'robots', content: 'noindex, nofollow' }],
})
</script>

<template>
  <TechnicalDocsShell>
    <header class="space-y-3">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">
        Internal · unlisted
      </p>
      <h1 class="text-3xl font-semibold tracking-tight md:text-4xl">
        How Launchpad is built
      </h1>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        This is a private technical walkthrough of the Launchpad Internal Developer Portal -
        written in plain language, with diagrams and concrete examples. It is
        <strong class="text-[var(--lp-text)]">not</strong>
        linked from the product navigation. The path is only useful if you know it.
      </p>
    </header>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">The short version</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Launchpad is two apps that talk to each other:
      </p>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <strong class="text-[var(--lp-text)]">Web UI</strong>
          (<code class="font-mono text-xs">apps/web</code>) - a Nuxt 4 frontend where developers
          launch previews, provision cloud workspaces, and watch logs.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">Control plane</strong>
          (<code class="font-mono text-xs">apps/api</code>) - a FastAPI backend that stores state,
          queues long jobs on Celery, talks to Kubernetes, and streams status over Redis.
        </li>
      </ul>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Think of the UI as a remote control, and the API + workers as the machine room. When you
        click <em>Launch</em>, the UI does not create Kubernetes namespaces itself - it asks the
        API to queue a job, then listens for live updates.
      </p>
    </section>

    <MermaidDiagram
      title="Big picture"
      code="
flowchart LR
  Dev[You in the browser]
  UI[Nuxt UI :3000]
  API[FastAPI :8000]
  W[Celery worker]
  PG[(Postgres)]
  R[(Redis)]
  K[kind / Kubernetes]

  Dev --> UI
  UI -->|REST + SSE| API
  API --> PG
  API --> R
  API --> W
  W --> PG
  W --> R
  W --> K
  R -->|live events| API
"
    />

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Three product jobs</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        The same portal does three related but separate jobs. Mixing them up is the most common
        source of confusion:
      </p>
      <div class="grid gap-3 md:grid-cols-3">
        <div class="lp-glass rounded-xl p-4 space-y-2">
          <p class="font-mono text-[10px] uppercase tracking-wider text-[var(--lp-accent)]">1</p>
          <h3 class="font-semibold">Environments</h3>
          <p class="text-xs leading-6 text-[var(--lp-muted)]">
            Short-lived app previews from a git branch. Isolated namespace, TTL, destroy when done.
          </p>
        </div>
        <div class="lp-glass rounded-xl p-4 space-y-2">
          <p class="font-mono text-[10px] uppercase tracking-wider text-[var(--lp-accent)]">2</p>
          <h3 class="font-semibold">Provision</h3>
          <p class="text-xs leading-6 text-[var(--lp-muted)]">
            Generate Terraform/Pulumi workspaces for cloud (or kind). Credentials stay encrypted.
          </p>
        </div>
        <div class="lp-glass rounded-xl p-4 space-y-2">
          <p class="font-mono text-[10px] uppercase tracking-wider text-[var(--lp-accent)]">3</p>
          <h3 class="font-semibold">Manifests</h3>
          <p class="text-xs leading-6 text-[var(--lp-muted)]">
            Apply your own K8s YAML from
            <code class="font-mono">infra/k8s/manifests/</code>
            onto a preview namespace.
          </p>
        </div>
      </div>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Example: launching a local preview</h2>
      <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>You open <code class="font-mono text-xs">/launch</code> and choose Local + a template.</li>
        <li>
          The UI calls <code class="font-mono text-xs">POST /api/v1/preview/launch</code> with your
          JWT and active org header.
        </li>
        <li>
          The API creates an <code class="font-mono text-xs">environments</code> row with status
          <code class="font-mono text-xs">PROVISIONING</code> and enqueues a Celery task.
        </li>
        <li>
          The worker may build a Docker image, then applies either the built-in preview profile or
          workspace manifests into kind.
        </li>
        <li>
          Status events go to Redis; the environment page listens on SSE and flips to
          <code class="font-mono text-xs">RUNNING</code> with an Open app URL.
        </li>
      </ol>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Read next</h2>
      <ul class="space-y-2 text-sm">
        <li>
          <NuxtLink
            to="/bibirinbuluaremieye/technical/architecture"
            class="text-[var(--lp-accent)] hover:underline"
          >
            Architecture
          </NuxtLink>
          - folders, processes, and how data moves.
        </li>
        <li>
          <NuxtLink
            to="/bibirinbuluaremieye/technical/flows"
            class="text-[var(--lp-accent)] hover:underline"
          >
            Core flows
          </NuxtLink>
          - launch, GitOps rebuild, teardown, drift.
        </li>
        <li>
          <NuxtLink
            to="/bibirinbuluaremieye/technical/operations"
            class="text-[var(--lp-accent)] hover:underline"
          >
            Operations
          </NuxtLink>
          - how to run it locally day to day.
        </li>
      </ul>
    </section>
  </TechnicalDocsShell>
</template>

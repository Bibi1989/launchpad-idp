<script setup lang="ts">
useHead({
  title: 'Architecture · Technical notes',
  meta: [{ name: 'robots', content: 'noindex, nofollow' }],
})
</script>

<template>
  <TechnicalDocsShell>
    <header class="space-y-3">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">Architecture</p>
      <h1 class="text-3xl font-semibold tracking-tight">Monorepo and runtime shape</h1>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Launchpad lives in one git repo with two applications and a few supporting folders. This
        page explains what each folder does and which processes you run while developing.
      </p>
    </header>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Folder map</h2>
      <pre class="lp-glass overflow-x-auto rounded-xl p-4 font-mono text-xs leading-6 text-[var(--lp-muted)]">launchpad/
├── apps/
│   ├── web/     # Nuxt 4 UI (browser)
│   └── api/     # FastAPI + Celery (control plane)
├── scripts/     # kind-up.sh / kind-down.sh
├── infra/       # sample / shared IaC bits
├── docker-compose.yml
└── Makefile</pre>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Everyday work almost always happens under
        <code class="font-mono text-xs">apps/web</code> and
        <code class="font-mono text-xs">apps/api</code>. Compose brings up Postgres and Redis so
        those apps have somewhere to store data and queue jobs.
      </p>
    </section>

    <MermaidDiagram
      title="Processes you run locally"
      code="
flowchart TB
  subgraph Always
    PG[(Postgres :5432)]
    RD[(Redis :6379)]
  end
  subgraph Usually
    API[uvicorn API :8000]
    WEB[Nuxt :3000]
    WRK[Celery worker]
  end
  subgraph Often
    BEAT[Celery beat]
    KIND[kind cluster]
  end
  WEB --> API
  API --> PG
  API --> RD
  WRK --> PG
  WRK --> RD
  WRK --> KIND
  BEAT --> RD
"
    />

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Why so many processes?</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Creating a Kubernetes preview can take minutes (clone, Docker build, apply manifests).
        If the API did that work inside the HTTP request, your browser would time out and a crash
        would lose the job.
      </p>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        So the API does the
        <strong class="text-[var(--lp-text)]">quick bookkeeping</strong>
        (create a row, check permissions, enqueue), and Celery does the
        <strong class="text-[var(--lp-text)]">heavy lifting</strong>
        in the background. Redis is the mailbox between them. Celery beat is the alarm clock for
        periodic chores (TTL reaper, drift scan).
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Data stores in one sentence each</h2>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <strong class="text-[var(--lp-text)]">Postgres</strong>
          — source of truth for users, orgs, environments, workspaces, audits, invites.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">Redis</strong>
          — Celery queues, distributed locks, and live
          <code class="font-mono text-xs">env_channel:{id}</code> pub/sub for SSE.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">Disk workspace root</strong>
          (<code class="font-mono text-xs">IAC_WORKSPACE_ROOT</code>) — generated Terraform/Pulumi
          and manifest files for each provisioning workspace.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">Kubernetes</strong>
          — where preview namespaces actually run (or simulated when
          <code class="font-mono text-xs">KUBERNETES_ENABLED=false</code>).
        </li>
      </ul>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Backend layering (mental model)</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Inside
        <code class="font-mono text-xs">apps/api/app/</code>
        code is arranged like a sandwich:
      </p>
      <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <strong class="text-[var(--lp-text)]">Routers</strong>
          — HTTP endpoints (thin). Example:
          <code class="font-mono text-xs">routers/api.py</code>.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">Services</strong>
          — business rules. Example:
          <code class="font-mono text-xs">services/environment.py</code>.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">Repositories</strong>
          — database queries. Example:
          <code class="font-mono text-xs">repositories/environment.py</code>.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">Models / schemas</strong>
          — SQLAlchemy tables and Pydantic request/response shapes.
        </li>
      </ol>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Workers call the same services and repositories; they just start from a Celery task instead
        of an HTTP handler.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Useful Make targets</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead class="font-mono text-[10px] uppercase tracking-wider text-[var(--lp-muted)]">
            <tr>
              <th class="py-2 pr-4">Target</th>
              <th class="py-2">What it does</th>
            </tr>
          </thead>
          <tbody class="text-[var(--lp-muted)]">
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">make up</td>
              <td class="py-2">Start Postgres + Redis (+ Adminer)</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">make migrate</td>
              <td class="py-2">Apply Alembic migrations</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">make api</td>
              <td class="py-2">Run FastAPI on :8000</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">make worker</td>
              <td class="py-2">Run Celery worker</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">make beat</td>
              <td class="py-2">Run Celery beat (TTL + drift)</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">make web</td>
              <td class="py-2">Run Nuxt on :3000</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">make kind-up</td>
              <td class="py-2">Create local kind cluster</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </TechnicalDocsShell>
</template>

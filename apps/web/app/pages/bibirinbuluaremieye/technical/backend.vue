<script setup lang="ts">
useHead({
  title: 'Backend · Technical notes',
  meta: [{ name: 'robots', content: 'noindex, nofollow' }],
})
</script>

<template>
  <TechnicalDocsShell>
    <header class="space-y-3">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">Backend</p>
      <h1 class="text-3xl font-semibold tracking-tight">FastAPI control plane</h1>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Everything under
        <code class="font-mono text-xs">apps/api</code>
        is the control plane: HTTP API, background workers, and integrations (GitHub, K8s, SMTP).
      </p>
    </header>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Entry point</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        <code class="font-mono text-xs">app/main.py</code>
        builds the FastAPI app, mounts routers at
        <code class="font-mono text-xs">/api/v1</code>,
        enables CORS for the Nuxt origin, and attaches a correlation-ID middleware so one click in
        the UI can be traced across logs.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Router map (examples)</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead class="font-mono text-[10px] uppercase tracking-wider text-[var(--lp-muted)]">
            <tr>
              <th class="py-2 pr-4">Prefix</th>
              <th class="py-2">File</th>
              <th class="py-2">Owns</th>
            </tr>
          </thead>
          <tbody class="text-[var(--lp-muted)]">
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/auth</td>
              <td class="py-2 font-mono text-xs">routers/auth.py</td>
              <td class="py-2">Login, register, OIDC, /me</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/orgs</td>
              <td class="py-2 font-mono text-xs">routers/orgs.py</td>
              <td class="py-2">Members, invites, SSO maps, costs</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/</td>
              <td class="py-2 font-mono text-xs">routers/api.py</td>
              <td class="py-2">Environments, preview launch, SSE, drift</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/provisioning</td>
              <td class="py-2 font-mono text-xs">routers/provisioning.py</td>
              <td class="py-2">Workspaces, files, GitHub push</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/webhooks</td>
              <td class="py-2 font-mono text-xs">routers/webhooks.py</td>
              <td class="py-2">GitHub push → rebuild</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Example: create environment request</h2>
      <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>Router validates the body with a Pydantic schema.</li>
        <li>
          <code class="font-mono text-xs">EnvironmentService</code>
          checks org access, concurrency limits, soft cost cap, and name uniqueness.
        </li>
        <li>Repository inserts a row with status PROVISIONING.</li>
        <li>
          Service enqueues
          <code class="font-mono text-xs">launchpad.provision_environment</code>.
        </li>
        <li>API returns 202 with the environment JSON; the worker continues offline.</li>
      </ol>
    </section>

    <MermaidDiagram
      title="Layers for one HTTP call"
      code="
flowchart TB
  R[Router]
  S[Service]
  Repo[Repository]
  DB[(Postgres)]
  Q[Celery / Redis]

  R --> S
  S --> Repo
  Repo --> DB
  S --> Q
"
    />

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Workers and beat</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Defined in
        <code class="font-mono text-xs">app/workers/celery_app.py</code>
        and
        <code class="font-mono text-xs">tasks.py</code>:
      </p>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <code class="font-mono text-xs">provision_environment</code>
          - VALIDATE → PLAN → optional BUILD → APPLY
        </li>
        <li>
          <code class="font-mono text-xs">rebuild_environment</code>
          - GitOps push rebuild
        </li>
        <li>
          <code class="font-mono text-xs">teardown_environment</code>
          - delete namespace, mark DESTROYED, free the unique name
        </li>
        <li>
          Beat:
          <code class="font-mono text-xs">reap_expired_environments</code>
          and
          <code class="font-mono text-xs">scan_preview_drift</code>
        </li>
      </ul>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        A Redis
        <strong class="text-[var(--lp-text)]">state lock</strong>
        prevents two workers from provisioning and tearing down the same environment at once.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Database migrations</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Schema changes go through Alembic under
        <code class="font-mono text-xs">apps/api/alembic/versions/</code>.
        Examples: org RBAC (
        <code class="font-mono text-xs">0010</code>),
        invites/SSO (
        <code class="font-mono text-xs">0012</code>),
        releasing destroyed environment names (
        <code class="font-mono text-xs">0013</code>).
        Always
        <code class="font-mono text-xs">make migrate</code>
        after pulling.
      </p>
    </section>
  </TechnicalDocsShell>
</template>

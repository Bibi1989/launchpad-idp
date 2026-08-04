<script setup lang="ts">
useHead({
  title: 'Operations · Technical notes',
  meta: [{ name: 'robots', content: 'noindex, nofollow' }],
})
</script>

<template>
  <TechnicalDocsShell>
    <header class="space-y-3">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">Operations</p>
      <h1 class="text-3xl font-semibold tracking-tight">Running and configuring Launchpad</h1>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        A practical runbook for local development, plus the categories of environment variables that
        shape behavior. Secret values stay in
        <code class="font-mono text-xs">.env</code>
        - this page only names the knobs.
      </p>
    </header>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Suggested local boot order</h2>
      <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li><code class="font-mono text-xs">make up</code> - Postgres + Redis</li>
        <li><code class="font-mono text-xs">make migrate</code> - schema</li>
        <li><code class="font-mono text-xs">make api</code> - control plane</li>
        <li><code class="font-mono text-xs">make worker</code> - jobs</li>
        <li><code class="font-mono text-xs">make beat</code> - TTL + drift timers (optional but useful)</li>
        <li><code class="font-mono text-xs">make web</code> - UI</li>
        <li>
          For real Local previews:
          <code class="font-mono text-xs">make kind-up</code>
          and set
          <code class="font-mono text-xs">KUBERNETES_ENABLED=true</code>
        </li>
      </ol>
    </section>

    <MermaidDiagram
      title="Healthy local stack"
      code="
flowchart LR
  W[make web] --> A[make api]
  A --> P[(postgres)]
  A --> R[(redis)]
  C[make worker] --> R
  C --> P
  B[make beat] --> R
  C --> K[kind]
"
    />

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Config categories</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead class="font-mono text-[10px] uppercase tracking-wider text-[var(--lp-muted)]">
            <tr>
              <th class="py-2 pr-4">Category</th>
              <th class="py-2">Examples</th>
            </tr>
          </thead>
          <tbody class="text-[var(--lp-muted)]">
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 text-[var(--lp-text)]">Data plane</td>
              <td class="py-2 font-mono text-xs">DATABASE_URL, REDIS_URL, CELERY_*</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 text-[var(--lp-text)]">Kubernetes</td>
              <td class="py-2 font-mono text-xs">KUBERNETES_ENABLED, KUBERNETES_CONTEXT</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 text-[var(--lp-text)]">Preview governance</td>
              <td class="py-2 font-mono text-xs">MAX_CONCURRENT_*, PREVIEW_SOFT_COST_CAP, TTL_*</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 text-[var(--lp-text)]">Drift</td>
              <td class="py-2 font-mono text-xs">DRIFT_SCAN_ENABLED, DRIFT_SCAN_INTERVAL_SECONDS</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 text-[var(--lp-text)]">Image build</td>
              <td class="py-2 font-mono text-xs">PREVIEW_BUILD_ENABLED, PREVIEW_IMAGE_REGISTRY</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 text-[var(--lp-text)]">Auth / OIDC</td>
              <td class="py-2 font-mono text-xs">JWT_*, OIDC_*, OIDC_GROUP_CLAIM</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 text-[var(--lp-text)]">Invites / mail</td>
              <td class="py-2 font-mono text-xs">INVITE_BASE_URL, SMTP_*</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 text-[var(--lp-text)]">GitHub / webhooks</td>
              <td class="py-2 font-mono text-xs">GITHUB_APP_*, WEBHOOK_SECRET</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Canonical list:
        <code class="font-mono text-xs">apps/api/.env.example</code>
        and
        <code class="font-mono text-xs">apps/api/app/core/config.py</code>.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Observability habits</h2>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>Follow one correlation ID from browser → API log → worker log.</li>
        <li>
          Read the environment
          <strong class="text-[var(--lp-text)]">audit trail</strong>
          for provision / rebuild / teardown / drift events.
        </li>
        <li>
          Soft cost cap and concurrent env limits return structured error codes the UI can show.
        </li>
      </ul>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Tests worth knowing</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        API:
        <code class="font-mono text-xs">cd apps/api && .venv/bin/pytest</code>
        - covers auth, org RBAC, drift, invites/SSO, name reuse after destroy, preview build, etc.
        Web:
        <code class="font-mono text-xs">npm test</code>
        under
        <code class="font-mono text-xs">apps/web</code>.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">This private docs path</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Bookmark
        <code class="font-mono text-xs">/bibirinbuluaremieye/technical</code>.
        Pages send
        <code class="font-mono text-xs">noindex</code>
        and are absent from
        <code class="font-mono text-xs">AppShell</code>
        navigation. Anyone who already has a Launchpad login could open the URL if they guessed it -
        secrecy is obscurity, not an ACL. Treat the content accordingly.
      </p>
    </section>
  </TechnicalDocsShell>
</template>

<script setup lang="ts">
useHead({
  title: 'Core flows · Technical notes',
  meta: [{ name: 'robots', content: 'noindex, nofollow' }],
})
</script>

<template>
  <TechnicalDocsShell>
    <header class="space-y-3">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">Core flows</p>
      <h1 class="text-3xl font-semibold tracking-tight">Lifecycle stories</h1>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        These are the paths that matter day to day. Each section walks through who does what, in
        simple order, with a diagram.
      </p>
    </header>

    <section class="space-y-4">
      <h2 class="text-xl font-semibold">1. Launch a preview</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        <strong class="text-[var(--lp-text)]">Goal:</strong>
        turn a git repo + branch into a temporary URL you can click.
      </p>
      <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>UI posts to <code class="font-mono text-xs">/preview/launch</code>.</li>
        <li>
          For Local, API may auto-create kind (
          <code class="font-mono text-xs">KIND_AUTO_MANAGE</code>).
        </li>
        <li>Row created as PROVISIONING; Celery task starts.</li>
        <li>
          Optional Docker build (
          <code class="font-mono text-xs">PREVIEW_BUILD_ENABLED</code>).
        </li>
        <li>
          APPLY: either built-in preview Deployment/Service, or workspace manifests.
        </li>
        <li>Status RUNNING + NodePort / portal URL; UI hears it over SSE.</li>
      </ol>
      <MermaidDiagram
        title="Launch sequence"
        code="
sequenceDiagram
  participant UI
  participant API
  participant Celery
  participant K8s
  UI->>API: POST /preview/launch
  API->>API: insert env PROVISIONING
  API->>Celery: enqueue provision
  API-->>UI: 202 + env id
  Celery->>Celery: BUILD?
  Celery->>K8s: APPLY
  Celery->>API: publish RUNNING via Redis
  UI->>API: SSE subscribe
"
      />
    </section>

    <section class="space-y-4">
      <h2 class="text-xl font-semibold">2. Git push rebuild (GitOps)</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        <strong class="text-[var(--lp-text)]">Goal:</strong>
        when someone pushes to the branch behind a running preview, refresh that preview.
      </p>
      <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>GitHub sends a push webhook to <code class="font-mono text-xs">/webhooks/github</code>.</li>
        <li>API checks HMAC signature against <code class="font-mono text-xs">WEBHOOK_SECRET</code>.</li>
        <li>Finds RUNNING environments matching repo + branch.</li>
        <li>Enqueues <code class="font-mono text-xs">rebuild_environment</code> (skips if locked).</li>
      </ol>
      <MermaidDiagram
        title="Webhook rebuild"
        code='
flowchart TD
  GH[GitHub push] --> WH["/webhooks/github"]
  WH --> OK{"HMAC ok?"}
  OK -->|no| Reject[400]
  OK -->|yes| Match["Match envs by repo+branch"]
  Match --> Q[enqueue rebuild]
  Q --> Apply["BUILD then re-APPLY"]
'
      />
    </section>

    <section class="space-y-4">
      <h2 class="text-xl font-semibold">3. Destroy / teardown</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        <strong class="text-[var(--lp-text)]">Goal:</strong>
        remove the namespace and free the environment name so you can relaunch the same name.
      </p>
      <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>UI calls <code class="font-mono text-xs">DELETE /environments/{id}</code>.</li>
        <li>Status becomes TEARDOWN_PENDING; teardown task is queued.</li>
        <li>Worker deletes the Kubernetes namespace (or simulates).</li>
        <li>
          Status DESTROYED and the unique
          <code class="font-mono text-xs">name</code>
          is renamed to
          <code class="font-mono text-xs">…--destroyed-…</code>
          so “environment already exists” does not block relaunch.
        </li>
      </ol>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        The TTL reaper does the same teardown path when
        <code class="font-mono text-xs">ttl_expires_at</code>
        passes.
      </p>
    </section>

    <MermaidDiagram
      title="Environment status machine"
      code="
stateDiagram-v2
  [*] --> PROVISIONING
  PROVISIONING --> RUNNING
  PROVISIONING --> FAILED
  RUNNING --> PROVISIONING: rebuild
  RUNNING --> TEARDOWN_PENDING: destroy or TTL
  FAILED --> TEARDOWN_PENDING: destroy
  TEARDOWN_PENDING --> DESTROYED
  DESTROYED --> [*]
"
    />

    <section class="space-y-4">
      <h2 class="text-xl font-semibold">4. Drift detection</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        <strong class="text-[var(--lp-text)]">Goal:</strong>
        notice when the live cluster no longer matches what Launchpad thinks it deployed.
      </p>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <strong class="text-[var(--lp-text)]">Preview mode</strong>
          — compares Deployment
          <code class="font-mono text-xs">app</code>
          image and git commit label/env.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">Manifest mode</strong>
          — reloads workspace YAML, patches it the same way deploy does, then compares each
          Deployment name/image in the namespace.
        </li>
        <li>
          Runs on a timer (Celery beat) or via
          <code class="font-mono text-xs">POST …/drift-scan</code>
          (“Scan drift” on the env page).
        </li>
        <li>
          Writes an audit event
          <code class="font-mono text-xs">DRIFT_DETECTED</code>
          and sets
          <code class="font-mono text-xs">drift_detected</code>
          on the API response.
        </li>
      </ul>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">5. Provision a cloud workspace (brief)</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Separate from previews: the Provision wizard generates IaC under
        <code class="font-mono text-xs">IAC_WORKSPACE_ROOT</code>,
        encrypts cloud credentials, and opens a sandbox terminal so you can
        <code class="font-mono text-xs">terraform apply</code>
        /
        <code class="font-mono text-xs">pulumi up</code>
        yourself. Workspaces can later feed a preview that uses
        <strong class="text-[var(--lp-text)]">manifest deploy</strong>.
      </p>
    </section>
  </TechnicalDocsShell>
</template>

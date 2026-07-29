<script setup lang="ts">
useHead({
  title: 'Frontend · Technical notes',
  meta: [{ name: 'robots', content: 'noindex, nofollow' }],
})
</script>

<template>
  <TechnicalDocsShell>
    <header class="space-y-3">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">Frontend</p>
      <h1 class="text-3xl font-semibold tracking-tight">Nuxt 4 UI</h1>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        The browser app is Nuxt 4 with TypeScript, Tailwind, and Zod. It never talks to Kubernetes
        directly — everything goes through
        <code class="font-mono text-xs">/api/v1</code>.
      </p>
    </header>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Where things live</h2>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <code class="font-mono text-xs">app/pages/</code>
          — routes (file-based). Example:
          <code class="font-mono text-xs">environments/[id].vue</code>
          →
          <code class="font-mono text-xs">/environments/:id</code>.
        </li>
        <li>
          <code class="font-mono text-xs">app/composables/</code>
          — reusable logic with
          <code class="font-mono text-xs">use*</code>
          names (
          <code class="font-mono text-xs">useApi</code>,
          <code class="font-mono text-xs">useEnvironments</code>,
          <code class="font-mono text-xs">useOrgs</code>).
        </li>
        <li>
          <code class="font-mono text-xs">app/components/</code>
          — UI pieces (
          <code class="font-mono text-xs">AppShell</code>,
          <code class="font-mono text-xs">EnvironmentCard</code>,
          <code class="font-mono text-xs">AuditTimeline</code>).
        </li>
        <li>
          <code class="font-mono text-xs">app/types/</code>
          — shared TypeScript interfaces. No
          <code class="font-mono text-xs">any</code>.
        </li>
      </ul>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">The API helper (most important composable)</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        <code class="font-mono text-xs">useApi().apiFetch</code>
        wraps
        <code class="font-mono text-xs">fetch</code>
        and automatically attaches:
      </p>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <code class="font-mono text-xs">Authorization: Bearer …</code>
          from localStorage
        </li>
        <li>
          <code class="font-mono text-xs">X-Org-ID</code>
          for the active organization
        </li>
        <li>A correlation ID so logs line up across UI → API → worker</li>
      </ul>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Example mental call:
        <code class="font-mono text-xs">apiFetch('/environments')</code>
        becomes
        <code class="font-mono text-xs">GET http://localhost:3000/api/v1/environments</code>
        (proxied by Nuxt/Vite to the FastAPI process).
      </p>
    </section>

    <MermaidDiagram
      title="Auth + org on every request"
      code="
sequenceDiagram
  participant Page
  participant useApi
  participant API
  Page->>useApi: apiFetch('/environments')
  useApi->>useApi: read token + activeOrgId
  useApi->>API: Bearer + X-Org-ID
  API-->>useApi: JSON list
  useApi-->>Page: typed Environment[]
"
    />

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Live updates without polling spam</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Environment pages open an
        <strong class="text-[var(--lp-text)]">EventSource (SSE)</strong>
        connection:
      </p>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <code class="font-mono text-xs">useEnvironmentLiveStream</code>
          — status changes (PROVISIONING → RUNNING)
        </li>
        <li>
          <code class="font-mono text-xs">useJobLogStream</code>
          —
          <code class="font-mono text-xs">/logs/stream</code>
          for provisioning log lines
        </li>
      </ul>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Both are
        <strong class="text-[var(--lp-text)]">browser-only</strong>.
        Creating
        <code class="font-mono text-xs">EventSource</code>
        during SSR crashes with “EventSource is not defined”, so
        <code class="font-mono text-xs">connect()</code>
        guards on
        <code class="font-mono text-xs">import.meta.client</code>.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Key pages cheat sheet</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead class="font-mono text-[10px] uppercase tracking-wider text-[var(--lp-muted)]">
            <tr>
              <th class="py-2 pr-4">Route</th>
              <th class="py-2">Job</th>
            </tr>
          </thead>
          <tbody class="text-[var(--lp-muted)]">
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/</td>
              <td class="py-2">Dashboard of environments + org cost chip</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/launch</td>
              <td class="py-2">One-click preview wizard</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/environments/:id</td>
              <td class="py-2">Detail, logs, TTL, destroy, drift scan</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/provision</td>
              <td class="py-2">Create cloud / kind IaC workspace</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/workspaces/:id</td>
              <td class="py-2">File tree, editor, sandbox terminal</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/org</td>
              <td class="py-2">Members, invites, SSO group maps</td>
            </tr>
            <tr class="border-t border-[var(--lp-line)]">
              <td class="py-2 pr-4 font-mono text-xs text-[var(--lp-text)]">/docs</td>
              <td class="py-2">Product user guide (public in nav)</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Shell and org switcher</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        <code class="font-mono text-xs">AppShell.vue</code>
        wraps almost every page. Switching org in the sidebar saves
        <code class="font-mono text-xs">launchpad_active_org_id</code>
        and reloads the page so lists refetch under the new
        <code class="font-mono text-xs">X-Org-ID</code>.
      </p>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        These technical notes intentionally do
        <strong class="text-[var(--lp-text)]">not</strong>
        appear in that sidebar.
      </p>
    </section>
  </TechnicalDocsShell>
</template>

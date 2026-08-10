<script setup lang="ts">
useHead({
  title: 'Fleet · Technical notes',
  meta: [{ name: 'robots', content: 'noindex, nofollow' }],
})
</script>

<template>
  <TechnicalDocsShell>
    <header class="space-y-3">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">Fleet</p>
      <h1 class="text-3xl font-semibold tracking-tight">Self-hosted nodes + AI blueprints</h1>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Fleet is how Launchpad reaches machines that are not a managed Kubernetes preview
        cluster and not a Terraform workspace alone. An
        <strong class="text-[var(--lp-text)]">agent</strong>
        on a Linux host opens an outbound tunnel; the control plane sends Docker commands and
        receives telemetry. The same page can generate guardrailed infrastructure blueprints with
        AI and deploy them to a node or to GCP / AWS / Azure.
      </p>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Product guide:
        <NuxtLink to="/docs#hybrid" class="text-[var(--lp-accent)] hover:underline">/docs#hybrid</NuxtLink>
        · markdown:
        <code class="font-mono text-xs">docs/hybrid-cloud.md</code>
        · UI:
        <NuxtLink to="/fleet" class="text-[var(--lp-accent)] hover:underline">/fleet</NuxtLink>.
      </p>
    </header>

    <MermaidDiagram
      title="Outbound agent tunnel"
      code="
sequenceDiagram
  participant Op as Operator
  participant UI as Nuxt /fleet
  participant API as Control plane
  participant Ag as Agent host
  Op->>UI: Enroll node
  UI->>API: POST /nodes
  API-->>UI: install command + TOKEN
  Op->>Ag: curl install.sh
  Ag->>API: POST /nodes/register
  API-->>Ag: node_id + HMAC secret
  Ag->>API: WSS connect signed HMAC
  loop Heartbeat
    Ag->>API: telemetry
  end
  UI->>API: deploy / command
  API->>Ag: pull run stop logs
  Ag-->>API: command result
"
    />

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Why outbound only?</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Homelab and edge hosts usually sit behind NAT. Instead of requiring a public IP or VPN into
        the house, the agent dials
        <code class="font-mono text-xs">wss://…/api/v1/ws/nodes/connect</code>
        and keeps the socket open. The control plane never initiates TCP into the private network.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Code map</h2>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <code class="font-mono text-xs">agent/</code>
          - daemon (
          <code class="font-mono text-xs">main.py</code>,
          <code class="font-mono text-xs">runner.py</code>) packaged as Docker.
        </li>
        <li>
          <code class="font-mono text-xs">services/node_registry.py</code>
          - enrollment, HMAC verify,
          <code class="font-mono text-xs">LiveAgentHub</code>
          for live sockets and command correlation.
        </li>
        <li>
          <code class="font-mono text-xs">services/agent_install.py</code>
          - public
          <code class="font-mono text-xs">/install.sh</code>
          and agent source bundle.
        </li>
        <li>
          <code class="font-mono text-xs">routers/nodes.py</code>
          - REST CRUD + WebSocket.
        </li>
        <li>
          <code class="font-mono text-xs">services/ai_provisioner.py</code>
          - Gemini / heuristic blueprints and deploy mapping.
        </li>
        <li>
          UI:
          <code class="font-mono text-xs">HybridProvisioner</code>,
          <code class="font-mono text-xs">NodeFleetPanel</code>,
          <code class="font-mono text-xs">AiProvisionerPanel</code>.
        </li>
      </ul>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Auth model</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Operators use normal Launchpad JWTs for
        <code class="font-mono text-xs">/api/v1/nodes</code>.
        Agents never see that JWT. They sign
        <code class="font-mono text-xs">{node_id}.{ts}.{nonce}</code>
        with a per-node secret (60s clock skew). Enrollment tokens are
        <code class="font-mono text-xs">lp_…</code>,
        single-use, TTL via
        <code class="font-mono text-xs">AGENT_ENROLLMENT_TTL_SECONDS</code>.
        Revoke deletes usability of the secret and closes the hub connection.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">AI provisioner path</h2>
      <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>Prompt → structured <code class="font-mono text-xs">InfraBlueprint</code> (JSON schema).</li>
        <li>Guardrails clamp CPU/memory for <code class="font-mono text-xs">local_node</code> targets.</li>
        <li>
          Target
          <code class="font-mono text-xs">local_node</code>:
          map services to Docker run specs and dispatch over the hub.
        </li>
        <li>
          Target GCP/AWS/Azure: map into the existing Provision / wizard request shape for IaC.
        </li>
      </ol>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Without
        <code class="font-mono text-xs">GEMINI_API_KEY</code>,
        heuristic mode still works when
        <code class="font-mono text-xs">AI_PROVISIONER_HEURISTIC_FALLBACK=true</code>.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Local wiring tip</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Set
        <code class="font-mono text-xs">AGENT_CONTROL_PLANE_URL</code>
        to the API origin (for example
        <code class="font-mono text-xs">http://localhost:8000</code>
        or your LAN IP), not the Nuxt origin on
        <code class="font-mono text-xs">:3000</code>.
        Install and register hit API routes; the web app would 404 them.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Not the same as</h2>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <strong class="text-[var(--lp-text)]">Environments</strong>
          - git branch previews on Kubernetes.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">Provision</strong>
          - generate Terraform/Pulumi on disk and apply in a sandbox.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">Launchpad OIDC issuer</strong>
          - short-lived tokens for cloud workload identity in sandboxes (separate from Hybrid login).
        </li>
      </ul>
    </section>
  </TechnicalDocsShell>
</template>

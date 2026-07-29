<script setup lang="ts">
useHead({
  title: 'Kubernetes · Technical notes',
  meta: [{ name: 'robots', content: 'noindex, nofollow' }],
})
</script>

<template>
  <TechnicalDocsShell>
    <header class="space-y-3">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">Kubernetes</p>
      <h1 class="text-3xl font-semibold tracking-tight">kind, preview, and manifests</h1>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Local development usually uses a
        <strong class="text-[var(--lp-text)]">kind</strong>
        cluster named something like
        <code class="font-mono text-xs">launchpad</code>
        (context
        <code class="font-mono text-xs">kind-launchpad</code>).
        Cloud previews use the same control-plane ideas against a real cluster or account.
      </p>
    </header>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Simulate vs real cluster</h2>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <code class="font-mono text-xs">KUBERNETES_ENABLED=false</code>
          — workers fake success. Useful for UI wiring without Docker/kind. Preview URL often points
          at the portal status page
          <code class="font-mono text-xs">/p/{id}</code>.
        </li>
        <li>
          <code class="font-mono text-xs">KUBERNETES_ENABLED=true</code>
          — real apply/delete against the configured context. Local NodePorts sit in a small range
          (default 30080–30084) so kind on Docker Desktop stays reliable.
        </li>
      </ul>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Two deploy modes</h2>
      <div class="grid gap-3 md:grid-cols-2">
        <div class="lp-glass space-y-2 rounded-xl p-4">
          <h3 class="font-semibold">preview</h3>
          <p class="text-xs leading-6 text-[var(--lp-muted)]">
            Launchpad programs a standard Deployment + Service + NetworkPolicy for you
            (<code class="font-mono">(KubernetesProvisioner)</code>.
            Good for templates and “just run this image”.
          </p>
        </div>
        <div class="lp-glass space-y-2 rounded-xl p-4">
          <h3 class="font-semibold">manifest</h3>
          <p class="text-xs leading-6 text-[var(--lp-muted)]">
            Launchpad loads YAML from the workspace folder
            <code class="font-mono">infra/k8s/manifests/</code>,
            patches namespace/labels/image, then applies
            <code class="font-mono">(ManifestDeployer)</code>.
          </p>
        </div>
      </div>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Mode is chosen explicitly on launch, or automatically when the linked workspace packaging is
        raw manifests.
      </p>
    </section>

    <MermaidDiagram
      title="Which deployer runs?"
      code="
flowchart LR
  Launch[Launch / provision task] --> Mode{deploy_mode}
  Mode -->|preview| KP[KubernetesProvisioner]
  Mode -->|manifest| MD[ManifestDeployer]
  KP --> NS[Ephemeral namespace]
  MD --> NS
"
    />

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">What “ephemeral” means here</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Each environment gets its own namespace (usually
        <code class="font-mono text-xs">launchpad-env-{uuid}</code>).
        Governance objects (LimitRange, ResourceQuota, NetworkPolicy) keep previews from eating the
        whole cluster. When you destroy or TTL expires, the namespace goes away.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Image build path (optional)</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        With
        <code class="font-mono text-xs">PREVIEW_BUILD_ENABLED=true</code>,
        the worker can clone the git repo, run
        <code class="font-mono text-xs">docker build</code>,
        and
        <code class="font-mono text-xs">kind load</code>
        (or push to a registry) before APPLY. Manifest mode typically skips this and uses the
        configured workload image instead.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">kind scripts</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        <code class="font-mono text-xs">scripts/kind-up.sh</code>
        /
        <code class="font-mono text-xs">kind-down.sh</code>
        (also
        <code class="font-mono text-xs">make kind-up</code>)
        create the cluster and port mappings. Auto-manage can call the same scripts when Local
        launch needs a cluster and none exists.
      </p>
    </section>
  </TechnicalDocsShell>
</template>

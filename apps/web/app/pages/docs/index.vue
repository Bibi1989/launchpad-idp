<script setup lang="ts">
const sections = [
  { id: 'overview', label: 'How it works' },
  { id: 'getting-started', label: 'Getting started' },
  { id: 'environments', label: 'Launch an environment' },
  { id: 'rebuild', label: 'Git push rebuilds' },
  { id: 'manifest', label: 'Apply manifests' },
  { id: 'provision', label: 'Provision cloud infra' },
  { id: 'credentials', label: 'Cloud credentials' },
  { id: 'github', label: 'GitHub Connect' },
  { id: 'workspaces', label: 'Workspaces & terminal' },
  { id: 'teardown', label: 'Teardown & TTL' },
] as const
</script>

<template>
  <div class="grid gap-8 lg:grid-cols-[220px_1fr] animate-fade-up">
    <aside class="lg:sticky lg:top-24 lg:self-start">
      <p class="lp-label mb-3">Guide</p>
      <nav class="space-y-1">
        <a
          v-for="section in sections"
          :key="section.id"
          :href="`#${section.id}`"
          class="lp-nav-link text-sm"
        >
          {{ section.label }}
        </a>
      </nav>
    </aside>

    <article class="space-y-12 max-w-3xl">
      <header class="space-y-3">
        <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">Documentation</p>
        <h1 class="text-3xl font-semibold tracking-tight md:text-4xl">Using Launchpad</h1>
        <p class="text-[var(--lp-muted)]">
          Launchpad is an internal developer portal for governed ephemeral app environments and
          multi-cloud infrastructure. This guide explains how the product works and walks through
          each flow step by step.
        </p>
      </header>

      <section id="overview" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">How it works</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          Launchpad has three distinct jobs. They share the same UI and can be linked, but they run
          on different lifecycles:
        </p>
        <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            <strong class="text-[var(--lp-text)]">Provision (infra)</strong> — generate a
            Terraform or Pulumi workspace for GCP, AWS, Azure, Cloudflare, or kind. You attach
            short-lived cloud credentials, pick resources, then apply the stack from the sandbox
            terminal. This stands up platform infrastructure (VPC, cluster, etc.).
          </li>
          <li>
            <strong class="text-[var(--lp-text)]">Manifest (workload)</strong> — apply Kubernetes
            YAML or Helm charts from
            <code class="font-mono text-xs">infra/k8s/manifests/</code>
            in your workspace. This deploys app objects onto a cluster that already exists. In the
            sandbox terminal, manifests apply separately after Terraform/Pulumi.
          </li>
          <li>
            <strong class="text-[var(--lp-text)]">Environment (preview)</strong> — short-lived,
            governed preview of an app from a git repo and branch. Launchpad creates an isolated
            namespace, deploys the workload, streams status and logs, and tears everything down when
            the TTL expires. When linked to a workspace with raw manifests, environments use
            <strong class="text-[var(--lp-text)]">manifest deploy</strong>
            instead of the built-in preview profile.
          </li>
        </ul>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          Use
          <NuxtLink to="/launch" class="text-[var(--lp-accent)] hover:underline">Launch</NuxtLink>
          for a one-click preview — Local kind (single screen) or cloud, catalog template or your
          own repo. Use
          <NuxtLink to="/" class="text-[var(--lp-accent)] hover:underline">Environments</NuxtLink>
          for the classic git form.
          Use
          <NuxtLink to="/provision" class="text-[var(--lp-accent)] hover:underline">Provision</NuxtLink>
          to create a new cloud stack, and
          <NuxtLink to="/workspaces" class="text-[var(--lp-accent)] hover:underline">Workspaces</NuxtLink>
          to reopen one you already generated.
        </p>
      </section>

      <section id="getting-started" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">Getting started</h2>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>Open Launchpad in your browser.</li>
          <li>
            Sign in with your account, or use
            <strong class="text-[var(--lp-text)]">Dev login</strong> when that option is available.
          </li>
          <li>
            You land on the Environments dashboard — from there you can launch a preview app or
            navigate to Provision / Workspaces / GitHub.
          </li>
        </ol>
      </section>

      <section id="environments" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">Launch an environment</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          An environment is a governed, time-boxed preview of your application. The fastest path is
          <NuxtLink to="/launch" class="text-[var(--lp-accent)] hover:underline">Launch</NuxtLink>
          — choose
          <strong class="text-[var(--lp-text)]">Local (kind)</strong>
          to test on your machine with no cloud credentials, or connect GCP/AWS/Azure/Cloudflare.
          Provisioning runs asynchronously; the detail page shows live status and logs while it
          comes up.
        </p>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            Open
            <NuxtLink to="/launch" class="text-[var(--lp-accent)] hover:underline">/launch</NuxtLink>
            and pick a target (Local kind is the default for local testing).
          </li>
          <li>
            For Local: run
            <code class="font-mono text-xs text-[var(--lp-text)]">make kind-down && make kind-up</code>
            (maps NodePorts to localhost), set
            <code class="font-mono text-xs text-[var(--lp-text)]">KUBERNETES_ENABLED=true</code>
            and
            <code class="font-mono text-xs text-[var(--lp-text)]">KUBERNETES_CONTEXT=kind-launchpad</code>
            in the API
            <code class="font-mono text-xs text-[var(--lp-text)]">.env</code>,
            then restart API + worker. Open Preview hits the real pod at
            <code class="font-mono text-xs text-[var(--lp-text)]">http://127.0.0.1:&lt;nodePort&gt;</code>.
          </li>
          <li>Pick a preview app template, name the environment, and launch.</li>
          <li>
            Open the environment to watch live logs, confirm it reaches
            <strong class="text-[var(--lp-text)]">RUNNING</strong>, and use
            <strong class="text-[var(--lp-text)]">Open preview</strong>.
          </li>
        </ol>
      </section>

      <section id="rebuild" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">Git push rebuilds</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          When a GitHub webhook is configured for your repositories, a push to a branch that matches
          an active environment’s repo and branch automatically triggers a rebuild. The environment
          detail page shows whether
          <code class="font-mono text-xs">WEBHOOK_SECRET</code>
          is set on the API.
        </p>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>Keep the environment running against the branch you are iterating on.</li>
          <li>Push commits to that branch in GitHub.</li>
          <li>
            Launchpad marks the environment as provisioning again, records the latest commit, and
            redeploys.
          </li>
          <li>
            Watch the environment card or detail page — status, commit SHA, and logs update live.
          </li>
        </ol>
      </section>

      <section id="provision" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">Provision cloud infrastructure</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          The Provision wizard builds an IaC bundle for you. Launchpad does not apply cloud changes
          by itself — you run the plan/apply commands in the sandbox after the bundle is ready.
        </p>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            Go to
            <NuxtLink to="/provision" class="text-[var(--lp-accent)] hover:underline">Provision</NuxtLink>.
          </li>
          <li>Choose a workspace name, cloud provider, and IaC engine (Terraform or Pulumi).</li>
          <li>
            Paste short-lived cloud credentials for that provider (see
            <a href="#credentials" class="text-[var(--lp-accent)] hover:underline">Cloud credentials</a>).
          </li>
          <li>Select the resources you want in the generated stack (VPC, cluster, storage, etc.).</li>
          <li>
            When you enable a cluster (GKE, EKS, AKS, …), pick
            <strong class="text-[var(--lp-text)]">Raw K8s Manifests</strong>
            or
            <strong class="text-[var(--lp-text)]">Helm</strong>,
            then toggle the Kubernetes objects to scaffold — Deployment, Service, Ingress, Pod,
            Job, CronJob, StatefulSet, DaemonSet, ConfigMap, Secret, PVC, Role, HPA, and more.
          </li>
          <li>
            Optionally connect GitHub and bootstrap a repository with a CI workflow and cloud secrets.
          </li>
          <li>
            Finish with
            <strong class="text-[var(--lp-text)]">Generate workspace</strong>.
            Launchpad scaffolds the IaC files and opens a sandbox terminal.
          </li>
          <li>
            In the terminal, apply infrastructure first:
            <code class="text-[var(--lp-accent)]">terraform plan</code>
            /
            <code class="text-[var(--lp-accent)]">terraform apply</code>,
            or
            <code class="text-[var(--lp-accent)]">pulumi up</code>.
            Manifest apply (
            <code class="text-[var(--lp-accent)]">kubectl apply</code>
            /
            <code class="text-[var(--lp-accent)]">helm upgrade</code>
            ) runs as a separate step afterward.
          </li>
        </ol>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          Credentials are encrypted at rest, injected into the sandbox for that session, and never
          shown in logs in plaintext.
        </p>
      </section>

      <section id="manifest" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">Apply Kubernetes manifests</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          Manifest deploy is separate from cloud infra provision. Workspaces with
          <strong class="text-[var(--lp-text)]">Raw K8s Manifests</strong>
          include files under
          <code class="font-mono text-xs">infra/k8s/manifests/</code>.
          You can apply them manually from the sandbox terminal, or let Launchpad apply them when
          you launch an environment linked to that workspace.
        </p>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>Complete the Provision wizard and enable Raw K8s Manifests packaging.</li>
          <li>
            Open the workspace terminal with
            <strong class="text-[var(--lp-text)]">Run kubectl apply in sandbox on open</strong>
            to apply manifests to your cluster, or apply infra first then manifests manually.
          </li>
          <li>
            To run a governed preview from those manifests, go to
            <NuxtLink to="/launch" class="text-[var(--lp-accent)] hover:underline">Launch</NuxtLink>
            and select the workspace — Launchpad uses
            <strong class="text-[var(--lp-text)]">manifest deploy</strong>
            automatically.
          </li>
        </ol>
      </section>

      <section id="credentials" class="scroll-mt-28 space-y-6">
        <div class="space-y-3">
          <h2 class="text-xl font-semibold">Cloud credentials</h2>
          <p class="text-sm leading-7 text-[var(--lp-muted)]">
            Cloud access is attached
            <strong class="text-[var(--lp-text)]">per workspace</strong>
            in the Provision wizard — there is no permanent cloud OAuth login. Use credentials with
            only the permissions needed for the resources you selected.
          </p>
        </div>

        <div id="gcp" class="scroll-mt-28 space-y-3">
          <h3 class="text-lg font-semibold">GCP — service account JSON</h3>
          <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
            <li>
              Open
              <a
                class="text-[var(--lp-accent)] hover:underline"
                href="https://console.cloud.google.com/iam-admin/serviceaccounts"
                target="_blank"
                rel="noreferrer"
              >IAM → Service Accounts</a>
              in Google Cloud Console.
            </li>
            <li>Select or create a service account in the target project.</li>
            <li>
              Grant roles for the resources you will provision (for example Compute Admin, Kubernetes
              Engine Admin, Secret Manager Admin).
            </li>
            <li>Keys → Add key → Create new key → JSON — download the file.</li>
            <li>
              In Provision, paste the full JSON into
              <strong class="text-[var(--lp-text)]">GCP SA key JSON</strong>
              and set the Project ID.
            </li>
          </ol>
        </div>

        <div id="aws" class="scroll-mt-28 space-y-3">
          <h3 class="text-lg font-semibold">AWS — access keys</h3>
          <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
            <li>
              Sign in to the
              <a
                class="text-[var(--lp-accent)] hover:underline"
                href="https://console.aws.amazon.com/iam/"
                target="_blank"
                rel="noreferrer"
              >AWS IAM console</a>.
            </li>
            <li>
              Create or select a user with least-privilege policies for the resources you need
              (VPC, EKS, S3, and so on).
            </li>
            <li>Security credentials → Create access key → Command Line Interface (CLI).</li>
            <li>
              Paste the Access key ID and Secret access key into Provision. Add a session token if
              you are using temporary STS credentials.
            </li>
          </ol>
        </div>

        <div id="azure" class="scroll-mt-28 space-y-3">
          <h3 class="text-lg font-semibold">Azure — service principal</h3>
          <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
            <li>Azure Portal → Microsoft Entra ID → App registrations → New registration.</li>
            <li>Certificates &amp; secrets → New client secret — copy the value immediately.</li>
            <li>
              Note the Application (client) ID, Directory (tenant) ID, and your Subscription ID.
            </li>
            <li>
              Assign the app a role on the subscription or resource group (for example Contributor).
            </li>
            <li>
              Paste Client ID, Client secret, Tenant ID, Subscription ID, and Resource group into
              Provision.
            </li>
          </ol>
        </div>

        <div id="cloudflare" class="scroll-mt-28 space-y-3">
          <h3 class="text-lg font-semibold">Cloudflare — API token</h3>
          <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
            <li>
              Open
              <a
                class="text-[var(--lp-accent)] hover:underline"
                href="https://dash.cloudflare.com/profile/api-tokens"
                target="_blank"
                rel="noreferrer"
              >My Profile → API Tokens</a>.
            </li>
            <li>Create a token with permissions for Workers, R2, and/or DNS as needed.</li>
            <li>Paste the token and Account ID into Provision.</li>
          </ol>
        </div>
      </section>

      <section id="github" class="scroll-mt-28 space-y-4">
        <h2 class="text-xl font-semibold">GitHub Connect</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          Launchpad uses a
          <strong class="text-[var(--lp-text)]">GitHub App</strong>
          so you never paste a personal access token in the browser. Connecting installs or
          authorizes the App on your user or organization account; Launchpad then creates repos and
          sets CI secrets on your behalf during Provision.
        </p>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            Open
            <NuxtLink to="/integrations/github" class="text-[var(--lp-accent)] hover:underline">Integrations → GitHub</NuxtLink>.
          </li>
          <li>Click <strong class="text-[var(--lp-text)]">Connect GitHub</strong>.</li>
          <li>
            On GitHub, choose the account or organization and the repositories the App may access,
            then Authorize / Install.
          </li>
          <li>You are redirected back to Launchpad when installation finishes.</li>
          <li>
            Confirm installations appear on the GitHub page — select one when bootstrapping a repo
            from the Provision wizard.
          </li>
        </ol>
        <div class="rounded-lg border border-[var(--lp-line)] bg-[var(--lp-ink)]/50 p-4 text-sm text-[var(--lp-muted)]">
          <p class="lp-label mb-2">What the App needs</p>
          <ul class="mb-3 list-disc space-y-1 pl-5 text-xs leading-6">
            <li>
              <strong class="text-[var(--lp-text)]">Administration</strong>
              — create repositories
            </li>
            <li>
              <strong class="text-[var(--lp-text)]">Contents</strong>
              — push workflow and infrastructure files
            </li>
            <li>
              <strong class="text-[var(--lp-text)]">Secrets</strong>
              — set CI cloud secrets
            </li>
            <li>
              <strong class="text-[var(--lp-text)]">Metadata</strong>
              — read-only
            </li>
          </ul>
          <p class="text-xs leading-6">
            After permission changes, open the installation on GitHub and
            <strong class="text-[var(--lp-text)]">Accept</strong>
            the new request. Apps installed on a
            <strong class="text-[var(--lp-text)]">personal account</strong>
            cannot create new repos via API — create an empty repo first, or install on an
            <strong class="text-[var(--lp-text)]">Organization</strong>.
          </p>
        </div>
        <GithubConnectCard compact />
      </section>

      <section id="workspaces" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">Workspaces &amp; sandbox terminal</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          Every finished Provision run becomes a workspace: generated Terraform or Pulumi files plus
          a sandbox where your cloud credentials are already available. The workspace page includes
          an IDE-style file explorer so you can edit manifests, add Kubernetes/Terraform templates,
          save, format, push to GitHub, and run kubectl / terraform commands in the terminal.
        </p>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            Open
            <NuxtLink to="/workspaces" class="text-[var(--lp-accent)] hover:underline">Workspaces</NuxtLink>
            and select a workspace.
          </li>
          <li>
            Use the explorer to create, rename, delete, edit, format, and save files
            (<kbd class="font-mono text-[var(--lp-accent)]">⌘/Ctrl+S</kbd>).
          </li>
          <li>
            Add Kubernetes YAML (Deployment, Service, Ingress, Pod, Job, …) or Terraform stubs from
            the template menus.
          </li>
          <li>
            Click
            <strong class="text-[var(--lp-text)]">Push to GitHub</strong>
            to commit the current bundle into a connected repository.
          </li>
          <li>
            Use
            <strong class="text-[var(--lp-text)]">kubectl apply / delete</strong>
            or Terraform
            <strong class="text-[var(--lp-text)]">plan / apply / destroy</strong>
            — commands are sent to the sandbox terminal.
          </li>
        </ol>
      </section>

      <section id="teardown" class="scroll-mt-28 space-y-3">
        <h2 class="text-xl font-semibold">Teardown &amp; TTL</h2>
        <p class="text-sm leading-7 text-[var(--lp-muted)]">
          Environments are meant to be temporary. Launchpad tracks remaining lifetime on each
          environment and cleans up expired ones automatically.
        </p>
        <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
          <li>
            To tear down early, open the environment and click
            <strong class="text-[var(--lp-text)]">Destroy</strong>
            (or destroy from the dashboard).
          </li>
          <li>Status moves through teardown while resources are removed; watch the live logs.</li>
          <li>
            If you do nothing, the environment expires when its TTL is reached and Launchpad
            reaps it on a schedule.
          </li>
        </ol>
      </section>
    </article>
  </div>
</template>

<script setup lang="ts">
useHead({
  title: 'Auth & orgs · Technical notes',
  meta: [{ name: 'robots', content: 'noindex, nofollow' }],
})
</script>

<template>
  <TechnicalDocsShell>
    <header class="space-y-3">
      <p class="font-mono text-xs uppercase tracking-[0.22em] text-[var(--lp-accent)]">Auth & orgs</p>
      <h1 class="text-3xl font-semibold tracking-tight">Who can see what</h1>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Launchpad is multi-tenant at the organization level. Your JWT proves who you are; the
        <code class="font-mono text-xs">X-Org-ID</code>
        header says which org you are acting in.
      </p>
    </header>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Signing in</h2>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>
          <strong class="text-[var(--lp-text)]">Password</strong>
          - register / login; password stored as a hash.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">Dev login</strong>
          - one-click local account when
          <code class="font-mono text-xs">AUTH_DEV_LOGIN_ENABLED=true</code>.
        </li>
        <li>
          <strong class="text-[var(--lp-text)]">OIDC SSO</strong>
          - Authorization Code flow (
          <code class="font-mono text-xs">/auth/oidc/start</code>
          → IdP →
          <code class="font-mono text-xs">/auth/callback</code>).
        </li>
      </ul>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Every successful login returns a JWT plus a list of orgs. The UI stores the token as
        <code class="font-mono text-xs">launchpad_access_token</code>.
      </p>
    </section>

    <MermaidDiagram
      title="OIDC happy path"
      code="
sequenceDiagram
  participant Browser
  participant API
  participant IdP
  Browser->>API: GET /auth/oidc/start
  API-->>Browser: authorization_url + state
  Browser->>IdP: login / consent
  IdP-->>Browser: redirect with code
  Browser->>API: POST /auth/oidc/callback
  API->>IdP: exchange code
  API->>API: upsert user + sync groups
  API-->>Browser: JWT + orgs
"
    />

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Roles (simple ranking)</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        From weakest to strongest:
        <code class="font-mono text-xs">viewer</code>
        →
        <code class="font-mono text-xs">member</code>
        →
        <code class="font-mono text-xs">admin</code>
        →
        <code class="font-mono text-xs">owner</code>.
        Admins manage members and invites. Owners can assign the owner role. Cross-tenant access
        returns
        <strong class="text-[var(--lp-text)]">404</strong>
        (not 403) so existence is not leaked.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Invites by email</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        From
        <code class="font-mono text-xs">/org</code>
        an admin creates an invite. The API stores a
        <strong class="text-[var(--lp-text)]">hashed token</strong>
        (never the raw token). If SMTP is configured, an email goes out; otherwise the response
        includes a shareable
        <code class="font-mono text-xs">invite_url</code>
        like
        <code class="font-mono text-xs">/invite/{token}</code>.
      </p>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        When the invitee registers or logs in with that email, pending invites are auto-accepted.
        They can also open the invite link while signed in.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">SSO group → role mapping</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        IdP tokens often include a
        <code class="font-mono text-xs">groups</code>
        claim (configurable via
        <code class="font-mono text-xs">OIDC_GROUP_CLAIM</code>).
        On each SSO login Launchpad:
      </p>
      <ol class="list-decimal space-y-2 pl-5 text-sm leading-7 text-[var(--lp-muted)]">
        <li>Reads the groups list from the id_token.</li>
        <li>Looks up per-org mappings created on the Organization page.</li>
        <li>
          Optionally applies a global map (
          <code class="font-mono text-xs">OIDC_GROUP_ROLE_MAP</code>
          +
          <code class="font-mono text-xs">OIDC_DEFAULT_ORG_SLUG</code>).
        </li>
        <li>
          Creates or
          <strong class="text-[var(--lp-text)]">promotes</strong>
          memberships. It never demotes an owner via SSO.
        </li>
      </ol>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        Example: map IdP group
        <code class="font-mono text-xs">launchpad-admins</code>
        → role
        <code class="font-mono text-xs">admin</code>
        for your shared org. Next SSO login for that user joins/promotes automatically.
      </p>
    </section>

    <section class="space-y-3">
      <h2 class="text-xl font-semibold">Personal org bootstrap</h2>
      <p class="text-sm leading-7 text-[var(--lp-muted)]">
        First login always ensures you have at least one org (your personal org as owner). Shared
        orgs come from invites or SSO maps. Environments and workspaces carry
        <code class="font-mono text-xs">org_id</code>
        so lists stay scoped.
      </p>
    </section>
  </TechnicalDocsShell>
</template>

# Launchpad product guide

How-to reference for features in the UI. In-app copy lives at **`/docs`**. Hybrid details: [`hybrid-cloud.md`](./hybrid-cloud.md).

## Map of the product

| Area | Path | One-line purpose |
| --- | --- | --- |
| Home | `/home` | Counts, shortcuts, cloud-key status |
| Launch | `/launch` | One-click preview from template or git |
| Environments | `/environments`, `/environments/[id]` | Manage running previews and ops actions |
| Share status | `/p/[id]` | Public-ish progress page for a preview |
| PR deep link | `/pr/[number]` | Jump to env (or preview) for a GitHub PR |
| Catalog | `/catalog` | Golden-path templates + starred workspaces |
| Workspaces | `/workspaces` | Generated IaC / manifests + sandbox IDE |
| Projects | `/projects` | Group workspaces under org projects |
| Provision | `/provision` | Wizard: Terraform, Pulumi, or Ansible bundles |
| Hybrid Cloud | `/hybrid` | Homelab agents + AI blueprints |
| Integrations | `/integrations` | GitHub, GitLab, Slack, Jira |
| Organization | `/org` | Members, invites, SSO maps, billing |
| Settings | `/settings` | Account cloud credentials vault |
| Dockerfiles | `/dockerfiles` | Scan, scaffold, review, build Dockerfiles |
| Docs | `/docs` | In-product guide |
| Login / SSO | `/login`, `/auth/callback` | Password, Dev login, OIDC SSO |
| Onboarding | `/onboarding/org` | Create first org |
| Invites | `/invite/...` | Accept org or project invites |

---

## Catalog (`/catalog`)

Golden paths and favorites used when launching previews.

1. Open **Catalog**.
2. **Templates** tab: pick a golden-path template (stack, blurb, Launch shortcut).
3. **Starred** tab (`?tab=starred`): workspaces you starred from Workspaces; unstar when done.
4. From Launch, choose a catalog template instead of pasting a git URL when you want a known-good starter.

Create flows (when enabled) live under `/catalog/create` and `/catalog/[id]`.

---

## Projects (`/projects`)

Org-scoped folders for workspaces and collaboration.

1. Select your org in the shell (org switcher).
2. Open **Projects**. Plan limits show how many projects you can create.
3. Owners/admins create a project by name.
4. Open a project to manage members, invites, and linked workspaces; import a repo or jump into Provision / Workspaces.

Project invites use the same invite-link pattern as org invites (`/invite/...`).

---

## Organization (`/org`)

Tenancy and access for the active org (admins/owners).

### Members and invites

1. Invite by email + role (`viewer` → `member` → `admin` → `owner`).
2. Pending invites can be revoked. Invitees accept via email link or shared URL.
3. Change roles on the members list (owners control owner role).

### SSO group → role

When OIDC SSO is enabled on the API (`OIDC_ENABLED=true`):

1. Map IdP group names (from `OIDC_GROUP_CLAIM`, default `groups`) to org roles.
2. On each SSO login, Launchpad creates or **promotes** membership for matching groups (never demotes an owner via SSO).
3. Optional global map: `OIDC_GROUP_ROLE_MAP` + `OIDC_DEFAULT_ORG_SLUG`.

Sign-in: **Sign in with SSO** on `/login` → IdP → `/auth/callback`.

### Billing / plan

If Stripe is configured, **Organization** shows plan summary (project caps, usage). Owners can start checkout or open the billing portal.

---

## Integrations (`/integrations`)

### Source control

| Integration | Path | Purpose |
| --- | --- | --- |
| GitHub | `/integrations/github` | App install: create repos, push CI, webhooks, PR status |
| GitLab | `/integrations/gitlab` | OAuth or PAT: list projects, create repos, push files |

### Collaboration

| Integration | Path | Purpose |
| --- | --- | --- |
| Slack | `/integrations/slack` | Org Incoming Webhook for Ready / Failed / TTL warning / soft cost-cap |
| Jira | `/integrations/jira` | Site URL + email + API token; auto or manual issues on provision/rebuild failure |

Slack/Jira are org-scoped; admins connect. Environment detail can create/open a Jira issue when connected. Events never fail a provision if Slack/Jira is down.

---

## Environment operations (`/environments/[id]`)

Beyond launch and destroy:

| Action | What it does |
| --- | --- |
| **Extend TTL** | Adds more lifetime within org max |
| **Pause / Resume** | Stops or restarts the preview workload without destroying the record |
| **Retry provision** | Re-queues a failed provision |
| **Deploy to cloud** | Promote a Local preview toward a cloud account flow |
| **Scan drift** | Compare live cluster state to control-plane expectations |
| **Preview analyzer** | Heuristic / Gemini analysis of failures and logs |
| **Jira** | Create or open a linked issue when Jira is connected |
| **Audits** | Control-plane audit trail for the env |

### Running instance (attach)

Cloud / VM previews with `deploy=attach` clone the workspace **linked app repo** (GitHub or GitLab) onto the host by default, or sync the workspace disk when there is no remote clone URL. **Open app** uses the VM preview URL (for example `http://<ip>:8080`). Workload image is hidden unless you supplied a real container image (platform defaults like `nginx:1.27-alpine` are not shown).

### Stage promotions (preview → staging → production)

Environments carry a `lifecycle_stage` (`preview`, `staging`, `production`). From an environment detail page:

1. **Promote to staging** / **Promote to production** creates a new environment in that stage (same workspace/git, new name).
2. Org policy (Organization → Stage promotions) controls whether staging/production need admin approval. Production requires approval by default.
3. Pending requests are approved or rejected at `/org/promotions`. Approve enqueues provision of the target environment.
4. Production (and optional permanent staging) use **no TTL** and are not reaped by the TTL worker.

This is separate from **Deploy to cloud** (local → cloud preview retarget).

### Git push rebuilds

With `WEBHOOK_SECRET` matching the GitHub App (or GitLab) webhook secret, pushes to the tracked branch rebuild the active environment (re-clone / restart on instance mode). Detail page shows latest commit SHA and whether the webhook is configured. In-app: `/docs#rebuild`.

### Sharing and PRs

- **`/p/{id}`** - shareable status page (progress, open app when Running).
- **`/pr/{number}`** - resolve GitHub PR number to the matching environment when Launchpad commented / tracked it.
- With GitHub App + PR number at Launch: commit status and PR comment when Running.

### Caps

`MAX_CONCURRENT_ENVIRONMENTS` and `PREVIEW_SOFT_COST_CAP` limit how many previews you can run and soft cost. Soft cost-cap can notify Slack when configured.

In-app notifications (header bell) surface lifecycle events without leaving the shell.

---

## Provision depth (`/provision` + workspace IDE)

Engines:

- **Terraform** / **Pulumi** - classic cloud IaC (documented in `/docs#provision`).
- **Ansible** - `infra/ansible` inventory + playbooks; run check/apply from workspace toolbar when detected.

Runtime modes:

- **Kubernetes** - manifests / Helm / Kustomize on a cluster
- **Compose** - `docker compose` from the workspace
- **Running instance** - VM or managed compute; control plane applies IaC when configured, then delivers the linked repo (clone) or workspace sync (SSH)

Kubernetes packaging modes:

- Raw manifests (`infra/k8s/manifests/`)
- Helm chart
- **Kustomize** (`infra/kustomize/...`)

Optional scaffolds (wizard / Advanced IDE):

- CI: GitHub Actions or GitLab CI
- Pipeline security: Trivy / SAST / health-rollback style toggles
- Cost options: spot / HPA / VPA / idle-shutdown style knobs written into generated infra

**Linked app repo**: on the workspace, link GitHub/GitLab so instance environments clone that branch by default. Repo **import** / scaffold still syncs the workspace tree when there is no remote URL.

Repo **import**: from Workspaces (`?import=1`) or a project, detect GitHub/GitLab repos and pull them into a workspace (often redirects from `/import`).

---

## Dockerfiles (`/dockerfiles`)

Dedicated tooling for container definitions:

1. Connect GitHub (installations list).
2. Scan a repository for Dockerfiles / stack hints.
3. Scaffold or improve a Dockerfile; review with heuristic/Gemini when configured.
4. Push scaffold bundle back to the repo and/or enqueue a registry build job and poll status.

Useful before Launch when the repo has no usable Dockerfile for preview builds.

---

## Auth and onboarding

| Flow | Path | Notes |
| --- | --- | --- |
| Register / password login | `/login` | Always available unless disabled |
| Dev login | `/login` | When `AUTH_DEV_LOGIN_ENABLED=true` |
| OIDC SSO | `/login` → IdP → `/auth/callback` | When `OIDC_ENABLED` + client configured |
| First org | `/onboarding/org` | Required before Home if you have no org |
| Accept invite | `/invite/[token]` and accept routes | Org or project membership |

---

## Hybrid Cloud

See [`hybrid-cloud.md`](./hybrid-cloud.md) and in-app `/docs#hybrid`.

---

## Related ops docs

- Local / kind: root [`README.md`](../README.md)
- OCI / homelab Compose: [`deploy/oci/README.md`](../deploy/oci/README.md)
- Hybrid agent: [`agent/README.md`](../agent/README.md)

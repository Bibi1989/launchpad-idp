# Launchpad: ICP, Value, and Gaps (one-pager)

Use this for sales conversations, investor decks, and internal prioritization. It describes **who buys**, **why they care**, and **what still blocks enterprise scale**.

---

## One-line pitch

Launchpad is an internal developer portal that combines **governed ephemeral previews**, **multi-cloud IaC workspaces**, and **hybrid agents** so platform teams can offer self-service without handing out cloud keys or stitching five tools together.

---

## Ideal customer profile (ICP)

### Primary buyer
- **Platform / DevEx / SRE lead** at a product company with 20–200 engineers
- Owns “how we ship previews and infra,” not a full Backstage program yet
- Pain: ticket-driven envs, shared cloud keys, slow Terraform/Pulumi onboarding

### Strong fit signals
- Multi-cloud or planning to be (GCP / AWS / Azure / Cloudflare)
- GitHub- or GitLab-centric delivery
- Wants **local (kind) → cloud** adoption without a big-bang migration
- Small platform team (1–5 people) that cannot maintain custom glue forever

### Weak fit (today)
- 200+ engineers with hard SAML/SCIM, SIEM, and policy-as-code requirements
- Regulated buyers that need Vault/cloud SM + External Secrets as table stakes
- Orgs that already run mature Backstage + Humanitec/Okteto + enterprise secrets

### Economic buyer
- VP Eng / Head of Platform (tool ROI: fewer tickets, faster PR feedback, less cloud waste)
- Finance cares later via FinOps (cost caps, team cost, idle pause)

---

## Value props (company outcomes)

| Outcome | What Launchpad does | Why it matters |
|--------|---------------------|----------------|
| Faster PR feedback | TTL’d previews from repo/branch, rebuild on push, GitHub PR status/comments | Devs see the change without waiting on ops |
| Safer self-service | Encrypted creds, sandbox inject, quotas, TTL, soft cost caps, org RBAC / OIDC | Self-serve without “here’s the root key” |
| Less tool sprawl | Previews + IaC (Terraform / OpenTofu / Pulumi / Ansible) + catalog in one portal | One surface instead of preview tool + IaC generator + wiki |
| Incremental adoption | Local kind first, promote to cloud when ready | Lowers adoption risk vs “all cloud on day one” |
| Hybrid reach | Outbound agent nodes (no inbound ports) + optional AI blueprints | Same portal for lab/on-prem and public cloud |

**Wedge to lead with:** PR-native preview environments (daily developer habit), then expand into golden-path catalog and governed IaC.

---

## Competitive frame (honest)

| Alternative | Launchpad angle |
|-------------|-----------------|
| Backstage-only | Catalog and docs without built-in previews + multi-cloud apply |
| Preview-only (e.g. Okteto-class) | Previews without deep IaC workspace generation |
| DIY scripts + Terraform Cloud | Works until credential sharing, TTL, catalog, and Git loop become full-time jobs |
| Big IDP suites | More complete for enterprise; heavier, slower, often overkill under ~100 eng |

Positioning line: **previews + IaC + light governance in one place**, not “replace your entire platform overnight.”

---

## Proof points you can demo

1. Launch Local (kind) from a template or git branch → Running → Open app / share `/p/:id`
2. Store cloud credentials once in Settings → provision a workspace → apply in sandbox
3. GitHub App: PR comment + commit status when preview is ready
4. Promote Local → cloud; show TTL / destroy / cost-cap story
5. Optional: enroll a hybrid agent and deploy a small blueprint

Keep demos on **real paths** (Kubernetes enabled). Simulate mode is for UI only and undercuts credibility.

---

## Gaps that limit company usefulness (roadmap)

These are adoption blockers for larger buyers, not reasons to stop selling to ICP:

1. **Identity** - OIDC today; enterprises expect SAML, SCIM, IdP MFA
2. **Policy / promote** - Quotas, TTL, and **stage promotions** (preview → staging → production with org approval gates) ship; OPA/Kyverno policy packs still ahead
3. **Secrets** - Session vault is early-stage; need Vault / cloud SM + External Secrets
4. **Audit / ops** - Per-env audit + promotion audits; need org-wide export, retention, SIEM, HA reference arch
5. **Preview realism** - Ephemeral DBs/Redis, monorepo filters, stable PR URLs, destroy-on-PR-close
6. **FinOps depth** - Soft caps exist; finance wants team budgets, Slack alerts, monthly export

Prioritize by ICP: **PR previews depth → golden paths → policy packs → SAML/SCIM**.

---

## Messaging do / don’t

**Do**
- Sell to platform teams drowning in tickets and key sharing
- Lead with time-to-preview and governed self-service
- Be explicit about roadmap for enterprise trust features

**Don’t**
- Claim full Backstage + Humanitec + Vault replacement today
- Demo simulate-only flows as production Kubernetes
- Promise compliance certification without the audit/secrets stack

---

## 30-second close

> “Launchpad gives your engineers self-serve previews and multi-cloud workspaces under org guardrails, so your platform team stops being a ticket queue. Start on local Kubernetes, grow into cloud and hybrid. We are strong for teams under ~200 engineers today; SAML/SCIM, policy packs, and deeper secrets/audit are the path to larger enterprise.”

---

## Related docs

- Product overview: `README.md`, `docs/product-guide.md`
- Enterprise detail: `docs/Launchpad-Enterprise-Roadmap.md`
- 30-day GTM checklist: `docs/go-to-market-30-days.md`
- Hybrid: `docs/hybrid-cloud.md`

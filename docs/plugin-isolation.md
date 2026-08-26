# User plugin storage + isolation model

How Launchpad lets an org add its own cloud plugin safely.

## What a "user plugin" is

A **declarative manifest** plus an **IaC bundle** - never executable control-plane code.

- **Manifest** (`app/plugins/manifest.py::PluginManifest`): metadata (id, label, credential
  fields, regions, sizes, runtime targets) + a `runner` spec that names an existing tool
  (`terraform` / `opentofu` / `pulumi` / `ansible`), the bundle-relative path to run, and a
  `var_mapping` that maps `${credentials.X}` / `${spec.Y}` into tool inputs.
- **Bundle**: the user's `.tf` / Pulumi program / `playbook.yml`, stored on disk under a
  per-org directory. Executed only by the existing IaC tools via the runner plugins.

Because the platform never imports or evaluates user Python/JS, the attack surface is
"what can this Terraform/Pulumi/Ansible do," not "what can this code do in our process."

## Storage

- **Manifests**: table `plugin_manifests` (migration `0037`), scoped by `org_id`, unique on
  `(org_id, plugin_id)`. Stores the manifest JSON + a pointer (`bundle_path`) to the bundle.
  No secrets are stored here (credentials live in the encrypted vault).
- **Bundles**: per-org directory `~/.launchpad/plugins/<org_id>/<plugin_id>/` (sibling of the
  workspaces root). One org can never see or run another org's bundle.
- **Service**: `app/services/user_plugins.py::UserPluginService` (upsert / list / delete /
  build). Manifest plugins are merged into `GET /api/v1/cloud-providers` so they appear in
  the picker next to built-ins.

## Isolation - what is enforced today

1. **No code execution.** Manifests are data; only Terraform/Pulumi/Ansible run.
2. **Path confinement.** `ManifestPlugin._safe_path` refuses any runner path that resolves
   outside the plugin's bundle root (blocks `../../etc`-style traversal).
3. **Restricted environment.** For terraform/ansible runners, `ManifestPlugin` passes a
   minimal env (`PATH` + a bundle-local `HOME`) via the runners' `base_env` parameter, so
   user IaC **cannot read control-plane secrets from the environment**. All provider auth
   is passed explicitly as mapped `-var` values, nothing implicit.
4. **Explicit credential mapping.** Only the fields the manifest declares are injected, and
   only from that org's vault.
5. **Timeouts.** Every runner has a wall-clock timeout (default 30 min IaC / 15 min Ansible).
6. **Per-org filesystem isolation.** Bundles live under `<org_id>/` directories.

## Isolation - required before untrusted/public plugins (production)

The guardrails above are defense-in-depth but **do not sandbox the tool process itself**.
Terraform/Pulumi still download and run provider plugins, and Ansible connects out over SSH.
For truly untrusted (e.g. public marketplace) plugins, add:

1. **Out-of-process execution on the hybrid agent/worker, not the API host.** Run the tool
   inside a container (or microVM: Firecracker/gVisor) per provision, so a malicious
   provider plugin cannot touch the control plane.
2. **Egress controls.** Restrict network from the sandbox to the target cloud's API
   endpoints only (deny arbitrary exfiltration).
3. **Scoped, short-lived credentials.** Inject only the credentials for this provision, with
   the narrowest scope and shortest TTL possible; never long-lived org-wide keys.
4. **Resource limits.** CPU/memory/disk caps + the existing timeouts.
5. **Review/approval workflow.** First-party plugins are trusted; org-authored plugins run
   in the org's own sandbox; public plugins require review before listing.
6. **State isolation.** Per-plugin, per-org Terraform/Pulumi backend state, never shared.

## Summary

- **Now:** safe for **first-party and same-org** plugins - no code execution, path + env +
  credential confinement, per-org storage, timeouts.
- **Next (for untrusted plugins):** containerized/microVM execution on the agent with egress
  controls and scoped credentials. The manifest contract does not change - only where the
  runner executes.

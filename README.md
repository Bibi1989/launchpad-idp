# Launchpad

Internal Developer Portal for governed ephemeral environments and multi-cloud infrastructure provisioning.

## How it works

Launchpad has two related flows:

1. **Environments** - time-boxed preview deployments from a git repo and branch. Launchpad provisions an isolated namespace, streams status and logs, rebuilds on matching git pushes, and tears down when the TTL expires or you destroy the environment.
2. **Provision** - generate a Terraform or Pulumi workspace for GCP, AWS, Azure, or Cloudflare. You attach short-lived cloud credentials per workspace, optionally bootstrap a GitHub repo, then apply the stack from an interactive sandbox terminal.

Cloud credentials are encrypted at rest and injected into the sandbox for that session. GitHub access uses a GitHub App (no personal tokens in the browser).

## Using Launchpad

Open the app at **`/`** for the product overview, then sign in (or use **Dev login** when available). You land on **Home** (`/home`); in-product guides live at **/docs**. Store cloud keys under **Settings** (`/settings`). Environments live at `/environments`.

### One-click preview (recommended)

1. Go to **Launch** (`/launch`).
2. Stay on **Local (kind)** (default) for a single-screen launch, or pick a cloud account.
3. Choose a catalog template **or** your own git repo + branch.
4. Launch - kind starts automatically when needed. When **Running**, use **Open app** for the workload URL (NodePort locally). **Status page** (`/p/{id}`) is the shareable progress link.
5. Push to the environment’s branch to rebuild when `WEBHOOK_SECRET` is configured.
6. Optional: pass a GitHub PR number when launching your repo - with the GitHub App installed, Launchpad comments + sets a commit status when Running.
7. On the environment page: **Extend TTL**, **Deploy to cloud** (from Local), Destroy, and runtime summary (image / NodePort). Concurrent preview and soft cost caps apply (`MAX_CONCURRENT_ENVIRONMENTS`, `PREVIEW_SOFT_COST_CAP`).

#### Local kind testing

```bash
make kind-down && make kind-up   # NodePorts 30080-30089 → localhost
# Then in apps/api/.env:
#   KUBERNETES_ENABLED=true
#   KUBERNETES_CONTEXT=kind-launchpad
#   PREVIEW_NODE_HOST=127.0.0.1
#   PROVISION_STEP_DELAY_SECONDS=0
# Restart API + worker, then Launch → Local (kind)
```

Open Preview opens `http://127.0.0.1:<nodePort>` (real nginx pod) once Ready.

With `KUBERNETES_ENABLED=false`, Local still runs the UI in simulate mode (portal `/p/{id}` only).

### Advanced paths

- **Provision** - start with **Dev (kind)**; the API runs `kind-up` for you and `kind-down` when you destroy the last Dev workspace. Switch the wizard to GCP/AWS/Azure/Cloudflare when ready. Choose **Simple** or **Standard** network topology (public+private with NAT) when VPC/subnets are enabled.
- **Settings** - account-level encrypted GCP/AWS/Azure/Cloudflare credentials (fallback when workspace fields are blank).
- **Workspaces** - IDE is hidden by default; click **Advanced IDE** to edit files, run kubectl/terraform, push to GitHub.
- Classic git form remains available from the empty dashboard (“advanced git form”).

Disable auto kind management with `KIND_AUTO_MANAGE=false` in `apps/api/.env` if you prefer to run `make kind-up` / `make kind-down` yourself.

## Deploy on Oracle Cloud (Always Free)

Production-ish Compose + Caddy pack for an Ampere A1 VM (control plane only; K8s previews off by default):

→ **[deploy/oci/README.md](deploy/oci/README.md)** - `cp deploy/oci/env.example deploy/oci/.env` then `make oci-up`

## Local quick start

```bash
docker compose up -d postgres redis

# API
cd apps/api && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Worker (separate terminal)
celery -A app.workers.celery_app.celery_app worker --loglevel=INFO

# Web UI
cd apps/web && npm install && npm run dev
```

- UI: http://localhost:3000
- Product docs: http://localhost:3000/docs
- API reference: http://localhost:8000/docs

Copy `apps/api/.env.example` → `.env` and fill values for your environment (see comments in that file). Run `make beat` if you need the TTL reaper locally.

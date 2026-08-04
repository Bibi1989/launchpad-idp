# Deploy Launchpad on Oracle Cloud Always Free

Host the Launchpad **control plane** (UI + API + worker + Postgres + Redis) on a single OCI VM. Preview Kubernetes stays off by default (`KUBERNETES_ENABLED=false`) so the Always Free Ampere box stays healthy.

## 1. Create the VM

1. [Oracle Cloud Console](https://cloud.oracle.com) → **Compute → Instances → Create**.
2. Shape: **VM.Standard.A1.Flex** (Ampere). Prefer **2 OCPU / 12 GB** (or more if quota allows).
3. Image: **Ubuntu 22.04** (or 24.04).
4. Networking: public subnet, assign a **public IPv4**.
5. Upload your SSH public key.

### Security list / NSG

Allow ingress:

| Port | Purpose |
|------|---------|
| 22 | SSH |
| 80 | HTTP (Caddy) |
| 443 | HTTPS (Caddy + HTTP/3) |

Do **not** expose 5432, 6379, or 8000 publicly.

## 2. Install Docker on the VM

SSH in, then:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
# log out and back in so docker works without sudo
```

Confirm:

```bash
docker version
docker compose version
```

## 3. Clone and configure

```bash
git clone <your-launchpad-repo-url> launchpad
cd launchpad
cp deploy/oci/env.example deploy/oci/.env
nano deploy/oci/.env   # or vim
```

Set at least:

- `POSTGRES_PASSWORD`, `JWT_SECRET`, `SECRETS_ENCRYPTION_KEY` - long random strings
- `LAUNCHPAD_PUBLIC_ORIGIN` - how users open the app, e.g. `http://130.61.x.x` or `https://launchpad.example.com`
- `LAUNCHPAD_SITE_ADDRESS`:
  - no DNS yet → `:80`
  - DNS A record → your hostname (Caddy will request a Let's Encrypt cert); set `ACME_EMAIL`
- `AUTH_DEV_LOGIN_ENABLED=false` for anything internet-facing

## 4. Start the stack

From the **repo root**:

```bash
docker compose -f deploy/oci/docker-compose.yml --env-file deploy/oci/.env up -d --build
```

Or:

```bash
make oci-up
```

Follow logs:

```bash
docker compose -f deploy/oci/docker-compose.yml --env-file deploy/oci/.env logs -f
```

Open `LAUNCHPAD_PUBLIC_ORIGIN` in a browser. Register a user (dev login is off by default).

## 5. Optional: real domain + HTTPS

1. Point an A (and AAAA if you have IPv6) record at the VM public IP.
2. In `.env`:
   - `LAUNCHPAD_SITE_ADDRESS=launchpad.example.com`
   - `LAUNCHPAD_PUBLIC_ORIGIN=https://launchpad.example.com`
   - `ACME_EMAIL=you@example.com`
3. Recreate Caddy:

```bash
docker compose -f deploy/oci/docker-compose.yml --env-file deploy/oci/.env up -d caddy
```

## Architecture

```text
Internet → :80/:443 Caddy
              ├─ /api/v1/*  → api:8000   (FastAPI + WS)
              └─ /*         → web:3000   (Nuxt)
         api / worker / beat → postgres + redis (private network)
```

Same-origin `/api/v1` avoids browser CORS issues; WebSockets upgrade through Caddy.

## Ops cheatsheet

```bash
# Restart after .env changes that affect all services
make oci-up

# Stop
make oci-down

# DB migrations re-run on api container start (alembic upgrade head)

# Disk usage
docker system df
```

## Limits of Always Free

| Capability | On this stack |
|------------|----------------|
| Portal UI, auth, workspaces, IaC files | Yes |
| Real preview pods (kind / k3s) | Not enabled; needs more RAM / a second VM |
| Heavy concurrent Celery builds | Keep concurrency low |

To enable Kubernetes later, install k3s on a second A1 (or the same host if you have 24 GB), set `KUBERNETES_ENABLED=true`, and mount a kubeconfig into `api` / `worker` - that is out of scope for this minimal pack.

## Troubleshooting

- **Out of capacity** creating A1: try another region or availability domain.
- **Caddy TLS fails**: DNS must resolve to this VM; ports 80/443 must be open from the internet.
- **CORS / login errors**: `LAUNCHPAD_PUBLIC_ORIGIN` must exactly match the URL in the browser (scheme + host, no trailing slash).
- **502 from Caddy**: wait for `api` healthcheck; `docker compose … logs api`.

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

## Limits of Always Free / small VMs

| Capability | On this stack |
|------------|----------------|
| Portal UI, auth, workspaces, IaC files | Yes |
| Real preview pods (k3d / k3s via host Docker) | Yes when `KUBERNETES_ENABLED=true` (16GB+ laptop/server recommended) |
| Tiny Always Free Ampere only | Set `KUBERNETES_ENABLED=false` to keep the box healthy |

### Local Sandbox on this compose stack

`api` / `worker` images (`deploy/oci/Dockerfile.api`) include `kubectl`, `k3d`, and the Docker CLI. Compose mounts:

- host `${DOCKER_SOCK:-/var/run/docker.sock}` → `/var/run/docker.sock`
- shared `kube_data` volume at `/kube` (`KUBECONFIG=/kube/config`)
- `scripts/` → `/opt/launchpad/scripts` (`KIND_SCRIPTS_DIR`)

Requirements on the **host**:

1. Docker Engine or Docker Desktop running
2. Enough RAM for the control plane + a k3d cluster (~16GB comfortable)
3. After changing env, recreate api/worker:
   `docker compose -f deploy/oci/docker-compose.yml --env-file deploy/oci/.env up -d --build api worker`
4. On Launch, click **Refresh** until status is not `tools_missing`

Open-app NodePorts use host ports `30080-30089` (created by k3d on the Docker host).

#### Host-based previews (Cloudflare `ws-*.yourdomain`)

`scripts/k3s-up.sh` publishes ingress HTTP on host port **3080** (NodePort 30090) and installs **ingress-nginx**.

1. Recreate the cluster once so port 3080 is mapped: `k3d cluster delete launchpad` then Launch / `scripts/k3s-up.sh`
2. In `deploy/oci/.env`: `USE_CLOUDFLARE_TUNNEL=true`, `PREVIEW_BASE_DOMAIN=launchpad-idp.online`
3. Cloudflare Tunnel public hostnames:
   - `launchpad-idp.online` → `http://caddy:80`
   - `*.launchpad-idp.online` → `http://host.docker.internal:3080`
4. DNS: wildcard `*` (or `*.preview`) CNAME to the same tunnel (Proxied)

## Troubleshooting

- **Out of capacity** creating A1: try another region or availability domain.
- **Caddy TLS fails**: DNS must resolve to this VM; ports 80/443 must be open from the internet.
- **CORS / login errors**: `LAUNCHPAD_PUBLIC_ORIGIN` must exactly match the URL in the browser (scheme + host, no trailing slash).
- **502 from Caddy**: wait for `api` healthcheck; `docker compose … logs api`.

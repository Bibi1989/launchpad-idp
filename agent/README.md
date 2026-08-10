# Launchpad hybrid agent

A lightweight Python daemon that turns any self-hosted / homelab Linux host into
a secure Launchpad deployment target. It dials an **outbound** WSS tunnel to the
control plane, so no inbound ports, port-forwarding, or public IP are required.

Product overview (enrollment UI, AI provisioner, config):
**[docs/hybrid-cloud.md](../docs/hybrid-cloud.md)** · in-app **Hybrid Cloud** at `/hybrid`.

## How it works

1. An operator enrolls a node in the dashboard (or `POST /api/v1/nodes`) and gets a
   one-line installer with a single-use token:
   ```bash
   curl -sSL https://launchpad.example.com/install.sh | TOKEN=lp_xxx sh
   ```
2. On first boot the agent exchanges the token at `POST /api/v1/nodes/register`
   for a durable per-node HMAC secret, stored in `AGENT_STATE_DIR/credentials.json`.
3. It connects to `wss://.../api/v1/ws/nodes/connect`, authenticating with an
   HMAC signature over `{node_id}.{ts}.{nonce}` (replay window: 60s).
4. Every ~10s it pushes a telemetry heartbeat (CPU / RAM / disk / Docker status +
   running containers) and executes dispatched commands (pull image, run / stop /
   restart container, collect logs) against the local Docker daemon.

## Run locally (without Docker packaging)

```bash
pip install -r requirements.txt
LAUNCHPAD_URL=http://localhost:8000 TOKEN=lp_xxx AGENT_STATE_DIR=./state python main.py
```

Use the API origin for `LAUNCHPAD_URL` (for example `:8000`), not the Nuxt UI origin.

## Build the image

```bash
docker build -t ghcr.io/launchpad/agent:latest agent/
```

## Environment

| Variable          | Required | Description                                        |
| ----------------- | -------- | -------------------------------------------------- |
| `LAUNCHPAD_URL`   | yes      | Control-plane base URL                             |
| `TOKEN`           | first run| Single-use enrollment token (`lp_...`)             |
| `AGENT_STATE_DIR` | no       | Credential store (default `/var/lib/launchpad-agent`) |

Security: the agent never accepts inbound connections and authenticates with a
per-node secret (not a user JWT). Revoking a node in the dashboard drops its live
tunnel and invalidates the secret.

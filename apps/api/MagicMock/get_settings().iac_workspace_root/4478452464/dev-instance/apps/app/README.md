# app - Node.js/Express

Launchpad-generated Node.js/Express mini-application. It is containerized, wired to
its Kubernetes manifests under `infra/k8s/manifests/`, and immediately runnable
on a local Kind cluster.

## Endpoints

- `GET /` - live health dashboard
- `GET /health` - liveness probe (process is up)
- `GET /ready` - readiness probe (503 when a configured dependency is down)
- `GET /info` - application metadata
- `GET /api/status` - full JSON status (app + Kubernetes + database + Redis)

## Configured dependencies

_None configured._

The application reads connection strings from the workload Secret
(`app-secrets`) and deployment metadata from the downward API
(`POD_NAME`, `POD_NAMESPACE`, `REPLICA_COUNT`, …).

## Run on Kind (no registry required)

From the workspace root:

```bash
# Build the image, load it into the Kind cluster, apply manifests, wait for rollout
./scripts/deploy-kind.sh

# Then browse the app:
kubectl -n <namespace> port-forward svc/app 8080:80
# open http://127.0.0.1:8080/
```

Individual steps are also available:

```bash
./scripts/build-image.sh   # docker build -t app:latest apps/app
./scripts/kind-load.sh     # kind load docker-image app:latest
kubectl apply -f infra/k8s/manifests/ -R
```

## Run locally with Docker

```bash
docker build -t app:latest apps/app
docker run --rm -p 8080:8080 app:latest
# open http://127.0.0.1:8080/
```

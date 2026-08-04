#!/usr/bin/env bash
# Create a local k3s cluster (via k3d) for Launchpad preview / Dev workspaces.
# k3d runs real k3s inside Docker - the macOS-friendly way to run k3s locally.
# Maps a small NodePort range to localhost so Open Preview hits real pods, matching
# the kind engine's extraPortMappings behaviour.
set -euo pipefail

CLUSTER_NAME="${KIND_CLUSTER_NAME:-${LOCAL_CLUSTER_NAME:-launchpad}}"
CONTEXT="k3d-${CLUSTER_NAME}"
# Keep this range small - a large host-port map is slow to bind on Docker Desktop.
PORT_MIN="${PREVIEW_NODE_PORT_MIN:-30080}"
PORT_MAX="${PREVIEW_NODE_PORT_MAX:-30089}"
# Optional pin, e.g. rancher/k3s:v1.31.5-k3s1 - empty uses k3d's default.
K3S_IMAGE="${K3D_NODE_IMAGE:-}"
PRELOAD_IMAGE="${K3D_PRELOAD_IMAGE:-1}"
LOCK_DIR="${TMPDIR:-/tmp}/launchpad-k3s-${CLUSTER_NAME}.lockdir"
LOCK_WAIT_SECONDS="${K3S_LOCK_WAIT_SECONDS:-300}"

if ! command -v k3d >/dev/null 2>&1; then
  echo "k3d is not installed. Install: https://k3d.io/#installation (brew install k3d)" >&2
  exit 1
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is not installed." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed / not on PATH." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not reachable. Start Docker Desktop and retry." >&2
  exit 1
fi

if [[ "${PORT_MAX}" -lt "${PORT_MIN}" ]]; then
  echo "PREVIEW_NODE_PORT_MAX (${PORT_MAX}) must be >= PREVIEW_NODE_PORT_MIN (${PORT_MIN})" >&2
  exit 1
fi

acquire_lock() {
  local waited=0
  while ! mkdir "${LOCK_DIR}" 2>/dev/null; do
    if [[ "${waited}" -ge "${LOCK_WAIT_SECONDS}" ]]; then
      echo "Timed out waiting for k3s lock (${LOCK_DIR})." >&2
      echo "If nothing is creating a cluster, remove it: rm -rf '${LOCK_DIR}'" >&2
      exit 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  # shellcheck disable=SC2064
  trap 'rm -rf "${LOCK_DIR}"' EXIT
}

acquire_lock

cluster_exists() {
  k3d cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "${CLUSTER_NAME}"
}

cluster_ready() {
  cluster_exists \
    && kubectl get nodes --context "${CONTEXT}" \
         -o 'jsonpath={.items[*].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null \
       | grep -q "True"
}

if cluster_ready; then
  echo "k3s cluster '${CLUSTER_NAME}' is already ready (context: ${CONTEXT})"
else
  if cluster_exists; then
    echo "k3s cluster '${CLUSTER_NAME}' exists but is unhealthy - recreating…"
    k3d cluster delete "${CLUSTER_NAME}" || true
    sleep 2
  fi

  # Bind the host NodePort range straight to the server node so NodePort Services
  # on those ports are reachable at 127.0.0.1 - the same contract as kind.
  CREATE_ARGS=(
    cluster create "${CLUSTER_NAME}"
    --servers 1
    --port "${PORT_MIN}-${PORT_MAX}:${PORT_MIN}-${PORT_MAX}@server:0"
    # Match the kind engine: no bundled ingress controller (Launchpad installs
    # its own when requested). Keep servicelb for optional LoadBalancer support.
    --k3s-arg "--disable=traefik@server:0"
    --wait
    --timeout 150s
  )
  # When k3d runs inside the api/worker container (Docker socket mount), the
  # kube-apiserver must be reachable via the host gateway name, not 127.0.0.1.
  #
  # IMPORTANT: the apiserver PUBLISH/bind address and the CLIENT (kubeconfig)
  # address are two different things and must not be conflated. host.docker.internal
  # is a gateway ALIAS (e.g. 192.168.65.254 on Docker Desktop) - reachable *from*
  # containers, but NOT a real interface address the host can bind a published
  # port to. Binding it fails with "can't assign requested address".
  #
  # So: bind the port on a real, bindable host address (0.0.0.0 works on both
  # Docker Desktop and Linux), advertise the client-facing name as a TLS SAN, and
  # rewrite the kubeconfig server to K3D_API_HOST after create (see below).
  if [[ -n "${K3D_API_HOST:-}" ]]; then
    API_PORT="${K3D_API_PORT:-6445}"
    API_BIND_HOST="${K3D_API_BIND_HOST:-0.0.0.0}"
    CREATE_ARGS+=(--api-port "${API_BIND_HOST}:${API_PORT}")
    CREATE_ARGS+=(--k3s-arg "--tls-san=${K3D_API_HOST}@server:0")
  fi
  if [[ -n "${K3S_IMAGE}" ]]; then
    CREATE_ARGS+=(--image "${K3S_IMAGE}")
  fi

  echo "Creating k3s cluster '${CLUSTER_NAME}' with NodePort mappings ${PORT_MIN}-${PORT_MAX}…"
  if ! k3d "${CREATE_ARGS[@]}"; then
    echo >&2
    echo "k3d cluster create failed. Common fixes on macOS / Docker Desktop:" >&2
    echo "  1. make k3s-down && sleep 3 && make k3s-up" >&2
    echo "  2. Remove stale clusters: k3d cluster list && k3d cluster delete <name>" >&2
    echo "  3. Give Docker more CPUs/RAM, then retry" >&2
    echo "  4. Narrow ports: PREVIEW_NODE_PORT_MAX=30089" >&2
    exit 1
  fi
fi

# k3d merges + switches kubeconfig context on create. When we bound the apiserver
# to a generic address (0.0.0.0), k3d writes that unreachable host into the
# kubeconfig - repoint it at the client-facing gateway name so api/worker
# containers can actually reach the apiserver (matches the --tls-san above).
if [[ -n "${K3D_API_HOST:-}" ]]; then
  echo "Pointing kubeconfig cluster '${CONTEXT}' at ${K3D_API_HOST}:${K3D_API_PORT:-6445}…"
  kubectl config set-cluster "${CONTEXT}" \
    --server="https://${K3D_API_HOST}:${K3D_API_PORT:-6445}" >/dev/null
fi

# k3d merges + switches kubeconfig context on create; make sure it exists.
kubectl cluster-info --context "${CONTEXT}"

# Prefetch workload image (best-effort; multi-arch digests often fail on Apple Silicon).
if [[ "${PRELOAD_IMAGE}" != "0" ]]; then
  IMAGE="${DEFAULT_WORKLOAD_IMAGE:-nginx:1.27-alpine}"
  echo "Importing ${IMAGE} into k3s (best-effort)…"
  if docker pull --platform linux/amd64 "${IMAGE}" >/dev/null 2>&1 \
    || docker pull "${IMAGE}" >/dev/null 2>&1; then
    if ! k3d image import "${IMAGE}" -c "${CLUSTER_NAME}"; then
      echo "Warning: could not preload ${IMAGE} into k3s (non-fatal)." >&2
    fi
  else
    echo "Warning: could not pull ${IMAGE} (non-fatal)." >&2
  fi
fi

cat <<EOF

k3s cluster ready (context: ${CONTEXT}).

Enable real Kubernetes provisioning in apps/api/.env:

  KUBERNETES_ENABLED=true
  KUBERNETES_IN_CLUSTER=false
  LOCAL_K8S_ENGINE=k3s
  KUBERNETES_CONTEXT=${CONTEXT}
  PREVIEW_NODE_HOST=127.0.0.1
  PREVIEW_NODE_PORT_MIN=${PORT_MIN}
  PREVIEW_NODE_PORT_MAX=${PORT_MAX}
  PROVISION_STEP_DELAY_SECONDS=0

Then restart the API and Celery worker.

Open Preview / Dev sandboxes use this cluster automatically when managed by the API.
EOF

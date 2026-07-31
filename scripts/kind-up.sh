#!/usr/bin/env bash
# Create a local kind cluster for Launchpad preview / Dev (kind) workspaces.
# Maps a small NodePort range to localhost so Open Preview hits real pods.
set -euo pipefail

CLUSTER_NAME="${KIND_CLUSTER_NAME:-launchpad}"
CONTEXT="kind-${CLUSTER_NAME}"
# Keep this range small — Docker Desktop + many hostPort maps often breaks kubeadm init.
PORT_MIN="${PREVIEW_NODE_PORT_MIN:-30080}"
PORT_MAX="${PREVIEW_NODE_PORT_MAX:-30089}"
# Optional pin, e.g. kindest/node:v1.32.2 — empty uses kind's default.
NODE_IMAGE="${KIND_NODE_IMAGE:-}"
PRELOAD_IMAGE="${KIND_PRELOAD_IMAGE:-1}"
LOCK_DIR="${TMPDIR:-/tmp}/launchpad-kind-${CLUSTER_NAME}.lockdir"
LOCK_WAIT_SECONDS="${KIND_LOCK_WAIT_SECONDS:-300}"

if ! command -v kind >/dev/null 2>&1; then
  echo "kind is not installed. Install: https://kind.sigs.k8s.io/docs/user/quick-start/#installation" >&2
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

PORT_COUNT=$((PORT_MAX - PORT_MIN + 1))
if [[ "${PORT_COUNT}" -gt 10 ]]; then
  echo "Warning: mapping ${PORT_COUNT} host ports can fail on Docker Desktop. Prefer <=10 (e.g. 30080-30089)." >&2
fi

acquire_lock() {
  local waited=0
  while ! mkdir "${LOCK_DIR}" 2>/dev/null; do
    if [[ "${waited}" -ge "${LOCK_WAIT_SECONDS}" ]]; then
      echo "Timed out waiting for kind lock (${LOCK_DIR})." >&2
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

cluster_ready() {
  kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}" \
    && kubectl cluster-info --context "${CONTEXT}" >/dev/null 2>&1 \
    && kubectl get pods -n kube-system --context "${CONTEXT}" -l component=kube-controller-manager 2>/dev/null | grep -q "Running"
}

if cluster_ready; then
  echo "kind cluster '${CLUSTER_NAME}' is already ready (context: ${CONTEXT})"
else
  if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
    echo "kind cluster '${CLUSTER_NAME}' exists but is unhealthy — recreating…"
    kind delete cluster --name "${CLUSTER_NAME}" || true
    # Give Docker Desktop a moment to release hostPort binds.
    sleep 2
  fi

  CONFIG_FILE="$(mktemp -t launchpad-kind-XXXXXX.yaml)"
  cleanup_all() {
    rm -f "${CONFIG_FILE}"
    rm -rf "${LOCK_DIR}"
  }
  trap cleanup_all EXIT

  {
    cat <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
EOF
    if [[ -n "${NODE_IMAGE}" ]]; then
      echo "    image: ${NODE_IMAGE}"
    fi
    cat <<EOF
    extraPortMappings:
EOF
    port="${PORT_MIN}"
    while [[ "${port}" -le "${PORT_MAX}" ]]; do
      cat <<EOF
      - containerPort: ${port}
        hostPort: ${port}
        protocol: TCP
EOF
      port=$((port + 1))
    done
  } > "${CONFIG_FILE}"

  echo "Creating kind cluster '${CLUSTER_NAME}' with NodePort mappings ${PORT_MIN}-${PORT_MAX}…"
  if ! kind create cluster --name "${CLUSTER_NAME}" --config "${CONFIG_FILE}"; then
    echo >&2
    echo "kind create failed. Common fixes on macOS / Docker Desktop:" >&2
    echo "  1. make kind-down && sleep 3 && make kind-up" >&2
    echo "  2. Quit other kind clusters: kind get clusters && kind delete cluster --name <name>" >&2
    echo "  3. Give Docker more CPUs/RAM, then retry" >&2
    echo "  4. Narrow ports: PREVIEW_NODE_PORT_MAX=30089" >&2
    exit 1
  fi
fi

kubectl cluster-info --context "${CONTEXT}"

# Prefetch workload image (best-effort; multi-arch digests often fail on Apple Silicon).
if [[ "${PRELOAD_IMAGE}" != "0" ]] && command -v docker >/dev/null 2>&1; then
  IMAGE="${DEFAULT_WORKLOAD_IMAGE:-nginx:1.27-alpine}"
  echo "Loading ${IMAGE} into kind (best-effort)…"
  if docker pull --platform linux/amd64 "${IMAGE}" >/dev/null 2>&1 \
    || docker pull "${IMAGE}" >/dev/null 2>&1; then
    if ! kind load docker-image "${IMAGE}" --name "${CLUSTER_NAME}"; then
      echo "Warning: could not preload ${IMAGE} into kind (non-fatal)." >&2
    fi
  else
    echo "Warning: could not pull ${IMAGE} (non-fatal)." >&2
  fi
fi

cat <<EOF

Kind cluster ready (context: ${CONTEXT}).

Enable real Kubernetes provisioning in apps/api/.env:

  KUBERNETES_ENABLED=true
  KUBERNETES_IN_CLUSTER=false
  KUBERNETES_CONTEXT=${CONTEXT}
  PREVIEW_NODE_HOST=127.0.0.1
  PREVIEW_NODE_PORT_MIN=${PORT_MIN}
  PREVIEW_NODE_PORT_MAX=${PORT_MAX}
  PROVISION_STEP_DELAY_SECONDS=0

Then restart the API and Celery worker.

Open Preview / Dev (kind) sandboxes use this cluster automatically when managed by the API.
EOF

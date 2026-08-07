#!/usr/bin/env bash
# Ensure ingress-nginx is installed for Cloudflare / Host-based preview URLs.
# Expects PREVIEW_INGRESS_NODE_PORT (default 30090) to be published on the k3d server
# as host PREVIEW_INGRESS_HTTP_PORT (default 3080) (see scripts/k3s-up.sh).
set -euo pipefail

CONTEXT="${1:-${KUBECONFIG_CONTEXT:-k3d-launchpad}}"
NODE_PORT="${PREVIEW_INGRESS_NODE_PORT:-30090}"
INGRESS_NS=ingress-nginx
MANIFEST_URL="${INGRESS_NGINX_MANIFEST_URL:-https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.3/deploy/static/provider/baremetal/deploy.yaml}"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is required to install ingress-nginx" >&2
  exit 1
fi

kubectl_ctx() {
  kubectl --context "${CONTEXT}" "$@"
}

already_ready=0
if kubectl_ctx get ingressclass nginx >/dev/null 2>&1 \
  && kubectl_ctx -n "${INGRESS_NS}" get svc ingress-nginx-controller >/dev/null 2>&1; then
  # Skip patch + long rollout wait when the controller is already Available.
  if kubectl_ctx -n "${INGRESS_NS}" get deploy ingress-nginx-controller \
       -o jsonpath='{.status.availableReplicas}' 2>/dev/null | grep -Eq '^[1-9]'; then
    echo "ingress-nginx already ready (context: ${CONTEXT})"
    already_ready=1
  else
    echo "ingress-nginx present but not Available yet (context: ${CONTEXT})"
  fi
fi

if [[ "${already_ready}" -eq 0 ]]; then
  if ! kubectl_ctx get ingressclass nginx >/dev/null 2>&1 \
    || ! kubectl_ctx -n "${INGRESS_NS}" get svc ingress-nginx-controller >/dev/null 2>&1; then
    echo "Installing ingress-nginx into ${CONTEXT}…"
    if ! kubectl_ctx apply -f "${MANIFEST_URL}"; then
      echo "Failed to apply ingress-nginx manifest from ${MANIFEST_URL}" >&2
      echo "Check network access from this host, or apply a local copy." >&2
      exit 1
    fi
  fi

  # Wait for the controller Service, then pin HTTP NodePort for Cloudflare Tunnel.
  for _ in $(seq 1 60); do
    if kubectl_ctx -n "${INGRESS_NS}" get svc ingress-nginx-controller >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done

  if ! kubectl_ctx -n "${INGRESS_NS}" get svc ingress-nginx-controller >/dev/null 2>&1; then
    echo "ingress-nginx-controller Service not found after install" >&2
    exit 1
  fi

  # Force NodePort + fixed http nodePort so host mapping 3080:30090 stays stable.
  kubectl_ctx -n "${INGRESS_NS}" patch svc ingress-nginx-controller --type merge -p "{
    \"spec\": {
      \"type\": \"NodePort\",
      \"ports\": [
        {\"name\": \"http\", \"port\": 80, \"targetPort\": \"http\", \"protocol\": \"TCP\", \"nodePort\": ${NODE_PORT}},
        {\"name\": \"https\", \"port\": 443, \"targetPort\": \"https\", \"protocol\": \"TCP\", \"nodePort\": 30091}
      ]
    }
  }" >/dev/null

  kubectl_ctx -n "${INGRESS_NS}" rollout status deployment/ingress-nginx-controller --timeout=90s || true
fi

echo "ingress-nginx ready. HTTP NodePort=${NODE_PORT} (map host PREVIEW_INGRESS_HTTP_PORT → this NodePort)."
kubectl_ctx -n "${INGRESS_NS}" get svc ingress-nginx-controller

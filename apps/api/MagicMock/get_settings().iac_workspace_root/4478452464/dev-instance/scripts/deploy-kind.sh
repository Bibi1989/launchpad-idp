#!/usr/bin/env bash
# One-shot: build -> load into Kind -> apply manifests -> wait for rollout.
# Requires: docker, kind, kubectl. No remote container registry needed.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${IMAGE:-app:latest}"
CLUSTER="${KIND_CLUSTER:-launchpad}"
CONTEXT="apps/app"
NAMESPACE="lp-dev-instance"

echo "==> [1/4] Building image ${IMAGE}"
docker build -t "${IMAGE}" "${CONTEXT}"

echo "==> [2/4] Loading image into kind cluster ${CLUSTER}"
kind load docker-image "${IMAGE}" --name "${CLUSTER}"

echo "==> [3/4] Applying manifests"
kubectl apply -f infra/k8s/manifests/ -R

echo "==> [4/4] Waiting for all deployments to become Available"
# Namespace-wide: waits for every generated Deployment (launch-web, launch-server,
# postgres, …) rather than a hardcoded deployment/app.
kubectl -n "${NAMESPACE}" wait --for=condition=Available --timeout=180s deployment --all

echo ""
echo "==> Done. Deployments + Services:"
kubectl -n "${NAMESPACE}" get deploy,svc
echo "==> Port-forward a service, e.g.:"
echo "    kubectl -n ${NAMESPACE} port-forward svc/app 8080:80"

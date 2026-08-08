#!/usr/bin/env bash
# Load the locally-built image into the Kind cluster (no registry required).
set -euo pipefail
IMAGE="${IMAGE:-app:latest}"
CLUSTER="${KIND_CLUSTER:-launchpad}"
echo "==> kind load docker-image ${IMAGE} --name ${CLUSTER}"
kind load docker-image "${IMAGE}" --name "${CLUSTER}"
echo "==> Loaded ${IMAGE} into kind cluster ${CLUSTER}"

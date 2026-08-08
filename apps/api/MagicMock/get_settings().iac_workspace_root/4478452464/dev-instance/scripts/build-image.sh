#!/usr/bin/env bash
# Build the application container image locally.
set -euo pipefail
IMAGE="${IMAGE:-app:latest}"
CONTEXT="apps/app"
echo "==> docker build -t ${IMAGE} ${CONTEXT}"
docker build -t "${IMAGE}" "${CONTEXT}"
echo "==> Built ${IMAGE}"

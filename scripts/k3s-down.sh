#!/usr/bin/env bash
# Delete the Launchpad k3s cluster (via k3d).
set -euo pipefail

CLUSTER_NAME="${KIND_CLUSTER_NAME:-${LOCAL_CLUSTER_NAME:-launchpad}}"

if ! command -v k3d >/dev/null 2>&1; then
  echo "k3d is not installed." >&2
  exit 1
fi

if k3d cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "${CLUSTER_NAME}"; then
  k3d cluster delete "${CLUSTER_NAME}"
  echo "Deleted k3s cluster '${CLUSTER_NAME}'"
else
  echo "No k3s cluster named '${CLUSTER_NAME}'"
fi

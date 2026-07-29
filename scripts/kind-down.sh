#!/usr/bin/env bash
# Delete the Launchpad kind cluster.
set -euo pipefail

CLUSTER_NAME="${KIND_CLUSTER_NAME:-launchpad}"

if ! command -v kind >/dev/null 2>&1; then
  echo "kind is not installed." >&2
  exit 1
fi

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  kind delete cluster --name "${CLUSTER_NAME}"
  echo "Deleted kind cluster '${CLUSTER_NAME}'"
else
  echo "No kind cluster named '${CLUSTER_NAME}'"
fi

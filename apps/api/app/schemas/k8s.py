from __future__ import annotations

from enum import Enum


class DeployMode(str, Enum):
    """How an environment applies workload resources to a cluster."""

    PREVIEW = "preview"
    """Programmatic preview deploy via the control plane (NodePort + git metadata)."""

    MANIFEST = "manifest"
    """Apply Kubernetes manifests from a linked workspace (``infra/k8s/manifests/``)."""

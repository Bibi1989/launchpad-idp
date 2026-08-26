from __future__ import annotations

from enum import Enum


class DeployMode(str, Enum):
    """How an environment applies workload resources at preview time."""

    PREVIEW = "preview"
    """Programmatic preview deploy via the control plane (NodePort + git metadata)."""

    MANIFEST = "manifest"
    """Apply Kubernetes manifests from a linked workspace (``infra/k8s/manifests/``)."""

    COMPOSE = "compose"
    """Run ``docker compose`` from the linked workspace (local Docker only)."""

    DOCKER_COMPOSE = "docker-compose"
    """Run ``docker compose`` from the linked workspace (local Docker only)."""

    DOCKER_COMPOSE_UNDERSCORE = "docker_compose"
    """Run ``docker compose`` from the linked workspace (local Docker only)."""

    ATTACH = "attach"
    """Attach to an existing runtime (kube context, serverless service, or endpoint)."""


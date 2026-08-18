"""Dynamic preview ingress route generator.

Auto-issues a per-environment preview subdomain (``pr-{id}.preview.{base}``) and
builds the Kubernetes Ingress manifest that routes it to the preview Service.

Additive and self-contained: this generates a NEW preview Ingress object. It does
not read or mutate any production ingress controller, the ``ws-*`` workspace
ingress (``preview_urls``), or the manifest-deploy ingress patcher - those paths
are untouched.
"""

from __future__ import annotations

import re
from uuid import UUID

from app.core.config import Settings, get_settings

_SAFE_HOST_LABEL = re.compile(r"[^a-z0-9-]")


def preview_subdomain_host(
    environment_id: UUID | str, *, settings: Settings | None = None
) -> str | None:
    """Return ``pr-{id}.preview.{base}`` (or None when no base domain is set)."""
    cfg = settings or get_settings()
    base = (cfg.preview_base_domain or "").strip().strip(".")
    if not base:
        return None
    ident = _SAFE_HOST_LABEL.sub("-", str(environment_id).lower()).strip("-")[:63] or "env"
    template = (cfg.preview_subdomain_template or "pr-{id}.preview.{base}").strip()
    host = template.format(id=ident, base=base).strip().strip(".")
    # Collapse accidental double dots from an empty template segment.
    return re.sub(r"\.{2,}", ".", host)


def preview_subdomain_url(
    environment_id: UUID | str, *, settings: Settings | None = None
) -> str | None:
    cfg = settings or get_settings()
    host = preview_subdomain_host(environment_id, settings=cfg)
    if not host:
        return None
    scheme = "https" if cfg.preview_tunnel_active else "http"
    return f"{scheme}://{host}"


def generate_preview_ingress(
    *,
    environment_id: UUID | str,
    namespace: str,
    service_name: str,
    service_port: int,
    path: str = "/",
    settings: Settings | None = None,
) -> dict:
    """Build a Kubernetes Ingress manifest for the environment's preview subdomain.

    Returns a plain dict (``networking.k8s.io/v1`` Ingress) ready to apply with the
    Python client (``utils.create_from_dict``) or serialize to YAML. Raises
    ``ValueError`` when no ``preview_base_domain`` is configured (no host to issue).
    """
    cfg = settings or get_settings()
    host = preview_subdomain_host(environment_id, settings=cfg)
    if not host:
        raise ValueError(
            "Cannot issue a preview subdomain: PREVIEW_BASE_DOMAIN is not configured."
        )
    ingress_class = (getattr(cfg, "preview_ingress_class", None) or "nginx").strip() or "nginx"
    name = f"preview-{_SAFE_HOST_LABEL.sub('-', str(environment_id).lower()).strip('-')[:52]}"

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "launchpad.io/environment-id": str(environment_id),
                "launchpad.io/preview-route": "true",
                "launchpad.io/managed-by": "launchpad-idp",
            },
            "annotations": {
                "nginx.ingress.kubernetes.io/ssl-redirect": "false",
            },
        },
        "spec": {
            "ingressClassName": ingress_class,
            "rules": [
                {
                    "host": host,
                    "http": {
                        "paths": [
                            {
                                "path": path or "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": service_name,
                                        "port": {"number": int(service_port)},
                                    }
                                },
                            }
                        ]
                    },
                }
            ],
        },
    }

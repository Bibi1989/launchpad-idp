"""Expand per-cloud services into built-in catalog plugins.

GCP / AWS / Azure / Cloudflare (and other catalogued clouds) remain Settings
*accounts*. Each concrete service (GKE, EKS, Cloud Run, ...) is a plugin that
inherits that account's keys via ``parent_cloud``.
"""

from __future__ import annotations

from typing import Any

from app.providers.provider_services import SERVICE_CATALOG

PARENT_CLOUDS = (
    "gcp",
    "aws",
    "azure",
    "cloudflare",
    "hetzner",
    "digitalocean",
    "linode",
    "railway",
    "render",
)

_RUNTIME_TARGETS: dict[str, list[str]] = {
    "kubernetes": ["kubernetes"],
    "docker": ["docker_host"],
    "vm": ["vm"],
    "paas": ["paas"],
}

_ICONS: dict[str, str] = {
    "gke": "hub",
    "cloud-run": "directions_run",
    "gce-docker": "deployed_code",
    "gce": "computer",
    "eks": "hub",
    "ecs-fargate": "sailing",
    "ec2-docker": "deployed_code",
    "ec2": "computer",
    "aks": "hub",
    "container-apps": "view_in_ar",
    "aci": "inventory_2",
    "vm-docker": "deployed_code",
    "azure-vm": "desktop_windows",
    "workers": "bolt",
    "pages": "web",
    "k3s": "hub",
    "server-docker": "deployed_code",
    "cloud-server": "dns",
    "doks": "hub",
    "app-platform": "apps",
    "droplet-docker": "deployed_code",
    "droplet": "water_drop",
    "lke": "hub",
    "linode-docker": "deployed_code",
    "linode-instance": "computer",
    "railway-service": "rocket_launch",
    "render-web": "web",
    "render-worker": "precision_manufacturing",
}

_PARENT_ICONS: dict[str, str] = {
    "gcp": "cloud_sync",
    "aws": "cloud_upload",
    "azure": "cloud_queue",
    "cloudflare": "cyclone",
    "hetzner": "dns",
    "digitalocean": "water_drop",
    "linode": "computer",
    "railway": "rocket_launch",
    "render": "web",
}


def split_plugin_id(provider_id: str) -> tuple[str, str | None]:
    """Map ``gcp-gke`` -> ``(gcp, gke)``. Parent-only ids return ``(gcp, None)``."""
    pid = (provider_id or "").strip().lower()
    if pid.endswith("-legacy"):
        return pid, None
    for parent in sorted(PARENT_CLOUDS, key=len, reverse=True):
        if pid == parent:
            return parent, None
        prefix = f"{parent}-"
        if pid.startswith(prefix):
            rest = pid[len(prefix) :]
            if rest:
                return parent, rest
    return pid, None


def adapter_id_for(provider_id: str) -> str:
    """Adapter registry key used for credentials, tools, and provision."""
    parent, _service = split_plugin_id(provider_id)
    return parent


def expand_service_plugins(base_catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep parent cloud entries and append one plugin per concrete service."""
    parents: list[dict[str, Any]] = []
    extra: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for entry in base_catalog:
        row = dict(entry)
        row.setdefault("parent_cloud", None)
        row.setdefault("source", "builtin")
        row.setdefault("icon", _PARENT_ICONS.get(str(row.get("id")), "cloud"))
        parents.append(row)
        by_id[str(row.get("id"))] = row

    for parent_id, groups in SERVICE_CATALOG.items():
        parent = by_id.get(parent_id)
        if parent is None:
            continue
        for group in groups:
            targets = _RUNTIME_TARGETS.get(group.runtime, list(parent.get("runtime_targets") or ["vm"]))
            for svc in group.services:
                extra.append(
                    {
                        **parent,
                        "id": f"{parent_id}-{svc.id}",
                        "label": svc.label,
                        "description": svc.description,
                        "icon": _ICONS.get(svc.id, _PARENT_ICONS.get(parent_id, "cloud")),
                        "parent_cloud": parent_id,
                        "service": svc.id,
                        "source": "builtin-plugin",
                        "runtime_targets": targets,
                        "services": [
                            {
                                "runtime": group.runtime,
                                "label": group.label,
                                "services": [svc.model_dump()],
                            }
                        ],
                    }
                )
    return parents + extra


def catalog_overlay_for(provider_id: str, parent_entry: dict[str, Any]) -> dict[str, Any]:
    """UI catalog row for a service plugin id, based on the parent adapter entry."""
    parent, service = split_plugin_id(provider_id)
    if not service:
        return {**parent_entry, "services": parent_entry.get("services") or []}
    expanded = expand_service_plugins([parent_entry])
    match = next((item for item in expanded if item["id"] == provider_id), None)
    if match is not None:
        return match
    return {
        **parent_entry,
        "id": provider_id,
        "parent_cloud": parent,
        "service": service,
        "source": "builtin-plugin",
    }


def merge_catalog(base: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last write wins by id so a registered plugin can replace a builtin service id."""
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in list(base) + list(extra):
        pid = str(row.get("id") or "")
        if not pid:
            continue
        if pid not in by_id:
            order.append(pid)
        by_id[pid] = row
    return [by_id[pid] for pid in order]


__all__ = [
    "PARENT_CLOUDS",
    "adapter_id_for",
    "catalog_overlay_for",
    "expand_service_plugins",
    "merge_catalog",
    "split_plugin_id",
]

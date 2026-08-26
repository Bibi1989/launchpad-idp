"""JSON Schema drafts for plugin credentials + deploy config, keyed by cloud service.

Used by PluginAiService (Gemini fallback) and by POST /plugins/generate-schemas.
Typed parent clouds (gcp/aws/azure/cloudflare) keep credentials optional so Settings
keys apply unless the user overrides them on the plugin.
"""

from __future__ import annotations

from typing import Any

from app.providers.service_plugins import split_plugin_id

TYPED_PARENTS = frozenset({"gcp", "aws", "azure", "cloudflare"})
_DRAFT = "http://json-schema.org/draft-07/schema#"

_K8S_SERVICES = frozenset({"gke", "eks", "aks", "doks", "lke", "k3s"})
_CONTAINER_SERVICES = frozenset({
    "cloud-run",
    "ecs-fargate",
    "container-apps",
    "aci",
    "app-platform",
    "app_runner",
})
_VM_SERVICES = frozenset({
    "gce",
    "gce-docker",
    "ec2",
    "ec2-docker",
    "azure-vm",
    "vm-docker",
    "droplet",
    "droplet-docker",
    "cloud-server",
    "server-docker",
    "linode-instance",
    "linode-docker",
})
_PAAS_SERVICES = frozenset({
    "workers",
    "pages",
    "railway-service",
    "render-web",
    "render-worker",
    "tunnels",
    "tunnel",
})


def _prop(title: str, *, type_: str = "string", description: str = "", **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": type_, "title": title}
    if description:
        out["description"] = description
    out.update(extra)
    return out


def _object_schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    description: str = "",
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$schema": _DRAFT,
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        schema["required"] = required
    if description:
        schema["description"] = description
    return schema


def schemas_for_cloud_service(
    *,
    parent_cloud: str = "",
    service_type: str = "",
    plugin_id: str = "",
    label: str = "",
    category: str = "",
    prompt: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (credentialsSchema, deploymentConfigSchema) for the selected service."""
    parent, service = _resolve_service(
        parent_cloud=parent_cloud,
        service_type=service_type,
        plugin_id=plugin_id,
        label=label,
        prompt=prompt,
    )
    creds = _credentials_schema(parent, category=category)
    deploy = _deployment_schema(parent, service, service_type=service_type)
    return creds, deploy


def _resolve_service(
    *,
    parent_cloud: str,
    service_type: str,
    plugin_id: str,
    label: str,
    prompt: str,
) -> tuple[str, str]:
    pid = (plugin_id or "").strip().lower()
    parent = (parent_cloud or "").strip().lower()
    service = ""
    if pid:
        inferred_parent, inferred_service = split_plugin_id(pid)
        if not parent:
            parent = inferred_parent
        if inferred_service:
            service = inferred_service
    blob = f"{label} {prompt} {pid} {service_type}".lower()
    if not parent:
        parent = _guess_parent(blob)
    if not service:
        service = _guess_service(parent, service_type, blob)
    return parent, service


def _guess_parent(text: str) -> str:
    checks = (
        ("digitalocean", "digitalocean"),
        ("droplet", "digitalocean"),
        ("hetzner", "hetzner"),
        ("linode", "linode"),
        ("akamai", "linode"),
        ("railway", "railway"),
        ("render", "render"),
        ("cloudflare", "cloudflare"),
        ("workers", "cloudflare"),
        ("gke", "gcp"),
        ("gcp", "gcp"),
        ("google", "gcp"),
        ("eks", "aws"),
        ("aws", "aws"),
        ("amazon", "aws"),
        ("aks", "azure"),
        ("azure", "azure"),
    )
    for token, parent in checks:
        if token in text:
            return parent
    return ""


def _guess_service(parent: str, service_type: str, text: str) -> str:
    for sid in (
        "gke",
        "cloud-run",
        "gce-docker",
        "eks",
        "ecs-fargate",
        "ec2-docker",
        "aks",
        "container-apps",
        "workers",
        "pages",
        "droplet",
        "doks",
    ):
        if sid.replace("-", " ") in text or sid in text:
            return sid
    if "kubernetes" in text or service_type == "kubernetes":
        return {
            "gcp": "gke",
            "aws": "eks",
            "azure": "aks",
            "digitalocean": "doks",
            "linode": "lke",
            "hetzner": "k3s",
        }.get(parent, "gke")
    if service_type == "container":
        return {
            "gcp": "cloud-run",
            "aws": "ecs-fargate",
            "azure": "container-apps",
            "digitalocean": "app-platform",
        }.get(parent, "cloud-run")
    if service_type == "paas":
        return {
            "cloudflare": "workers",
            "railway": "railway-service",
            "render": "render-web",
        }.get(parent, "workers")
    if service_type == "vm":
        return {
            "gcp": "gce",
            "aws": "ec2",
            "azure": "azure-vm",
            "digitalocean": "droplet",
            "hetzner": "cloud-server",
            "linode": "linode-instance",
        }.get(parent, "gce")
    return service_type or parent


def _credentials_schema(parent: str, *, category: str) -> dict[str, Any]:
    if category == "config":
        return _object_schema(
            {
                "ssh_user": _prop("SSH user", description="Login user for Ansible/Puppet/Chef.", default="ubuntu"),
                "ssh_private_key": _prop(
                    "SSH private key",
                    description="Optional override of the workspace SSH key.",
                    writeOnly=True,
                ),
                "inventory": _prop("Inventory", description="Host group or inventory path."),
            },
            description="Optional connection overrides for a config plugin.",
        )
    if parent in TYPED_PARENTS:
        return _typed_override_credentials(parent)
    if parent == "digitalocean":
        return _object_schema(
            {
                "token": _prop(
                    "API token",
                    description="DigitalOcean personal access token with write scope.",
                    writeOnly=True,
                ),
            },
            required=["token"],
        )
    if parent == "hetzner":
        return _object_schema(
            {
                "api_token": _prop("API token", description="Hetzner Cloud API token.", writeOnly=True),
            },
            required=["api_token"],
        )
    if parent == "linode":
        return _object_schema(
            {
                "token": _prop("API token", description="Linode / Akamai personal access token.", writeOnly=True),
            },
            required=["token"],
        )
    if parent == "railway":
        return _object_schema(
            {
                "api_token": _prop("API token", description="Railway account token.", writeOnly=True),
            },
            required=["api_token"],
        )
    if parent == "render":
        return _object_schema(
            {
                "api_key": _prop("API key", description="Render API key.", writeOnly=True),
            },
            required=["api_key"],
        )
    return _object_schema(
        {
            "api_token": _prop(
                "API token",
                description="Provider API token. Leave blank if this plugin inherits Settings keys.",
                writeOnly=True,
            ),
        },
        description="Plugin-specific credentials. Leave empty when a parent cloud is set.",
    )


def _typed_override_credentials(parent: str) -> dict[str, Any]:
    description = (
        f"Optional. Empty means use {parent.upper()} keys from Settings. "
        "Values here override Settings for this plugin only."
    )
    if parent == "gcp":
        return _object_schema(
            {
                "gcp_sa_key_json": _prop(
                    "Service account JSON",
                    description="Optional override of the Settings GCP service account key.",
                    writeOnly=True,
                ),
            },
            description=description,
        )
    if parent == "aws":
        return _object_schema(
            {
                "aws_access_key_id": _prop("Access key ID", description="Optional override of Settings AWS keys."),
                "aws_secret_access_key": _prop(
                    "Secret access key",
                    description="Optional override of Settings AWS keys.",
                    writeOnly=True,
                ),
            },
            description=description,
        )
    if parent == "azure":
        return _object_schema(
            {
                "azure_client_id": _prop("Client ID", description="Optional override of the Settings Azure SP."),
                "azure_client_secret": _prop(
                    "Client secret",
                    description="Optional override of the Settings Azure SP.",
                    writeOnly=True,
                ),
                "azure_tenant_id": _prop("Tenant ID"),
                "azure_subscription_id": _prop("Subscription ID"),
            },
            description=description,
        )
    return _object_schema(
        {
            "cloudflare_api_token": _prop(
                "API token",
                description="Optional override of the Settings Cloudflare token.",
                writeOnly=True,
            ),
        },
        description=description,
    )


def _deployment_schema(parent: str, service: str, *, service_type: str) -> dict[str, Any]:
    sid = (service or "").lower()
    if sid in _K8S_SERVICES or service_type == "kubernetes":
        return _k8s_deploy(parent, sid)
    if sid in _CONTAINER_SERVICES or sid == "cloud-run":
        return _container_deploy(parent, sid)
    if sid in _PAAS_SERVICES or service_type == "paas":
        return _paas_deploy(parent, sid)
    if sid in _VM_SERVICES or service_type == "vm":
        return _vm_deploy(parent, sid)
    return _k8s_deploy(parent, sid) if service_type == "kubernetes" else _vm_deploy(parent, sid)


def _k8s_deploy(parent: str, service: str) -> dict[str, Any]:
    region_title = "Location" if parent == "azure" else "Region"
    region_default = {
        "gcp": "us-central1",
        "aws": "us-east-1",
        "azure": "eastus",
        "digitalocean": "nyc1",
        "linode": "us-east",
        "hetzner": "fsn1",
    }.get(parent, "us-central1")
    machine = "vmSize" if parent == "azure" else "machineType"
    machine_default = {
        "gcp": "e2-standard-4",
        "aws": "t3.medium",
        "azure": "Standard_D2_v2",
    }.get(parent, "e2-standard-4")
    registry_key = {
        "gcp": "artifactRegistry",
        "aws": "ecr",
        "azure": "acr",
        "digitalocean": "containerRegistry",
        "linode": "containerRegistry",
    }.get(parent, "artifactRegistry")
    secret_enum = ["secret_manager", "native_k8s"]
    if parent == "aws":
        secret_enum = ["secrets_manager", "native_k8s"]
    elif parent == "azure":
        secret_enum = ["key_vault", "native_k8s"]
    return _object_schema(
        {
            "region": _prop(region_title, default=region_default),
            "clusterName": _prop("Cluster name", description=f"{service.upper() or 'Kubernetes'} cluster name."),
            "nodeCount": _prop("Node count", type_="integer", minimum=1, default=1),
            machine: _prop("Node size", default=machine_default),
            "imageSource": _prop(
                "Container image source",
                enum=["build_registry", "external"],
                default="build_registry",
                description="build_registry provisions a native registry; external uses GHCR/Docker Hub/etc.",
            ),
            registry_key: _prop(
                "Provision image registry",
                type_="boolean",
                default=True,
                description="Skipped automatically when imageSource is external.",
            ),
            "secretBackend": _prop(
                "Secret backend",
                enum=secret_enum,
                default=secret_enum[0],
                description="Cloud secret manager vs native Kubernetes Secrets / manifests.",
            ),
            "vpc": _prop("Create VPC / network", type_="boolean", default=True),
            "subnets": _prop("Create subnets", type_="boolean", default=True),
        },
        required=["region"],
        description=f"Deploy config for {service or 'managed Kubernetes'} on {parent or 'this cloud'}.",
    )


def _container_deploy(parent: str, service: str) -> dict[str, Any]:
    region_default = "us-central1" if parent == "gcp" else "us-east-1"
    return _object_schema(
        {
            "region": _prop("Region", default=region_default),
            "serviceName": _prop("Service name"),
            "image": _prop(
                "Container image",
                description="Set when using an external hub. Leave blank to push to the native registry.",
            ),
            "imageSource": _prop(
                "Container image source",
                enum=["build_registry", "external"],
                default="build_registry",
            ),
            "cpu": _prop("CPU", default="1"),
            "memory": _prop("Memory", default="512Mi"),
            "port": _prop("App port", type_="integer", default=8080, minimum=1, maximum=65535),
            "allowUnauthenticated": _prop("Allow unauthenticated", type_="boolean", default=True),
        },
        required=["region"],
        description=f"Deploy config for {service or 'serverless containers'} on {parent or 'this cloud'}.",
    )


def _vm_deploy(parent: str, service: str) -> dict[str, Any]:
    region_default = {
        "gcp": "us-central1",
        "aws": "us-east-1",
        "azure": "eastus",
        "digitalocean": "nyc3",
        "hetzner": "fsn1",
        "linode": "us-east",
    }.get(parent, "us-central1")
    size_key = "size" if parent in {"digitalocean", "hetzner", "linode"} else "machineType"
    size_default = {
        "gcp": "e2-standard-4",
        "aws": "t3.medium",
        "azure": "Standard_D2_v2",
        "digitalocean": "s-1vcpu-2gb",
        "hetzner": "cx22",
        "linode": "g6-standard-2",
    }.get(parent, "e2-standard-4")
    return _object_schema(
        {
            "region": _prop("Region / location", default=region_default),
            "zone": _prop("Zone", description="Required for GCE. Ignored on regional clouds."),
            size_key: _prop("Machine size", default=size_default),
            "image": _prop("OS image", default="ubuntu-24.04"),
            "sshUser": _prop("SSH user", default="ubuntu"),
            "appPort": _prop("App port", type_="integer", default=8080),
            "installDocker": _prop(
                "Install Docker via Launchpad script",
                type_="boolean",
                default=True,
            ),
        },
        required=["region"],
        description=f"Deploy config for {service or 'VM'} on {parent or 'this cloud'}.",
    )


def _paas_deploy(parent: str, service: str) -> dict[str, Any]:
    if parent == "cloudflare":
        return _object_schema(
            {
                "accountId": _prop("Account ID"),
                "name": _prop("Worker / Pages project name"),
                "compatibilityDate": _prop("Compatibility date", default="2024-01-01"),
            },
            description=f"Deploy config for Cloudflare {service or 'Workers'}.",
        )
    return _object_schema(
        {
            "region": _prop("Region"),
            "name": _prop("Service name"),
            "image": _prop("Container image", description="Optional when deploying from git."),
        },
        description=f"Deploy config for {service or 'PaaS'} on {parent or 'this cloud'}.",
    )

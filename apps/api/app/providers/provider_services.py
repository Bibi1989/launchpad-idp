"""Per-cloud service taxonomy, grouped by how the application runs.

Additive UI metadata: for each cloud, which concrete services let the app run under
Kubernetes, Docker / Docker Compose, a plain VM, or a managed PaaS. The Cloud Plugins
page renders these groups so a user can see, per cloud, what they can target.

Pure static data - it describes options, it does not provision anything.
"""

from __future__ import annotations

from pydantic import BaseModel


class CloudService(BaseModel):
    id: str
    label: str
    description: str


class CloudServiceGroup(BaseModel):
    # Runtime the services in this group provide: kubernetes | docker | vm | paas.
    runtime: str
    label: str
    services: list[CloudService]


def _g(runtime: str, label: str, services: list[tuple[str, str, str]]) -> CloudServiceGroup:
    return CloudServiceGroup(
        runtime=runtime,
        label=label,
        services=[CloudService(id=s[0], label=s[1], description=s[2]) for s in services],
    )


_K8S = "Kubernetes"
_DOCKER = "Docker / Docker Compose"
_VM = "Virtual Machine"
_PAAS = "Managed Platform"


_SERVICE_CATALOG: dict[str, list[CloudServiceGroup]] = {
    "aws": [
        _g("kubernetes", _K8S, [("eks", "Amazon EKS", "Managed Kubernetes control plane.")]),
        _g("docker", _DOCKER, [
            ("ecs-fargate", "ECS on Fargate", "Serverless containers, no VM to manage."),
            ("ec2-docker", "EC2 + Docker", "Docker Engine on an EC2 instance (cloud-init)."),
        ]),
        _g("vm", _VM, [("ec2", "Amazon EC2", "Raw Linux VM bootstrapped via cloud-init.")]),
    ],
    "gcp": [
        _g("kubernetes", _K8S, [("gke", "Google GKE", "Managed Kubernetes on Google Cloud.")]),
        _g("docker", _DOCKER, [
            ("cloud-run", "Cloud Run", "Fully managed container runtime."),
            ("gce-docker", "GCE + Docker", "Docker Engine on a Compute Engine VM (cloud-init)."),
        ]),
        _g("vm", _VM, [("gce", "Compute Engine", "Raw Linux VM bootstrapped via cloud-init.")]),
    ],
    "azure": [
        _g("kubernetes", _K8S, [("aks", "Azure AKS", "Managed Kubernetes on Azure.")]),
        _g("docker", _DOCKER, [
            ("container-apps", "Container Apps", "Serverless containers on Azure."),
            ("aci", "Container Instances", "Single-container serverless runtime."),
            ("vm-docker", "Azure VM + Docker", "Docker Engine on an Azure VM (cloud-init)."),
        ]),
        _g("vm", _VM, [("azure-vm", "Azure VM", "Raw Linux VM bootstrapped via cloud-init.")]),
    ],
    "hetzner": [
        _g("kubernetes", _K8S, [("k3s", "k3s on Cloud Server", "Lightweight Kubernetes on a Hetzner VM.")]),
        _g("docker", _DOCKER, [
            ("server-docker", "Cloud Server + Docker", "Docker Engine on a Hetzner VM (cloud-init)."),
        ]),
        _g("vm", _VM, [("cloud-server", "Cloud Server", "Raw Linux VM bootstrapped via cloud-init.")]),
    ],
    "digitalocean": [
        _g("kubernetes", _K8S, [("doks", "DigitalOcean Kubernetes", "Managed Kubernetes (DOKS).")]),
        _g("docker", _DOCKER, [
            ("app-platform", "App Platform", "Managed container/app platform."),
            ("droplet-docker", "Droplet + Docker", "Docker Engine on a Droplet (cloud-init)."),
        ]),
        _g("vm", _VM, [("droplet", "Droplet", "Raw Linux VM bootstrapped via cloud-init.")]),
    ],
    "linode": [
        _g("kubernetes", _K8S, [("lke", "Linode Kubernetes Engine", "Managed Kubernetes (LKE).")]),
        _g("docker", _DOCKER, [
            ("linode-docker", "Linode + Docker", "Docker Engine on a Linode instance (cloud-init)."),
        ]),
        _g("vm", _VM, [("linode-instance", "Linode Instance", "Raw Linux VM bootstrapped via cloud-init.")]),
    ],
    "railway": [
        _g("paas", _PAAS, [("railway-service", "Railway Service", "Deploy a container or repo as a service.")]),
    ],
    "render": [
        _g("paas", _PAAS, [
            ("render-web", "Render Web Service", "Deploy a container image or git repo."),
            ("render-worker", "Render Background Worker", "Long-running worker service."),
        ]),
    ],
    "cloudflare": [
        _g("paas", _PAAS, [
            ("workers", "Cloudflare Workers", "Edge JavaScript runtime."),
            ("pages", "Cloudflare Pages", "Static / SSR site hosting."),
        ]),
    ],
}

# Legacy bridge ids share the base cloud's services.
_LEGACY_ALIAS = {"aws-legacy": "aws", "gcp-legacy": "gcp", "azure-legacy": "azure"}


def services_for(provider_id: str) -> list[dict]:
    """Return the service groups for a cloud, or [] if none are catalogued."""
    key = _LEGACY_ALIAS.get(provider_id, provider_id)
    return [group.model_dump() for group in _SERVICE_CATALOG.get(key, [])]


SERVICE_CATALOG = _SERVICE_CATALOG

__all__ = ["CloudService", "CloudServiceGroup", "SERVICE_CATALOG", "services_for"]

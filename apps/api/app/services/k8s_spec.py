"""Canonical Kubernetes governance and preview workload definitions.

Shared by ``k8s_bundle`` (YAML generation for workspaces) and ``kubernetes`` (API apply
for ephemeral preview environments).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings

APP_NAME = "app"
QUOTA_NAME = "launchpad-default-quota"
LIMIT_RANGE_NAME = "launchpad-defaults"
PREVIEW_NETWORK_POLICY_NAME = "launchpad-zero-trust"
WORKSPACE_NETWORK_POLICY_SUFFIX = "-zero-trust"

LIMIT_RANGE_CONTAINER_SPEC: dict[str, dict[str, str]] = {
    "default": {"cpu": "250m", "memory": "256Mi"},
    "defaultRequest": {"cpu": "100m", "memory": "128Mi"},
    "max": {"cpu": "1", "memory": "1Gi"},
    "min": {"cpu": "50m", "memory": "64Mi"},
}

CONTAINER_RESOURCES: dict[str, dict[str, str]] = {
    "requests": {"cpu": "100m", "memory": "256Mi"},
    "limits": {"cpu": "500m", "memory": "768Mi"},
}


def sanitize_label(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in value)
    return cleaned[:63].strip("-_.") or "branch"


def build_preview_labels(
    *,
    environment_id: str,
    name: str,
    git_branch: str,
    git_repo_url: str,
    owner_label: str,
    ttl_expires_at: str | None = None,
) -> dict[str, str]:
    labels = {
        "EnvironmentId": environment_id,
        "Owner": sanitize_label(owner_label),
        "CreatedBy": "launchpad-control-plane",
        "launchpad.io/environment-id": environment_id,
        "launchpad.io/environment-name": name,
        "launchpad.io/git-branch": sanitize_label(git_branch),
        "launchpad.io/git-repo": sanitize_label(git_repo_url),
        "launchpad.io/managed-by": "launchpad-idp",
        "app": APP_NAME,
    }
    if ttl_expires_at:
        labels["TTL_Expiration"] = sanitize_label(ttl_expires_at)
    return labels


def preview_workload_selector() -> dict[str, str]:
    return {"app": APP_NAME, "launchpad.io/managed-by": "launchpad-idp"}


def governance_quota_hard(settings: Settings) -> dict[str, str]:
    return {
        "requests.cpu": settings.kubernetes_cpu_request,
        "requests.memory": settings.kubernetes_memory_request,
        "limits.cpu": settings.kubernetes_cpu_limit,
        "limits.memory": settings.kubernetes_memory_limit,
        "pods": settings.kubernetes_pod_limit,
    }


def workspace_governance_quota_hard(settings: Settings) -> dict[str, str]:
    """Workspace scaffold quotas - aligned with preview settings where possible."""
    return {
        "requests.cpu": settings.kubernetes_cpu_request,
        "requests.memory": settings.kubernetes_memory_request,
        "limits.cpu": settings.kubernetes_cpu_limit,
        "limits.memory": settings.kubernetes_memory_limit,
        "pods": settings.kubernetes_pod_limit,
        "services": "10",
        "count/deployments.apps": "10",
    }


def render_limit_range_yaml(*, namespace: str, environment_name: str) -> str:
    spec = LIMIT_RANGE_CONTAINER_SPEC
    return f"""\
apiVersion: v1
kind: LimitRange
metadata:
  name: {LIMIT_RANGE_NAME}
  namespace: {namespace}
  labels:
    launchpad.io/environment-name: {environment_name}
    launchpad.io/managed-by: launchpad-idp
spec:
  limits:
    - type: Container
      default:
        cpu: {spec["default"]["cpu"]}
        memory: {spec["default"]["memory"]}
      defaultRequest:
        cpu: {spec["defaultRequest"]["cpu"]}
        memory: {spec["defaultRequest"]["memory"]}
      max:
        cpu: {spec["max"]["cpu"]}
        memory: {spec["max"]["memory"]}
      min:
        cpu: {spec["min"]["cpu"]}
        memory: {spec["min"]["memory"]}
"""


def render_resource_quota_yaml(
    *,
    namespace: str,
    environment_name: str,
    settings: Settings,
) -> str:
    hard = workspace_governance_quota_hard(settings)
    lines = "\n".join(f'    {key}: "{value}"' for key, value in hard.items())
    return f"""\
apiVersion: v1
kind: ResourceQuota
metadata:
  name: {QUOTA_NAME}
  namespace: {namespace}
  labels:
    launchpad.io/environment-name: {environment_name}
    launchpad.io/managed-by: launchpad-idp
spec:
  hard:
{lines}
"""


def render_workspace_network_policy_yaml(
    *,
    namespace: str,
    environment_name: str,
    app: str = APP_NAME,
    common_labels: str,
) -> str:
    return f"""\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {app}{WORKSPACE_NETWORK_POLICY_SUFFIX}
  namespace: {namespace}
  labels:
{common_labels.rstrip()}
spec:
  podSelector:
    matchLabels:
      app: {app}
      launchpad.io/managed-by: launchpad-idp
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress-nginx
        - podSelector:
            matchLabels:
              app: {app}
              launchpad.io/managed-by: launchpad-idp
      ports:
        - protocol: TCP
          port: 80
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # Same-namespace pods (any port) so the app + its wait-for-db init containers
    # can reach in-cluster datastores (postgres/redis/mysql/mongodb) and siblings.
    - to:
        - podSelector: {{}}
    - to:
        - namespaceSelector:
            matchLabels:
              launchpad.io/environment-name: {environment_name}
      ports:
        - protocol: TCP
          port: 443
        - protocol: TCP
          port: 80
"""


def build_preview_resource_quota(settings: Settings, *, namespace: str, labels: dict[str, str]):
    from kubernetes import client

    return client.V1ResourceQuota(
        metadata=client.V1ObjectMeta(name=QUOTA_NAME, namespace=namespace, labels=labels),
        spec=client.V1ResourceQuotaSpec(hard=governance_quota_hard(settings)),
    )


def build_preview_limit_range(*, namespace: str, labels: dict[str, str]):
    from kubernetes import client

    spec = LIMIT_RANGE_CONTAINER_SPEC
    return client.V1LimitRange(
        metadata=client.V1ObjectMeta(
            name=LIMIT_RANGE_NAME,
            namespace=namespace,
            labels=labels,
        ),
        spec=client.V1LimitRangeSpec(
            limits=[
                client.V1LimitRangeItem(
                    type="Container",
                    default=spec["default"],
                    default_request=spec["defaultRequest"],
                    max=spec["max"],
                    min=spec["min"],
                )
            ]
        ),
    )


def build_preview_network_policy(
    *,
    namespace: str,
    labels: dict[str, str],
    listen_ports: list[int] | None = None,
):
    from kubernetes import client

    selector = preview_workload_selector()
    ports = sorted({80, *(listen_ports or [])})
    return client.V1NetworkPolicy(
        metadata=client.V1ObjectMeta(
            name=PREVIEW_NETWORK_POLICY_NAME,
            namespace=namespace,
            labels=labels,
        ),
        spec=client.V1NetworkPolicySpec(
            pod_selector=client.V1LabelSelector(match_labels=selector),
            policy_types=["Ingress", "Egress"],
            ingress=[
                client.V1NetworkPolicyIngressRule(
                    ports=[
                        client.V1NetworkPolicyPort(protocol="TCP", port=port)
                        for port in ports
                    ],
                )
            ],
            egress=[
                # DNS resolution.
                client.V1NetworkPolicyEgressRule(
                    to=[
                        client.V1NetworkPolicyPeer(
                            namespace_selector=client.V1LabelSelector(
                                match_labels={
                                    "kubernetes.io/metadata.name": "kube-system",
                                }
                            )
                        )
                    ],
                    ports=[
                        client.V1NetworkPolicyPort(protocol="UDP", port=53),
                        client.V1NetworkPolicyPort(protocol="TCP", port=53),
                    ],
                ),
                # Same-namespace egress so the workload (and its wait-for-db init
                # containers) can reach in-cluster datastores (postgres/redis/…)
                # and sibling services. An empty podSelector with no
                # namespaceSelector matches all pods in this namespace only, so
                # the zero-trust boundary at the namespace edge is preserved.
                client.V1NetworkPolicyEgressRule(
                    to=[client.V1NetworkPolicyPeer(pod_selector=client.V1LabelSelector())],
                ),
            ],
        ),
    )

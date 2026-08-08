"""Resolve a smart preview DeployPlan from workspace wizard selections."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.cloud import (
    KubernetesPackaging,
    RunningInstanceKind,
    WorkspaceRuntimeMode,
    WorkspaceWizardConfig,
    WorkloadDependenciesConfig,
)
from app.schemas.k8s import DeployMode
from app.services.runtime_mode import has_serverless_runtime


@dataclass(frozen=True, slots=True)
class PreviewDeployPlan:
    """Concrete preview shape derived from provision-time selections."""

    deploy_mode: DeployMode
    runtime_mode: WorkspaceRuntimeMode
    enable_postgres: bool
    enable_redis: bool
    skip_local_cluster: bool
    reason: str
    manifest_packaging: str | None = None
    attach_kind: str | None = None
    attach_kube_context: str | None = None
    attach_endpoint_url: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "deploy_mode": self.deploy_mode.value,
            "runtime_mode": self.runtime_mode.value,
            "enable_postgres": self.enable_postgres,
            "enable_redis": self.enable_redis,
            "skip_local_cluster": self.skip_local_cluster,
            "reason": self.reason,
            "manifest_packaging": self.manifest_packaging,
            "attach_kind": self.attach_kind,
            "attach_kube_context": self.attach_kube_context,
            "attach_endpoint_url": self.attach_endpoint_url,
        }


def _deps_want_postgres(deps: WorkloadDependenciesConfig) -> bool:
    return bool(deps.postgres.enabled or deps.mysql.enabled or deps.mongodb.enabled)


def _deps_want_redis(deps: WorkloadDependenciesConfig) -> bool:
    return bool(deps.redis.enabled)


def resolve_preview_deploy_plan(
    config: WorkspaceWizardConfig,
    *,
    requested_deploy_mode: DeployMode | None = None,
) -> PreviewDeployPlan:
    """Map provider + runtime_mode + services/deps → preview deploy plan.

    Explicit client ``requested_deploy_mode`` is honored when compatible;
    otherwise the workspace runtime mode wins.
    """
    runtime = config.runtime_mode
    enable_postgres = _deps_want_postgres(config.dependencies)
    enable_redis = _deps_want_redis(config.dependencies)
    packaging = config.kubernetes_packaging
    packaging_value = packaging.value if packaging != KubernetesPackaging.NONE else None

    if runtime == WorkspaceRuntimeMode.DOCKER_COMPOSE:
        if requested_deploy_mode not in (None, DeployMode.COMPOSE):
            # Workspace is compose-primary; do not silently fall back to K8s.
            pass
        return PreviewDeployPlan(
            deploy_mode=DeployMode.COMPOSE,
            runtime_mode=runtime,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
            skip_local_cluster=True,
            reason="Workspace runtime_mode=docker_compose (local Compose preview)",
            manifest_packaging=None,
        )

    if runtime == WorkspaceRuntimeMode.RUNNING_INSTANCE:
        instance = config.running_instance
        kind = instance.kind
        if has_serverless_runtime(config.cloud):
            kind = RunningInstanceKind.SERVERLESS
        return PreviewDeployPlan(
            deploy_mode=DeployMode.ATTACH,
            runtime_mode=runtime,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
            skip_local_cluster=True,
            reason=f"Workspace runtime_mode=running_instance ({kind.value})",
            manifest_packaging=None,
            attach_kind=kind.value,
            attach_kube_context=instance.kube_context,
            attach_endpoint_url=instance.endpoint_url,
        )

    # kubernetes
    if requested_deploy_mode == DeployMode.MANIFEST:
        return PreviewDeployPlan(
            deploy_mode=DeployMode.MANIFEST,
            runtime_mode=runtime,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
            skip_local_cluster=False,
            reason="Client requested manifest deploy",
            manifest_packaging=packaging_value,
        )
    if requested_deploy_mode == DeployMode.PREVIEW:
        return PreviewDeployPlan(
            deploy_mode=DeployMode.PREVIEW,
            runtime_mode=runtime,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
            skip_local_cluster=False,
            reason="Client requested control-plane preview deploy",
            manifest_packaging=packaging_value,
        )

    if packaging in {KubernetesPackaging.RAW_MANIFESTS, KubernetesPackaging.HELM}:
        return PreviewDeployPlan(
            deploy_mode=DeployMode.MANIFEST,
            runtime_mode=runtime,
            enable_postgres=enable_postgres,
            enable_redis=enable_redis,
            skip_local_cluster=False,
            reason="Workspace has Kubernetes packaging; using manifest deploy",
            manifest_packaging=packaging_value,
        )

    return PreviewDeployPlan(
        deploy_mode=DeployMode.PREVIEW,
        runtime_mode=runtime,
        enable_postgres=enable_postgres,
        enable_redis=enable_redis,
        skip_local_cluster=False,
        reason="Default control-plane preview deploy",
        manifest_packaging=packaging_value,
    )

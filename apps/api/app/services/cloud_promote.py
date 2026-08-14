"""Map local attach/compose previews onto cloud VM or serverless deploy targets."""

from __future__ import annotations

import json
import re

from app.core.secrets import project_id_from_gcp_sa_json
from app.schemas.cloud import (
    AwsCloudConfig,
    AwsResources,
    AzureCloudConfig,
    AzureResources,
    CloudConfig,
    CloudCredentials,
    CloudProvider,
    CloudflareCloudConfig,
    CloudflareResources,
    ContainerScaffoldConfig,
    ContainerServiceSpec,
    GcpCloudConfig,
    GcpResources,
    IaCEngine,
    ImageSecurityScanConfig,
    InstanceCodeSource,
    InstanceProcessStrategy,
    KubernetesPackaging,
    ProvisioningWizardRequest,
    RunningInstanceConfig,
    RunningInstanceKind,
    WorkspaceArtifactsMode,
    WorkspaceRuntimeMode,
    WorkspaceWizardConfig,
)
from app.schemas.k8s import DeployMode
from app.services.runtime_mode import normalize_artifacts_for_runtime_mode
from app.services.service_kind import is_frontend_app_kind

_SAFE = re.compile(r"[^a-z0-9-]+")


_K8S_DEPLOY_MODES = frozenset({DeployMode.MANIFEST.value, DeployMode.PREVIEW.value})


def needs_cloud_retarget(
    *,
    source_provider: str | None,
    deploy_mode: str | None,
) -> bool:
    """True when promote must clone/re-target the workspace onto a cloud provider."""
    provider = (source_provider or "local").strip().lower()
    mode = (deploy_mode or DeployMode.PREVIEW.value).strip().lower()
    if provider == CloudProvider.LOCAL.value:
        return True
    return mode in {DeployMode.ATTACH.value, DeployMode.COMPOSE.value}


def promote_cloud_deploy_mode(
    source_deploy_mode: str | None,
    *,
    retarget: bool,
) -> DeployMode:
    """Keep Kubernetes deploys on manifests; only attach/compose become cloud VMs."""
    mode = (source_deploy_mode or DeployMode.PREVIEW.value).strip().lower()
    if mode in _K8S_DEPLOY_MODES:
        return DeployMode(mode)
    if retarget:
        return DeployMode.ATTACH
    try:
        return DeployMode(mode)
    except ValueError:
        return DeployMode.ATTACH


def promote_runtime_target(
    source: WorkspaceWizardConfig,
) -> RunningInstanceKind:
    """Instance mode promotes to cloud VM; compose promotes to serverless."""
    if source.runtime_mode == WorkspaceRuntimeMode.DOCKER_COMPOSE:
        return RunningInstanceKind.SERVERLESS
    return RunningInstanceKind.VM


_PRIMARY_PREFERRED_TOKENS = frozenset(
    {"web", "ui", "frontend", "website", "site", "marketing", "landing", "spa", "next", "nuxt"}
)
_PRIMARY_DEPRIORITIZED_TOKENS = frozenset(
    {
        "dashboard",
        "status",
        "health",
        "admin",
        "infra",
        "backend",
        "api",
        "server",
        "worker",
        "metrics",
    }
)


def _primary_service_rank(spec: ContainerServiceSpec) -> tuple[int, int, int, str]:
    from app.services.service_kind import service_name_tokens

    name = (spec.name or "").strip().lower()
    tokens = service_name_tokens(name)
    is_fe = 0 if is_frontend_app_kind(spec.app_kind or "", name=spec.name) else 1
    preferred = 0 if tokens & _PRIMARY_PREFERRED_TOKENS else 1
    deprioritized = 1 if tokens & _PRIMARY_DEPRIORITIZED_TOKENS else 0
    return (is_fe, deprioritized, preferred, name)


def recommend_primary_service(
    services: list[ContainerServiceSpec],
) -> str | None:
    if not services:
        return None
    # Prefer website/web over status-dashboard frontends when both exist.
    return sorted(services, key=_primary_service_rank)[0].name


def apply_primary_service_selection(
    services: list[ContainerServiceSpec],
    primary_service: str | None,
) -> list[ContainerServiceSpec]:
    if not services:
        return services
    primary = (primary_service or recommend_primary_service(services) or services[0].name).strip()
    updated: list[ContainerServiceSpec] = []
    for spec in services:
        # Exactly one preview target. Prior True on a sibling must not stick.
        expose = spec.name == primary
        updated.append(spec.model_copy(update={"expose_preview": expose}))
    return updated


def default_region(provider: CloudProvider) -> str:
    if provider == CloudProvider.GCP:
        return "us-central1"
    if provider == CloudProvider.AWS:
        return "us-east-1"
    if provider == CloudProvider.AZURE:
        return "eastus"
    return "auto"


def cloud_config_for_promote(
    provider: CloudProvider,
    credentials: CloudCredentials,
    *,
    target_kind: RunningInstanceKind,
    region: str | None = None,
    create_vpc: bool = False,
    create_subnets: bool = False,
    existing_vpc_id: str | None = None,
    existing_security_group_id: str | None = None,
    kubernetes: bool = False,
) -> CloudConfig:
    """Cloud workspace stub for ephemeral previews (K8s cluster, VM, or serverless)."""
    resolved_region = (region or "").strip() or default_region(provider)
    existing = (existing_vpc_id or "").strip() or None
    existing_sg = (existing_security_group_id or "").strip() or None
    want_vpc = bool(create_vpc or create_subnets) and not existing
    want_subnets = bool(create_subnets) if want_vpc else False
    if create_subnets and not existing:
        want_vpc = True
        want_subnets = True
    if provider == CloudProvider.GCP:
        project_id = project_id_from_gcp_sa_json(credentials.gcp_sa_key_json) or "launchpad-preview"
        serverless = (not kubernetes) and target_kind == RunningInstanceKind.SERVERLESS
        return GcpCloudConfig(
            provider=CloudProvider.GCP,
            resources=GcpResources(
                project_id=project_id,
                vpc=want_vpc,
                subnets=want_subnets,
                existing_vpc_id=existing,
                gke=kubernetes,
                cloud_run=serverless,
                artifact_registry=True,
                region=resolved_region,
            ),
        )
    if provider == CloudProvider.AWS:
        serverless = (not kubernetes) and target_kind == RunningInstanceKind.SERVERLESS
        return AwsCloudConfig(
            provider=CloudProvider.AWS,
            resources=AwsResources(
                vpc=want_vpc,
                subnets=want_subnets,
                existing_vpc_id=existing,
                existing_security_group_id=existing_sg,
                eks=kubernetes,
                ec2=(not kubernetes) and not serverless,
                app_runner=serverless,
                ecr=True,
                region=resolved_region,
            ),
        )
    if provider == CloudProvider.AZURE:
        serverless = (not kubernetes) and target_kind == RunningInstanceKind.SERVERLESS
        rg = "launchpad-preview"
        sub = (credentials.azure_subscription_id or "").strip()
        if sub and len(sub) >= 8:
            rg = f"lp-{sub[-8:].lower()}"
        return AzureCloudConfig(
            provider=CloudProvider.AZURE,
            resources=AzureResources(
                resource_group=rg,
                vnet=want_vpc,
                subnets=want_subnets,
                aks=kubernetes,
                container_apps=serverless,
                acr=True,
                location=resolved_region,
            ),
        )
    account_id = "00000000000000000000000000000000"
    token = (credentials.cloudflare_api_token or "").strip()
    if token:
        try:
            import base64

            parts = token.split(".")
            if len(parts) >= 2:
                payload = parts[1] + "=" * (-len(parts[1]) % 4)
                data = json.loads(base64.urlsafe_b64decode(payload))
                if isinstance(data, dict):
                    aid = data.get("account_id") or data.get("sub")
                    if isinstance(aid, str) and len(aid) >= 8:
                        account_id = aid
        except Exception:
            pass
    return CloudflareCloudConfig(
        provider=CloudProvider.CLOUDFLARE,
        resources=CloudflareResources(
            account_id=account_id,
            workers=True,
            pages=True,
        ),
    )


def _service_name_for_env(base_name: str, environment_name: str) -> str:
    raw = f"{base_name}-{environment_name}"[:48]
    cleaned = _SAFE.sub("-", raw.lower()).strip("-")
    return cleaned or "launchpad-app"


def build_cloud_running_instance(
    *,
    provider: CloudProvider,
    environment_name: str,
    primary_service: str | None,
    source: RunningInstanceConfig | None,
    target_kind: RunningInstanceKind,
    code_source: InstanceCodeSource | None = None,
    region: str | None = None,
) -> RunningInstanceConfig:
    base = source or RunningInstanceConfig()
    service = _service_name_for_env(
        primary_service or base.service_name or environment_name,
        environment_name,
    )
    resolved_region = (
        (region or "").strip()
        or (base.region or "").strip()
        or default_region(provider)
    )
    strategy = base.process_strategy or InstanceProcessStrategy.DOCKER
    if target_kind == RunningInstanceKind.SERVERLESS:
        strategy = InstanceProcessStrategy.DOCKER
    resolved_code = code_source or base.code_source or InstanceCodeSource.SSH
    if strategy == InstanceProcessStrategy.DOCKER:
        # Docker path pulls an image; code_source is unused.
        resolved_code = InstanceCodeSource.SSH
    return RunningInstanceConfig(
        kind=target_kind,
        service_name=service,
        region=resolved_region,
        listen_port=base.listen_port or 8080,
        process_strategy=strategy,
        code_source=resolved_code,
        reverse_proxy=base.reverse_proxy,
    )


def build_cloud_promote_wizard_request(
    source: WorkspaceWizardConfig,
    *,
    workspace_name: str,
    provider: CloudProvider,
    credentials: CloudCredentials,
    primary_service: str | None = None,
    code_source: InstanceCodeSource | None = None,
    region: str | None = None,
    create_vpc: bool = False,
    create_subnets: bool = False,
    existing_vpc_id: str | None = None,
    existing_security_group_id: str | None = None,
    image_scan: ImageSecurityScanConfig | None = None,
) -> ProvisioningWizardRequest:
    kubernetes = source.runtime_mode == WorkspaceRuntimeMode.KUBERNETES
    target_kind = promote_runtime_target(source)
    cloud = cloud_config_for_promote(
        provider,
        credentials,
        target_kind=target_kind,
        region=region,
        create_vpc=create_vpc,
        create_subnets=create_subnets,
        existing_vpc_id=existing_vpc_id,
        existing_security_group_id=existing_security_group_id,
        kubernetes=kubernetes,
    )
    services = list(source.container_scaffold.services or [])
    services = apply_primary_service_selection(services, primary_service)
    # Kubernetes promote must not rewrite imported apps into the Express
    # status-dashboard scaffold. Instance/VM promote still scaffolds Dockerfiles.
    scaffold = source.container_scaffold.model_copy(
        update={
            "generate_docker_compose": False,
            "services": services,
            **(
                {"enabled": False, "generate_dockerfile": False}
                if kubernetes
                else {"enabled": True, "generate_dockerfile": True}
            ),
        },
    )
    running_instance = (
        source.running_instance
        if kubernetes
        else build_cloud_running_instance(
            provider=provider,
            environment_name=workspace_name,
            primary_service=primary_service or recommend_primary_service(services),
            source=source.running_instance,
            target_kind=target_kind,
            code_source=code_source,
            region=region,
        )
    )
    runtime_mode = (
        WorkspaceRuntimeMode.KUBERNETES
        if kubernetes
        else WorkspaceRuntimeMode.RUNNING_INSTANCE
    )
    source_packaging = source.kubernetes_packaging
    if kubernetes and source_packaging == KubernetesPackaging.NONE:
        source_packaging = KubernetesPackaging.RAW_MANIFESTS
    k8s_options = source.kubernetes_options
    if image_scan is not None:
        k8s_options = k8s_options.model_copy(update={"image_scan": image_scan})
    artifact_mode, packaging, scaffold, running_instance = normalize_artifacts_for_runtime_mode(
        cloud=cloud,
        runtime_mode=runtime_mode,
        artifact_mode=(
            WorkspaceArtifactsMode.BOTH
            if kubernetes
            else WorkspaceArtifactsMode.IAC_ONLY
        ),
        kubernetes_packaging=(
            source_packaging if kubernetes else KubernetesPackaging.NONE
        ),
        container_scaffold=scaffold,
        running_instance=running_instance,
    )
    return ProvisioningWizardRequest(
        name=workspace_name,
        iac_engine=source.iac_engine or IaCEngine.TERRAFORM,
        cloud=cloud,
        credentials=credentials,
        run_init=False,
        runtime_mode=runtime_mode,
        running_instance=running_instance,
        artifact_mode=artifact_mode,
        kubernetes_packaging=packaging,
        kubernetes_options=k8s_options,
        cost_optimization=source.cost_optimization,
        container_scaffold=scaffold,
        dependencies=source.dependencies,
        ansible=source.ansible,
    )


def resolve_attach_running_instance(
    running_instance: RunningInstanceConfig,
    *,
    cloud_provider: str | None,
    environment_name: str,
    runtime_mode: WorkspaceRuntimeMode | None = None,
) -> RunningInstanceConfig:
    """When an env targets cloud, coerce local attach kinds to VM or serverless."""
    provider_raw = (cloud_provider or CloudProvider.LOCAL.value).strip().lower()
    if provider_raw == CloudProvider.LOCAL.value:
        return running_instance
    if runtime_mode == WorkspaceRuntimeMode.KUBERNETES:
        return running_instance
    if running_instance.kind in {RunningInstanceKind.SERVERLESS, RunningInstanceKind.VM}:
        if running_instance.service_name:
            return running_instance

    target_kind = RunningInstanceKind.SERVERLESS
    if runtime_mode != WorkspaceRuntimeMode.DOCKER_COMPOSE:
        target_kind = RunningInstanceKind.VM

    if running_instance.kind in {
        RunningInstanceKind.LOCAL_MACHINE,
        RunningInstanceKind.VM,
        RunningInstanceKind.SERVERLESS,
    }:
        return build_cloud_running_instance(
            provider=CloudProvider(provider_raw),
            environment_name=environment_name,
            primary_service=running_instance.service_name,
            source=running_instance,
            target_kind=target_kind,
        )
    return running_instance

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.events import EnvEventType, publish_env_event
from app.core.logging import configure_logging, get_logger, sanitize_log_message
from app.models.domain import (
    AuditAction,
    AuditStatus,
    Environment,
    EnvironmentStatus,
    ExecutionStage,
    LogLevel,
    ProvisioningWorkspace,
    User,
)
from app.repositories.environment import DeploymentLogRepository, EnvironmentRepository
from app.repositories.user import UserRepository
from app.schemas.k8s import DeployMode
from app.services.audit import AuditService
from app.services.deploy_mode_routing import (
    NON_K8S_DEPLOY_MODES,
    init_workflow_message,
    normalize_deploy_mode,
)
from app.services.drift_scanner import DRIFT_SCANNER_ACTOR, record_drift_if_changed, scan_environment
from app.services.kubernetes import (
    KubernetesProvisioner,
    PreviewCancelled,
    ProvisionedResources,
)
from app.services.manifest_deploy import ManifestDeployer
from app.services.preview_build import (
    PreviewBuildError,
    build_preview_image,
    preview_build_eligible,
)
from app.services.state_lock import (
    PROVISIONING_IN_PROGRESS_MESSAGE,
    StateLockConflict,
    acquire_state_lock,
    force_release_state_lock,
    is_state_locked,
)
from app.workers.celery_app import celery_app

configure_logging()
logger = get_logger(__name__)


async def _attach_org_slug(session: AsyncSession, environment: Environment) -> str | None:
    org_id = getattr(environment, "org_id", None)
    if org_id is None:
        return None
    from app.models.domain import Organization

    org = await session.get(Organization, org_id)
    slug = (getattr(org, "slug", None) or "").strip().lower() if org is not None else ""
    return slug or None


async def _merge_attach_credentials_from_vault(
    session: AsyncSession,
    *,
    attach_credentials: object | None,
    owner_id: UUID | None,
    provider: str | None = None,
) -> object | None:
    """Overlay Settings vault onto workspace creds for the active cloud provider."""
    if owner_id is None:
        return attach_credentials
    from app.schemas.cloud import CloudCredentials
    from app.services.provisioning import ProvisioningService

    owner_row = await session.get(User, owner_id)
    if owner_row is None:
        return attach_credentials
    base = attach_credentials if isinstance(attach_credentials, CloudCredentials) else CloudCredentials()
    return await ProvisioningService(session).fill_cloud_credentials_from_account_vault(
        base,
        owner_row,
        provider=provider,
    )


def _wizard_cloud_provider(snapshot: dict | None) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    cloud = snapshot.get("cloud")
    if isinstance(cloud, dict):
        raw = cloud.get("provider")
        if raw is not None:
            return str(getattr(raw, "value", raw))
    return None


def _wizard_network_flags(snapshot: dict | None) -> tuple[bool, bool]:
    """Return (create_vpc, create_subnets) from wizard cloud resources."""
    if not isinstance(snapshot, dict):
        return False, False
    cloud = snapshot.get("cloud")
    if not isinstance(cloud, dict):
        return False, False
    resources = cloud.get("resources")
    if not isinstance(resources, dict):
        return False, False
    if str(resources.get("existing_vpc_id") or "").strip():
        return False, False
    create_vpc = bool(resources.get("vpc") or resources.get("vnet"))
    create_subnets = bool(resources.get("subnets"))
    if create_subnets:
        create_vpc = True
    return create_vpc, create_subnets


def _wizard_existing_vpc_id(snapshot: dict | None) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    cloud = snapshot.get("cloud")
    if not isinstance(cloud, dict):
        return None
    resources = cloud.get("resources")
    if not isinstance(resources, dict):
        return None
    raw = resources.get("existing_vpc_id")
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _wizard_existing_security_group_id(snapshot: dict | None) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    cloud = snapshot.get("cloud")
    if not isinstance(cloud, dict):
        return None
    resources = cloud.get("resources")
    if not isinstance(resources, dict):
        return None
    raw = resources.get("existing_security_group_id")
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _wizard_kubernetes_image_source(snapshot: dict | None) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    opts = snapshot.get("kubernetes_options")
    if isinstance(opts, dict):
        raw = opts.get("image_source")
        if raw is not None:
            value = str(raw).strip()
            if value:
                return value
    return None


def _resolve_k8s_image_source(
    environment: object,
    snapshot: dict | None,
) -> str:
    from app.schemas.cloud import KubernetesImageSource

    env_raw = getattr(environment, "kubernetes_image_source", None)
    if env_raw:
        return str(env_raw).strip().lower()
    wizard = _wizard_kubernetes_image_source(snapshot)
    if wizard:
        return wizard.lower()
    return KubernetesImageSource.BUILD_REGISTRY.value


def _wizard_image_scan_config(snapshot: dict | None) -> object | None:
    if not isinstance(snapshot, dict):
        return None
    opts = snapshot.get("kubernetes_options")
    if isinstance(opts, dict):
        raw = opts.get("image_scan")
        if raw is not None:
            return raw
    return None


def _resolve_image_scan_config(environment: object, snapshot: dict | None) -> object:
    from app.services.image_security_scan import parse_image_scan_config

    env_raw = getattr(environment, "kubernetes_image_scan_json", None)
    if env_raw:
        return parse_image_scan_config(env_raw)
    return parse_image_scan_config(_wizard_image_scan_config(snapshot))


def _compose_primary_host_preference(snapshot: dict | None) -> int | None:
    """Host publish port from wizard running_instance.listen_port (Compose previews)."""
    if not isinstance(snapshot, dict):
        return None
    try:
        from app.schemas.cloud import WorkspaceWizardConfig

        wiz = WorkspaceWizardConfig.model_validate({**snapshot, "has_credentials": False})
        port = int(wiz.running_instance.listen_port)
    except Exception:
        return None
    return port if 1 <= port <= 65535 else None


def _wizard_gcp_project_id(snapshot: dict | None) -> str | None:
    """GCP project id from wizard cloud.resources.project_id."""
    if not isinstance(snapshot, dict):
        return None
    cloud = snapshot.get("cloud")
    if not isinstance(cloud, dict):
        return None
    resources = cloud.get("resources")
    if not isinstance(resources, dict):
        return None
    raw = resources.get("project_id")
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


async def _retarget_provisioner_for_cloud_k8s(
    session: AsyncSession,
    *,
    environment: Environment,
    provisioner: KubernetesProvisioner,
    deploy_mode: str,
    create_cluster: bool,
    log_repo: DeploymentLogRepository | None = None,
    env_uuid: UUID | None = None,
    commit_sha: str | None = None,
) -> KubernetesProvisioner:
    """Point the provisioner at GKE/EKS/AKS when this env is a cloud Kubernetes deploy."""
    from app.services.cloud_kubernetes import (
        ensure_cloud_kubernetes_target,
        is_cloud_kubernetes_deploy,
        region_from_wizard,
    )
    from app.services.provisioning import ProvisioningService

    provider = str(getattr(environment, "provider", None) or "local").lower()
    if not is_cloud_kubernetes_deploy(provider=provider, deploy_mode=deploy_mode):
        return provisioner

    snapshot: dict | None = None
    workspace_id = getattr(environment, "workspace_id", None)
    if workspace_id is not None:
        workspace_row = await session.get(ProvisioningWorkspace, workspace_id)
        if workspace_row is not None:
            snapshot = ProvisioningService(session)._load_wizard_snapshot(workspace_row)

    credentials = await _merge_attach_credentials_from_vault(
        session,
        attach_credentials=None,
        owner_id=environment.owner_id,
        provider=provider,
    )
    region = region_from_wizard(provider, snapshot)
    if log_repo is not None and env_uuid is not None:
        await _emit_log(
            log_repo,
            environment_id=env_uuid,
            message=f"INIT - connecting to cloud Kubernetes ({provider.upper()} {region})",
            status=EnvironmentStatus.PROVISIONING.value,
            commit_sha=commit_sha,
            stage=ExecutionStage.INIT,
        )
        await session.commit()

    target = await asyncio.to_thread(
        ensure_cloud_kubernetes_target,
        provider=provider,
        credentials=credentials,
        region=region,
        environment_id=str(environment.id),
        existing_vpc_id=_wizard_existing_vpc_id(snapshot),
        create=create_cluster,
    )
    provisioner.retarget(
        kubeconfig_path=target.kubeconfig_path,
        kube_context=target.context,
        remote_cluster=True,
    )
    if log_repo is not None and env_uuid is not None:
        created_note = "created " if target.created else "using "
        await _emit_log(
            log_repo,
            environment_id=env_uuid,
            message=(
                f"VALIDATE - cloud Kubernetes ready "
                f"({created_note}{target.cluster_name}, context={target.context})"
            ),
            status=EnvironmentStatus.PROVISIONING.value,
            commit_sha=commit_sha,
            stage=ExecutionStage.VALIDATE,
        )
        await session.commit()
    return provisioner


async def _sync_workspace_after_cloud_deploy(
    session: AsyncSession,
    *,
    workspace_id: UUID | None,
    resources: object,
    cloud_provider: str | None,
) -> None:
    """Write ansible + cloud IaC into the linked workspace after cloud attach deploy."""
    if workspace_id is None:
        return
    provider = (cloud_provider or "").strip().lower()
    if provider in {"", "local"}:
        return
    raw = getattr(resources, "running_instance", None)
    if raw is None:
        return
    from app.schemas.cloud import RunningInstanceConfig

    if isinstance(raw, RunningInstanceConfig):
        running_instance = raw
    else:
        try:
            running_instance = RunningInstanceConfig.model_validate(raw)
        except Exception:
            return
    if not (running_instance.host or "").strip():
        return
    from app.services.provisioning import ProvisioningService

    await ProvisioningService(session).sync_workspace_after_cloud_deploy(
        workspace_id,
        running_instance=running_instance,
    )


async def _effective_deploy_mode(
    session: AsyncSession,
    environment: Environment,
) -> str:
    """Resolve deploy mode from the workspace runtime; fix stale compose/attach/preview."""
    mode = normalize_deploy_mode(getattr(environment, "deploy_mode", None))

    workspace_id = environment.workspace_id
    if workspace_id is None:
        return mode

    from app.schemas.cloud import WorkspaceWizardConfig
    from app.services.preview_deploy_plan import resolve_preview_deploy_plan
    from app.services.provisioning import ProvisioningService

    row = await session.get(ProvisioningWorkspace, workspace_id)
    if row is None:
        return mode
    snapshot = ProvisioningService(session)._load_wizard_snapshot(row)
    if not snapshot:
        return mode
    try:
        config = WorkspaceWizardConfig.model_validate({**snapshot, "has_credentials": False})
    except Exception:
        logger.warning(
            "effective_deploy_mode_snapshot_invalid",
            workspace_id=str(workspace_id),
            environment_id=str(environment.id),
        )
        return mode

    plan = resolve_preview_deploy_plan(config, requested_deploy_mode=None)
    corrected = plan.deploy_mode.value
    if corrected == mode:
        return mode

    environment.deploy_mode = corrected
    logger.info(
        "deploy_mode_corrected_from_workspace",
        environment_id=str(environment.id),
        workspace_id=str(workspace_id),
        from_mode=mode,
        to_mode=corrected,
        reason=plan.reason,
    )
    return corrected


def _session_factory() -> async_sessionmaker[AsyncSession]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def _emit_log(
    log_repo: DeploymentLogRepository,
    *,
    environment_id: UUID,
    message: str,
    log_level: LogLevel = LogLevel.INFO,
    status: str | None = None,
    commit_sha: str | None = None,
    stage: ExecutionStage | None = None,
    event_type: EnvEventType = "LOG",
) -> None:
    safe_message = sanitize_log_message(message)
    ts = datetime.now(UTC)
    await log_repo.create(
        environment_id=environment_id,
        message=safe_message,
        log_level=log_level,
        stage=stage,
        timestamp=ts,
    )
    logger.info(
        "deployment_log",
        environment_id=str(environment_id),
        log_level=log_level.value,
        stage=stage.value if stage else None,
        message=safe_message,
    )
    await publish_env_event(
        environment_id,
        event_type=event_type,
        status=status,
        commit_sha=commit_sha,
        message=safe_message,
        log_level=log_level.value,
        stage=stage,
        timestamp=ts,
    )


async def _emit_stage(
    log_repo: DeploymentLogRepository,
    session: AsyncSession,
    *,
    environment_id: UUID,
    stage: ExecutionStage,
    message: str,
    commit_sha: str | None = None,
    status: str = EnvironmentStatus.PROVISIONING.value,
    log_level: LogLevel = LogLevel.INFO,
) -> None:
    """Emit a progress log line for a provisioning stage and persist it.

    Collapses the ``_emit_log(...)`` + ``session.commit()`` pattern repeated
    throughout the provision and rebuild pipelines into a single call. Defaults
    ``status`` to PROVISIONING since that is the state during every interim stage.
    """
    await _emit_log(
        log_repo,
        environment_id=environment_id,
        message=message,
        log_level=log_level,
        status=status,
        commit_sha=commit_sha,
        stage=stage,
    )
    await session.commit()


async def _publish_status(
    environment_id: UUID,
    *,
    status: EnvironmentStatus,
    commit_sha: str | None = None,
    message: str | None = None,
    stage: ExecutionStage | None = None,
    preview_url: str | None = None,
    node_port: int | None = None,
    app_ready: bool | None = None,
    notice: str | None = None,
    error_message: str | None = None,
    preview_endpoints: list[dict[str, object]] | None = None,
) -> None:
    await publish_env_event(
        environment_id,
        event_type="STATUS_CHANGE",
        status=status.value,
        commit_sha=commit_sha,
        message=message,
        stage=stage,
        preview_url=preview_url,
        node_port=node_port,
        app_ready=app_ready,
        notice=notice,
        error_message=error_message,
        preview_endpoints=preview_endpoints,
    )


async def _notify_integrations(
    session: AsyncSession,
    environment_id: UUID,
    *,
    event: str,
    message: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """Best-effort Slack/Jira notify; never raises into provision paths."""
    try:
        from app.services.integrations.notifier import IntegrationNotifier

        await IntegrationNotifier(session).notify_environment_event(
            environment_id,
            event=event,  # type: ignore[arg-type]
            message=message,
            correlation_id=correlation_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "integration_notify_hook_failed",
            environment_id=str(environment_id),
            event=event,
            error=str(exc),
        )


async def _record_audit(
    session: AsyncSession,
    *,
    action: AuditAction,
    actor_id: str,
    status: AuditStatus,
    environment_id: UUID,
    workspace_id: UUID | None,
    commit_sha: str | None,
    detail: str | None = None,
) -> None:
    audit = AuditService(session)
    await audit.record(
        action=action,
        actor_id=actor_id,
        status=status,
        environment_id=environment_id,
        workspace_id=workspace_id,
        commit_sha=commit_sha,
        detail=detail,
    )


async def _provision_cancelled(env_repo: "EnvironmentRepository", env_uuid: UUID) -> bool:
    """True when the user stopped or force-deleted mid-provision.

    Re-queries the row so the running provision task observes a cancel/delete
    issued after it started, and aborts at the next checkpoint.
    - ``FAILED``: stop provisioning (no teardown)
    - ``TEARDOWN_PENDING`` / ``DESTROYED``: destroy during provision (teardown follows)
    """
    fresh = await env_repo.get_by_id(env_uuid)
    return fresh is not None and fresh.status in {
        EnvironmentStatus.FAILED,
        EnvironmentStatus.TEARDOWN_PENDING,
        EnvironmentStatus.DESTROYED,
    }


async def _maybe_build_preview_image(
    log_repo: DeploymentLogRepository,
    *,
    settings,
    environment,
    env_uuid: UUID,
    commit_sha: str | None,
    force: bool = False,
) -> tuple[str | None, str | None]:
    """Run BUILD when enabled. Returns (image_ref, resolved_commit_sha)."""
    deploy_mode = getattr(environment, "deploy_mode", DeployMode.PREVIEW.value)
    workload_override = bool(
        environment.workload_image
        and environment.workload_image != settings.default_workload_image
    )
    eligible = preview_build_eligible(
        settings=settings,
        git_repo_url=environment.git_repo_url,
        template_id=environment.template_id,
        deploy_mode=deploy_mode,
        workload_image_override=workload_override and not force,
    )
    if not eligible:
        return None, commit_sha

    build_sha = commit_sha
    await _emit_log(
        log_repo,
        environment_id=env_uuid,
        message=(
            f"BUILD - cloning {environment.git_repo_url} "
            f"({environment.git_branch}) and building Dockerfile"
        ),
        status=EnvironmentStatus.PROVISIONING.value,
        commit_sha=build_sha,
        stage=ExecutionStage.BUILD,
    )

    try:
        result = await build_preview_image(
            settings=settings,
            environment_id=str(environment.id),
            git_repo_url=environment.git_repo_url,
            git_branch=environment.git_branch,
            commit_sha=build_sha,
        )
    except PreviewBuildError as exc:
        raise RuntimeError(str(exc)) from exc

    mode = "simulated" if result.simulated else "built"
    await _emit_log(
        log_repo,
        environment_id=env_uuid,
        message=f"BUILD - {mode} image {result.image} (commit {result.commit_sha})",
        status=EnvironmentStatus.PROVISIONING.value,
        commit_sha=result.commit_sha,
        stage=ExecutionStage.BUILD,
    )
    return result.image, result.commit_sha


async def _fail_execution(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    environment_id: UUID,
    error_text: str,
    commit_sha: str | None,
    stage: ExecutionStage,
    audit_action: AuditAction,
    actor_id: str,
    workspace_id: UUID | None,
) -> None:
    async with session_factory() as session:
        env_repo = EnvironmentRepository(session)
        log_repo = DeploymentLogRepository(session)
        environment = await env_repo.get_by_id(environment_id)
        if environment is None:
            return
        await _emit_log(
            log_repo,
            environment_id=environment_id,
            message=error_text,
            log_level=LogLevel.ERROR,
            status=EnvironmentStatus.FAILED.value,
            commit_sha=commit_sha or environment.latest_commit_sha,
            stage=stage,
            event_type="EXECUTION_FAILED",
        )
        await _record_audit(
            session,
            action=audit_action,
            actor_id=actor_id,
            status=AuditStatus.FAILURE,
            environment_id=environment_id,
            workspace_id=workspace_id or environment.workspace_id,
            commit_sha=commit_sha or environment.latest_commit_sha,
            detail=error_text,
        )
        await _record_audit(
            session,
            action=AuditAction.EXECUTION_FAILED,
            actor_id=actor_id,
            status=AuditStatus.FAILURE,
            environment_id=environment_id,
            workspace_id=workspace_id or environment.workspace_id,
            commit_sha=commit_sha or environment.latest_commit_sha,
            detail=error_text,
        )
        await env_repo.update_status(
            environment,
            EnvironmentStatus.FAILED,
            error_message=error_text,
            latest_commit_sha=commit_sha,
        )
        await session.commit()
        await _publish_status(
            environment_id,
            status=EnvironmentStatus.FAILED,
            commit_sha=commit_sha or environment.latest_commit_sha,
            message=error_text,
            stage=stage,
            app_ready=False,
            error_message=error_text,
        )
        async with session_factory() as notify_session:
            await _notify_integrations(
                notify_session,
                environment_id,
                event="failed",
                message=error_text,
            )
            await notify_session.commit()


async def _attempt_rebuild_rollback(
    session_factory: async_sessionmaker[AsyncSession],
    provisioner: "KubernetesProvisioner",
    *,
    env_uuid: UUID,
    eligible: bool,
    previous_image: str | None,
    previous_commit: str | None,
    failed_commit: str | None,
    stage: ExecutionStage,
    actor_id: str,
    workspace_id: UUID | None,
    error_text: str,
) -> bool:
    """Restore the last known-good deployment after a failed rebuild.

    Returns True when the workload was rolled back to the previous image and the
    environment is RUNNING again; False when there is nothing to roll back to or
    the restore itself fails (caller then marks the environment FAILED).
    """
    if not eligible or not (previous_image or previous_commit):
        return False

    restore_ref = previous_commit or "last-good"
    async with session_factory() as session:
        env_repo = EnvironmentRepository(session)
        log_repo = DeploymentLogRepository(session)
        user_repo = UserRepository(session)
        environment = await env_repo.get_by_id(env_uuid)
        if environment is None:
            return False
        if environment.status == EnvironmentStatus.TEARDOWN_PENDING:
            # A delete raced in while we were failing; let teardown win.
            return False

        owner = await user_repo.get_by_id(environment.owner_id)
        owner_label = owner.email if owner is not None else str(environment.owner_id)

        await _emit_log(
            log_repo,
            environment_id=env_uuid,
            message=(
                f"ROLLBACK - rebuild of {failed_commit or 'new revision'} failed; "
                f"restoring last working revision {restore_ref}"
            ),
            log_level=LogLevel.WARN,
            status=EnvironmentStatus.PROVISIONING.value,
            commit_sha=restore_ref,
            stage=stage,
        )
        await session.commit()

        try:
            provisioner.rebuild_workload(
                namespace=environment.namespace_name,
                environment_id=str(environment.id),
                name=environment.name,
                git_branch=environment.git_branch,
                git_repo_url=environment.git_repo_url,
                commit_sha=previous_commit or "",
                owner_label=owner_label,
                image=previous_image,
                enable_postgres=getattr(environment, "enable_postgres", False),
                enable_redis=getattr(environment, "enable_redis", False),
            )
        except Exception:
            logger.exception(
                "rebuild_rollback_failed",
                environment_id=str(env_uuid),
                previous_image=previous_image,
                previous_commit=previous_commit,
            )
            return False

        await env_repo.update_status(
            environment,
            EnvironmentStatus.RUNNING,
            latest_commit_sha=previous_commit,
            error_message=None,
            workload_image=previous_image or environment.workload_image,
        )
        await _emit_log(
            log_repo,
            environment_id=env_uuid,
            message=(
                f"ROLLBACK - restored last working revision {restore_ref}, RUNNING. "
                f"Failed rebuild: {error_text}"
            ),
            log_level=LogLevel.WARN,
            status=EnvironmentStatus.RUNNING.value,
            commit_sha=restore_ref,
            stage=stage,
        )
        # Record both the failure and the recovery so the audit trail is honest.
        await _record_audit(
            session,
            action=AuditAction.REBUILD_FAILED,
            actor_id=actor_id,
            status=AuditStatus.FAILURE,
            environment_id=env_uuid,
            workspace_id=workspace_id or environment.workspace_id,
            commit_sha=failed_commit,
            detail=error_text,
        )
        await _record_audit(
            session,
            action=AuditAction.REBUILD_ROLLED_BACK,
            actor_id=actor_id,
            status=AuditStatus.SUCCESS,
            environment_id=env_uuid,
            workspace_id=workspace_id or environment.workspace_id,
            commit_sha=previous_commit,
            detail=f"Rolled back to {restore_ref} after failed rebuild of {failed_commit or 'new revision'}",
        )
        await session.commit()
        await _publish_status(
            env_uuid,
            status=EnvironmentStatus.RUNNING,
            commit_sha=restore_ref,
            message=f"Rebuild failed; rolled back to last working revision {restore_ref}",
            stage=stage,
        )
    return True


async def _run_provision(environment_id: str, correlation_id: str) -> None:
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    settings = get_settings()
    session_factory = _session_factory()
    provisioner = KubernetesProvisioner(settings)
    resources: ProvisionedResources | None = None
    env_uuid = UUID(environment_id)
    actor_id = f"system:celery:{correlation_id}"
    workspace_id: UUID | None = None

    try:
        async with acquire_state_lock(
            env_uuid,
            scope="environment",
            settings=settings,
            timeout_seconds=getattr(settings, "teardown_state_lock_timeout_seconds", None),
        ):
            async with session_factory() as session:
                env_repo = EnvironmentRepository(session)
                log_repo = DeploymentLogRepository(session)
                user_repo = UserRepository(session)
                environment = await env_repo.get_by_id(env_uuid)
                if environment is None:
                    logger.error("provision_missing_environment", environment_id=environment_id)
                    return

                owner = await user_repo.get_by_id(environment.owner_id)
                owner_label = owner.email if owner is not None else str(environment.owner_id)
                actor_id = str(environment.owner_id)
                workspace_id = environment.workspace_id
                commit_sha = environment.latest_commit_sha
                current_stage = ExecutionStage.INIT
                deploy_mode = await _effective_deploy_mode(session, environment)
                await session.commit()

                await _emit_stage(
                    log_repo,
                    session,
                    environment_id=env_uuid,
                    stage=ExecutionStage.INIT,
                    message=init_workflow_message(deploy_mode),
                    commit_sha=commit_sha,
                )

                try:
                    current_stage = ExecutionStage.VALIDATE
                    if deploy_mode == DeployMode.COMPOSE.value:
                        from app.services.compose_deploy import docker_compose_available

                        if docker_compose_available():
                            mode_msg = "VALIDATE - docker compose available (local Compose preview)"
                        else:
                            mode_msg = (
                                "VALIDATE - docker compose unavailable; "
                                "Compose deploy will simulate preview URL"
                            )
                    elif deploy_mode == DeployMode.ATTACH.value:
                        mode_msg = (
                            "VALIDATE - running instance "
                            "(local Docker / VM SSH / serverless; no Kubernetes apply)"
                        )
                    elif settings.kubernetes_enabled:
                        from app.services.cloud_kubernetes import is_cloud_kubernetes_deploy

                        provider = getattr(environment, "provider", None)
                        is_local = str(provider or "").lower() == "local"
                        ctx = settings.resolved_kubernetes_context or settings.kubernetes_context or "default"
                        if is_cloud_kubernetes_deploy(
                            provider=str(provider or ""),
                            deploy_mode=deploy_mode,
                        ):
                            provisioner = await _retarget_provisioner_for_cloud_k8s(
                                session,
                                environment=environment,
                                provisioner=provisioner,
                                deploy_mode=deploy_mode,
                                create_cluster=True,
                                log_repo=log_repo,
                                env_uuid=env_uuid,
                                commit_sha=commit_sha,
                            )
                            provisioner.assert_cluster_ready(timeout_seconds=30.0)
                            mode_msg = (
                                "VALIDATE - cloud Kubernetes reachable "
                                f"(context={provisioner._kube_context or ctx})"
                            )
                        else:
                            # Auto-start the local cluster for local Kubernetes previews.
                            targets_local_cluster = (
                                is_local or ctx == settings.local_cluster_context
                            )
                            if targets_local_cluster:
                                from app.services.kind_cluster import ensure_kind_cluster

                                await _emit_log(
                                    log_repo,
                                    environment_id=env_uuid,
                                    message="INIT - ensuring local Kubernetes cluster is ready",
                                    status=EnvironmentStatus.PROVISIONING.value,
                                    commit_sha=commit_sha,
                                    stage=ExecutionStage.INIT,
                                )
                                await session.commit()
                                await ensure_kind_cluster()
                                provisioner.reload_clients()
                            provisioner.assert_cluster_ready(timeout_seconds=20.0)
                            mode_msg = f"VALIDATE - cluster reachable (context={ctx})"
                    else:
                        mode_msg = (
                            "VALIDATE - simulate mode (KUBERNETES_ENABLED=false); "
                            "no cluster mutations"
                        )
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message=mode_msg,
                        status=EnvironmentStatus.PROVISIONING.value,
                        commit_sha=commit_sha,
                        stage=ExecutionStage.VALIDATE,
                    )
                    await _record_audit(
                        session,
                        action=AuditAction.PROVISION_VALIDATED,
                        actor_id=actor_id,
                        status=AuditStatus.SUCCESS,
                        environment_id=env_uuid,
                        workspace_id=workspace_id,
                        commit_sha=commit_sha,
                        detail=mode_msg,
                    )
                    await session.commit()

                    await asyncio.sleep(settings.provision_step_delay_seconds)

                    current_stage = ExecutionStage.PLAN
                    if deploy_mode == DeployMode.MANIFEST.value:
                        plan_image_note = "from workspace manifests"
                    elif deploy_mode in NON_K8S_DEPLOY_MODES:
                        plan_image_note = (
                            environment.workload_image
                            or settings.default_workload_image
                            or "from workspace"
                        )
                    else:
                        plan_image_note = (
                            environment.workload_image or settings.default_workload_image
                        )
                    if deploy_mode == DeployMode.ATTACH.value:
                        plan_msg = (
                            f"PLAN - running-instance deploy "
                            f"(mode={deploy_mode}), image {plan_image_note}"
                        )
                    elif deploy_mode == DeployMode.COMPOSE.value:
                        plan_msg = (
                            f"PLAN - docker compose deploy "
                            f"(mode={deploy_mode}), image {plan_image_note}"
                        )
                    else:
                        plan_msg = (
                            f"PLAN - namespace {environment.namespace_name}, "
                            f"image {plan_image_note}"
                        )
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message=plan_msg,
                        status=EnvironmentStatus.PROVISIONING.value,
                        commit_sha=commit_sha,
                        stage=ExecutionStage.PLAN,
                    )
                    await _record_audit(
                        session,
                        action=AuditAction.PROVISION_PLANNED,
                        actor_id=actor_id,
                        status=AuditStatus.SUCCESS,
                        environment_id=env_uuid,
                        workspace_id=workspace_id,
                        commit_sha=commit_sha,
                        detail=plan_msg,
                    )
                    await session.commit()

                    deploy_image = environment.workload_image or settings.default_workload_image
                    # Attach/compose resolve images from the workspace; skip git preview builds.
                    if deploy_mode not in NON_K8S_DEPLOY_MODES:
                        built_image, built_sha = await _maybe_build_preview_image(
                            log_repo,
                            settings=settings,
                            environment=environment,
                            env_uuid=env_uuid,
                            commit_sha=commit_sha,
                        )
                    else:
                        built_image, built_sha = None, None
                    if built_image:
                        deploy_image = built_image
                        commit_sha = built_sha or commit_sha
                        environment.workload_image = deploy_image
                        if built_sha:
                            await env_repo.update_status(
                                environment,
                                EnvironmentStatus.PROVISIONING,
                                latest_commit_sha=built_sha,
                            )
                        await session.commit()

                    # MANIFEST deploys: pass built image or custom workload image if set.
                    # Otherwise ManifestDeployer inspects deployment.yaml/values.yaml.
                    manifest_image_override = (
                        built_image
                        or (
                            environment.workload_image
                            if environment.workload_image and environment.workload_image != settings.default_workload_image
                            else None
                        )
                        if deploy_mode == DeployMode.MANIFEST.value
                        else None
                    )

                    # Cooperative cancellation checkpoint: a force-delete request
                    # flips the row to TEARDOWN_PENDING. Abort before the expensive
                    # APPLY + readiness wait rather than provisioning resources the
                    # teardown task must immediately reclaim.
                    if await _provision_cancelled(env_repo, env_uuid):
                        raise PreviewCancelled(f"provision cancelled for {environment_id}")

                    current_stage = ExecutionStage.APPLY
                    workspace_root: Path | None = None
                    wizard_snapshot: dict | None = None
                    manifest_credentials = None
                    if workspace_id is not None:
                        workspace_row = await session.get(ProvisioningWorkspace, workspace_id)
                        if workspace_row is not None:
                            from app.services.provisioning import ProvisioningService

                            owner_row = await session.get(User, environment.owner_id)
                            if owner_row is not None:
                                from fastapi import HTTPException

                                provisioning = ProvisioningService(session)
                                wizard_snapshot = provisioning._load_wizard_snapshot(workspace_row)
                                try:
                                    workspace_row = await provisioning._ensure_workspace_on_disk(
                                        workspace_row,
                                        owner_row,
                                    )
                                except HTTPException as exc:
                                    detail = exc.detail
                                    if isinstance(detail, dict):
                                        message = str(detail.get("message") or detail)
                                    else:
                                        message = str(detail)
                                    raise RuntimeError(message) from exc
                            workspace_root = Path(workspace_row.root_dir)
                            manifest_credentials = await _merge_attach_credentials_from_vault(
                                session,
                                attach_credentials=None,
                                owner_id=environment.owner_id,
                                provider=getattr(environment, "provider", None),
                            )

                    k8s_image_source = _resolve_k8s_image_source(environment, wizard_snapshot)
                    image_scan = _resolve_image_scan_config(environment, wizard_snapshot)
                    cloud_provider = str(getattr(environment, "provider", None) or "local").lower()
                    from app.schemas.cloud import CloudProvider, KubernetesImageSource
                    from app.services.cloud_promote import default_region

                    def _cloud_region_for_provider(provider: str, snapshot: dict | None) -> str:
                        try:
                            region = default_region(CloudProvider(provider))
                        except ValueError:
                            region = "us-central1"
                        if isinstance(snapshot, dict):
                            cloud = snapshot.get("cloud")
                            if isinstance(cloud, dict):
                                resources_cfg = cloud.get("resources")
                                if isinstance(resources_cfg, dict):
                                    region = str(
                                        resources_cfg.get("region")
                                        or resources_cfg.get("location")
                                        or region
                                    )
                        return region

                    if (
                        deploy_mode == DeployMode.PREVIEW.value
                        and workspace_root is not None
                        and k8s_image_source == KubernetesImageSource.BUILD_REGISTRY.value
                        and cloud_provider != CloudProvider.LOCAL.value
                    ):
                        from app.services.attach_deploy import resolve_instance_image

                        try:
                            region = _cloud_region_for_provider(cloud_provider, wizard_snapshot)
                            deploy_image = resolve_instance_image(
                                image=environment.workload_image,
                                workspace_root=workspace_root,
                                environment_id=str(environment.id),
                                settings=settings,
                                cloud_provider=cloud_provider,
                                credentials=manifest_credentials,
                                region=region,
                                platform=provisioner.container_build_platform(),
                                image_scan=image_scan,
                            )
                            environment.workload_image = deploy_image
                            await session.commit()
                        except Exception as exc:  # noqa: BLE001
                            raise RuntimeError(str(exc)) from exc

                    if deploy_mode == DeployMode.MANIFEST.value and workspace_root is not None:
                        from app.services.manifest_deploy import workspace_has_helm_chart

                        helm_note = (
                            " (Helm chart)"
                            if workspace_has_helm_chart(workspace_root)
                            else ""
                        )
                        build_note = (
                            "build+push to registry"
                            if k8s_image_source == KubernetesImageSource.BUILD_REGISTRY.value
                            else "external image"
                        )
                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message=(
                                f"APPLY - deploying workspace Kubernetes manifests{helm_note} "
                                f"({build_note})"
                            ),
                            commit_sha=commit_sha,
                        )

                        region = _cloud_region_for_provider(cloud_provider, wizard_snapshot)

                        deployer = ManifestDeployer(settings, provisioner)
                        resources = deployer.deploy(
                            workspace_root=workspace_root,
                            namespace=environment.namespace_name,
                            environment_id=str(environment.id),
                            name=environment.name,
                            git_branch=environment.git_branch,
                            git_repo_url=environment.git_repo_url,
                            ttl_expires_at=environment.ttl_expires_at.isoformat(),
                            owner_label=owner_label,
                            image=manifest_image_override,
                            cloud_provider=cloud_provider,
                            credentials=manifest_credentials,
                            region=region,
                            image_source=k8s_image_source,
                            image_scan=image_scan,
                        )
                    elif deploy_mode == DeployMode.COMPOSE.value:
                        from app.services.compose_deploy import ComposeDeployError, deploy_compose

                        if workspace_root is None:
                            raise RuntimeError(
                                "Compose deploy requires a linked workspace with docker-compose.yml"
                            )
                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message="APPLY - docker compose up (local Compose preview)",
                            commit_sha=commit_sha,
                        )
                        try:
                            resources = await asyncio.to_thread(
                                deploy_compose,
                                workspace_root=workspace_root,
                                namespace=environment.namespace_name,
                                environment_id=str(environment.id),
                                name=environment.name,
                                image=deploy_image,
                                settings=settings,
                                primary_host_preference=_compose_primary_host_preference(
                                    wizard_snapshot
                                ),
                            )
                        except ComposeDeployError as exc:
                            raise RuntimeError(str(exc)) from exc
                    elif deploy_mode == DeployMode.ATTACH.value:
                        from app.schemas.cloud import (
                            KubernetesPackaging,
                            RunningInstanceConfig,
                            WorkspaceWizardConfig,
                        )
                        from app.services.attach_deploy import AttachDeployError, deploy_attach, prepare_attach_deploy
                        from app.services.provisioning import ProvisioningService

                        running_instance = RunningInstanceConfig()
                        attach_services = None
                        attach_runtime_mode = None
                        packaging: KubernetesPackaging | None = None
                        attach_credentials = None
                        workspace_encrypted: str | None = None
                        workspace_provider: str | None = None
                        wizard_cloud_provider: str | None = None
                        create_vpc = False
                        create_subnets = False
                        existing_vpc_id: str | None = None
                        existing_security_group_id: str | None = None
                        gcp_project_id: str | None = None
                        attach_org_slug = await _attach_org_slug(session, environment)
                        if workspace_id is not None:
                            provisioning = ProvisioningService(session)
                            workspace_row = await session.get(
                                ProvisioningWorkspace, workspace_id
                            )
                            if workspace_row is not None:
                                workspace_encrypted = workspace_row.encrypted_credentials
                                workspace_provider = workspace_row.provider
                                packaging = provisioning.get_workspace_kubernetes_packaging(
                                    workspace_row
                                )
                                snapshot = provisioning._load_wizard_snapshot(workspace_row)
                                wizard_cloud_provider = _wizard_cloud_provider(snapshot)
                                create_vpc, create_subnets = _wizard_network_flags(snapshot)
                                existing_vpc_id = _wizard_existing_vpc_id(snapshot)
                                existing_security_group_id = _wizard_existing_security_group_id(snapshot)
                                gcp_project_id = _wizard_gcp_project_id(snapshot)
                                if snapshot is not None:
                                    try:
                                        wizard = WorkspaceWizardConfig.model_validate(
                                            {**snapshot, "has_credentials": False}
                                        )
                                        running_instance = wizard.running_instance
                                        attach_services = list(
                                            wizard.container_scaffold.services or []
                                        )
                                        attach_runtime_mode = wizard.runtime_mode
                                    except Exception:
                                        logger.warning(
                                            "attach_wizard_snapshot_invalid",
                                            workspace_id=str(workspace_id),
                                        )
                        running_instance, attach_credentials, attach_provider = prepare_attach_deploy(
                            running_instance=running_instance,
                            cloud_provider=getattr(environment, "provider", None),
                            environment_name=environment.name,
                            encrypted_credentials=workspace_encrypted,
                            runtime_mode=attach_runtime_mode,
                            workspace_provider=workspace_provider,
                            wizard_cloud_provider=wizard_cloud_provider,
                        )
                        attach_credentials = await _merge_attach_credentials_from_vault(
                            session,
                            attach_credentials=attach_credentials,
                            owner_id=environment.owner_id,
                            provider=attach_provider,
                        )

                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message=(
                                f"APPLY - deploying to running instance "
                                f"({running_instance.kind.value}/{attach_provider})"
                            ),
                            commit_sha=commit_sha,
                        )
                        try:
                            resources = await asyncio.to_thread(
                                deploy_attach,
                                namespace=environment.namespace_name,
                                environment_id=str(environment.id),
                                name=environment.name,
                                git_branch=environment.git_branch,
                                git_repo_url=environment.git_repo_url,
                                ttl_expires_at=environment.ttl_expires_at.isoformat(),
                                owner_label=owner_label,
                                image=deploy_image,
                                enable_postgres=getattr(environment, "enable_postgres", False),
                                enable_redis=getattr(environment, "enable_redis", False),
                                running_instance=running_instance,
                                workspace_root=workspace_root,
                                packaging=packaging,
                                settings=settings,
                                services=attach_services,
                                cloud_provider=attach_provider,
                                credentials=attach_credentials,
                                org_slug=attach_org_slug,
                                workspace_provider=workspace_provider,
                                wizard_cloud_provider=wizard_cloud_provider,
                                create_vpc=create_vpc,
                                create_subnets=create_subnets,
                                gcp_project_id=gcp_project_id,
                                existing_vpc_id=existing_vpc_id,
                                existing_security_group_id=existing_security_group_id,
                            )
                        except AttachDeployError as exc:
                            raise RuntimeError(str(exc)) from exc
                    else:
                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message="APPLY - mutating Kubernetes resources (preview profile)",
                            commit_sha=commit_sha,
                        )
                        # Ephemeral datastores are applied inside provision() now,
                        # after the namespace exists and before the app workload.
                        resources = provisioner.provision(
                            namespace=environment.namespace_name,
                            environment_id=str(environment.id),
                            name=environment.name,
                            git_branch=environment.git_branch,
                            git_repo_url=environment.git_repo_url,
                            ttl_expires_at=environment.ttl_expires_at.isoformat(),
                            owner_label=owner_label,
                            image=deploy_image,
                            enable_postgres=getattr(environment, "enable_postgres", False),
                            enable_redis=getattr(environment, "enable_redis", False),
                            credentials=manifest_credentials,
                            cloud_provider=cloud_provider,
                            region=_cloud_region_for_provider(cloud_provider, wizard_snapshot),
                        )

                    if resources.simulated:
                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message=(
                                "APPLY - simulated preview resources "
                                f"(mode={deploy_mode})"
                            ),
                            commit_sha=commit_sha,
                        )
                    elif deploy_mode == DeployMode.COMPOSE.value:
                        port_note = (
                            f", host port {resources.node_port}"
                            if resources.node_port is not None
                            else ""
                        )
                        remap_note = (
                            f"; {resources.notice}"
                            if getattr(resources, "notice", None)
                            else ""
                        )
                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message=(
                                f"APPLY - docker compose stack ready"
                                f"{port_note}{remap_note}; "
                                f"preview={resources.preview_url or '-'}"
                            ),
                            commit_sha=commit_sha,
                        )
                    elif deploy_mode == DeployMode.ATTACH.value:
                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message=(
                                f"APPLY - attached running instance; "
                                f"preview={resources.preview_url or '-'}"
                            ),
                            commit_sha=commit_sha,
                        )
                    else:
                        port_note = (
                            f", NodePort {resources.node_port}"
                            if resources.node_port is not None
                            else ""
                        )
                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message=(
                                f"APPLY - ResourceQuota + LimitRange + zero-trust NetworkPolicy; "
                                f"Deployment/Service ready (image={resources.image}{port_note})"
                            ),
                            commit_sha=commit_sha,
                        )

                    # Final cancellation checkpoint: if the user force-deleted
                    # during APPLY, hand the just-applied resources to the teardown
                    # task instead of promoting to RUNNING.
                    if await _provision_cancelled(env_repo, env_uuid):
                        raise PreviewCancelled(f"provision cancelled for {environment_id}")

                    # Resolve the Open-app URL by target:
                    #  • attach/compose + CF named tunnel → ws-* Ingress → Docker host port
                    #  • local + tunnel on → cloudflared quick-tunnel URL (fallback)
                    #  • local + tunnel off → localhost NodePort URL (provisioner default)
                    #  • cloud/production k8s → LoadBalancer/Ingress public URL
                    await _attach_docker_host_preview_ingress(
                        env_uuid, environment, resources, provisioner
                    )
                    await _attach_preview_tunnel(env_uuid, environment, resources)
                    await _attach_cloud_preview_url(env_uuid, environment, resources, provisioner)

                    from app.schemas.environment import dump_preview_endpoints

                    endpoints = list(getattr(resources, "preview_endpoints", None) or [])
                    endpoints_json = dump_preview_endpoints(endpoints) if endpoints else None
                    await env_repo.update_status(
                        environment,
                        EnvironmentStatus.RUNNING,
                        preview_url=resources.preview_url,
                        node_port=resources.node_port,
                        workload_image=resources.image,
                        preview_endpoints_json=endpoints_json,
                    )
                    if endpoints:
                        preview_note = " Previews: " + ", ".join(
                            f"{e.get('name')}={e.get('url')}" for e in endpoints
                        )
                    else:
                        preview_note = (
                            f" Open app: {resources.preview_url}"
                            if resources.preview_url
                            else ""
                        )
                    remap_note = (
                        f" {resources.notice}."
                        if getattr(resources, "notice", None)
                        else ""
                    )
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message=(
                            f"APPLY - provision completed, RUNNING.{remap_note}{preview_note}"
                        ),
                        status=EnvironmentStatus.RUNNING.value,
                        commit_sha=commit_sha,
                        stage=ExecutionStage.APPLY,
                    )
                    await _record_audit(
                        session,
                        action=AuditAction.PROVISION_SUCCEEDED,
                        actor_id=actor_id,
                        status=AuditStatus.SUCCESS,
                        environment_id=env_uuid,
                        workspace_id=workspace_id,
                        commit_sha=commit_sha,
                        detail=resources.preview_url,
                    )
                    if deploy_mode == DeployMode.ATTACH.value:
                        await _sync_workspace_after_cloud_deploy(
                            session,
                            workspace_id=workspace_id,
                            resources=resources,
                            cloud_provider=attach_provider,
                        )
                    await session.commit()

                    if environment.github_pr_number is not None:
                        from app.services.github_preview import notify_preview_ready

                        notify = notify_preview_ready(environment, settings=settings)
                        if notify.commented or notify.status_set:
                            smoke_note = "smoke=pass" if notify.smoke_ok else f"smoke=fail:{notify.message}"
                            await _emit_log(
                                log_repo,
                                environment_id=env_uuid,
                                message=(
                                    f"Posted GitHub PR #{environment.github_pr_number} "
                                    f"preview notify ({notify.message}; {smoke_note})"
                                ),
                                status=EnvironmentStatus.RUNNING.value,
                                commit_sha=commit_sha,
                                stage=ExecutionStage.APPLY,
                            )
                            if environment.github_pr_url:
                                await env_repo.update_status(
                                    environment,
                                    EnvironmentStatus.RUNNING,
                                    github_pr_url=environment.github_pr_url,
                                )
                            await session.commit()
                        elif notify.message not in {
                            "no_pr_linked",
                            "github_app_not_configured",
                        }:
                            await _emit_log(
                                log_repo,
                                environment_id=env_uuid,
                                message=f"GitHub PR notify skipped: {notify.message}",
                                status=EnvironmentStatus.RUNNING.value,
                                commit_sha=commit_sha,
                                stage=ExecutionStage.APPLY,
                            )
                            await session.commit()

                    await _publish_status(
                        env_uuid,
                        status=EnvironmentStatus.RUNNING,
                        commit_sha=commit_sha,
                        message=resources.preview_url or "Provision completed",
                        stage=ExecutionStage.APPLY,
                        preview_url=resources.preview_url,
                        node_port=resources.node_port,
                        app_ready=bool(resources.preview_url),
                        notice=getattr(resources, "notice", None),
                        preview_endpoints=endpoints or None,
                    )
                    await _notify_integrations(
                        session,
                        env_uuid,
                        event="ready",
                        message=resources.preview_url or "Provision completed",
                        correlation_id=correlation_id,
                    )
                    await session.commit()
                except PreviewCancelled:
                    # User stopped provision (FAILED) or force-deleted (TEARDOWN_PENDING).
                    # Only enqueue teardown for the destroy path.
                    logger.info("provision_cancelled", environment_id=environment_id)
                    await session.rollback()
                    try:
                        cancelled = await env_repo.get_by_id(env_uuid)
                        if (
                            cancelled is not None
                            and cancelled.status == EnvironmentStatus.TEARDOWN_PENDING
                        ):
                            enqueue_teardown_environment(
                                environment_id=str(env_uuid),
                                correlation_id=f"post-cancel:{env_uuid}",
                            )
                        elif (
                            cancelled is not None
                            and cancelled.status == EnvironmentStatus.FAILED
                        ):
                            await _publish_status(
                                env_uuid,
                                status=EnvironmentStatus.FAILED,
                                commit_sha=cancelled.latest_commit_sha,
                                message=cancelled.error_message or "Provisioning stopped",
                                stage=ExecutionStage.APPLY,
                                app_ready=False,
                                error_message=cancelled.error_message
                                or "Provisioning stopped by user",
                            )
                    except Exception:
                        logger.exception(
                            "post_cancel_followup_failed",
                            environment_id=environment_id,
                        )
                    return
                except Exception as exc:
                    logger.exception("provision_failed", environment_id=environment_id)
                    await session.rollback()
                    if resources is None:
                        # Some deployers (notably ManifestDeployer) can raise before returning resources.
                        # When the exception provides them, persist preview_url/node_port/workload_image.
                        resources = getattr(exc, "provisioned_resources", None)
                    # If Ready timed out but the manifest image is live now, treat as success
                    # (kind control-plane blips). Avoid leaving FAILED + nginx Open confusion.
                    if (
                        resources is not None
                        and isinstance(exc, TimeoutError)
                        and resources.preview_url
                        and resources.image
                    ):
                        try:
                            provisioner.wait_for_workload_ready(
                                namespace=resources.namespace,
                                timeout_seconds=15.0,
                                expected_image=resources.image,
                                app_label=resources.app_label,
                            )
                            environment = await env_repo.get_by_id(env_uuid)
                            if environment is not None:
                                await _attach_docker_host_preview_ingress(
                                    env_uuid, environment, resources, provisioner
                                )
                                await _attach_preview_tunnel(env_uuid, environment, resources)
                                await _attach_cloud_preview_url(env_uuid, environment, resources, provisioner)
                                await env_repo.update_status(
                                    environment,
                                    EnvironmentStatus.RUNNING,
                                    preview_url=resources.preview_url,
                                    node_port=resources.node_port,
                                    workload_image=resources.image,
                                )
                                await _emit_log(
                                    log_repo,
                                    environment_id=env_uuid,
                                    message=(
                                        "APPLY - provision completed after Ready deadline "
                                        f"(image={resources.image}). Open app: {resources.preview_url}"
                                    ),
                                    status=EnvironmentStatus.RUNNING.value,
                                    commit_sha=commit_sha,
                                    stage=ExecutionStage.APPLY,
                                )
                                await session.commit()
                                await _publish_status(
                                    env_uuid,
                                    status=EnvironmentStatus.RUNNING,
                                    commit_sha=commit_sha,
                                    message=resources.preview_url,
                                    stage=ExecutionStage.APPLY,
                                    preview_url=resources.preview_url,
                                    node_port=resources.node_port,
                                    app_ready=bool(resources.preview_url),
                                    notice=getattr(resources, "notice", None),
                                )
                                await _notify_integrations(
                                    session,
                                    env_uuid,
                                    event="ready",
                                    message=resources.preview_url,
                                    correlation_id=correlation_id,
                                )
                                await session.commit()
                                return
                        except Exception:
                            logger.info(
                                "provision_late_ready_check_failed",
                                environment_id=environment_id,
                                namespace=resources.namespace,
                            )
                    if resources is not None:
                        try:
                            provisioner.rollback(resources)
                        except Exception:
                            logger.exception(
                                "provision_rollback_failed",
                                environment_id=environment_id,
                                namespace=resources.namespace,
                            )
                        # Keep NodePort/image on the env so Open app does not fall back
                        # to a different nginx preview URL after a partial apply.
                        if resources.preview_url or resources.node_port or resources.image:
                            try:
                                environment = await env_repo.get_by_id(env_uuid)
                                if environment is not None:
                                    if resources.preview_url:
                                        environment.preview_url = resources.preview_url
                                    if resources.node_port is not None:
                                        environment.node_port = resources.node_port
                                    if resources.image:
                                        environment.workload_image = resources.image
                                    await session.commit()
                            except Exception:
                                logger.exception(
                                    "provision_partial_metadata_persist_failed",
                                    environment_id=environment_id,
                                )
                    error_text = sanitize_log_message(str(exc))
                    await _fail_execution(
                        session_factory,
                        environment_id=env_uuid,
                        error_text=f"Provision failed: {error_text}",
                        commit_sha=commit_sha,
                        stage=current_stage,
                        audit_action=AuditAction.PROVISION_FAILED,
                        actor_id=actor_id,
                        workspace_id=workspace_id,
                    )
    except StateLockConflict:
        logger.warning(
            "provision_skipped_lock_held",
            environment_id=environment_id,
            message=PROVISIONING_IN_PROGRESS_MESSAGE,
        )
        async with session_factory() as session:
            log_repo = DeploymentLogRepository(session)
            await _emit_log(
                log_repo,
                environment_id=env_uuid,
                message=PROVISIONING_IN_PROGRESS_MESSAGE,
                log_level=LogLevel.WARN,
                stage=ExecutionStage.INIT,
            )
            await _record_audit(
                session,
                action=AuditAction.PROVISION_INITIATED,
                actor_id=actor_id,
                status=AuditStatus.REJECTED,
                environment_id=env_uuid,
                workspace_id=workspace_id,
                commit_sha=None,
                detail=PROVISIONING_IN_PROGRESS_MESSAGE,
            )
            await session.commit()


async def _run_rebuild(environment_id: str, commit_sha: str, correlation_id: str) -> None:
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    settings = get_settings()
    session_factory = _session_factory()
    provisioner = KubernetesProvisioner(settings)
    env_uuid = UUID(environment_id)
    short_sha = commit_sha.strip()
    actor_id = f"webhook:github"
    workspace_id: UUID | None = None

    try:
        async with acquire_state_lock(env_uuid, scope="environment", settings=settings):
            async with session_factory() as session:
                env_repo = EnvironmentRepository(session)
                log_repo = DeploymentLogRepository(session)
                user_repo = UserRepository(session)
                environment = await env_repo.get_by_id(env_uuid)
                if environment is None:
                    logger.error("rebuild_missing_environment", environment_id=environment_id)
                    return

                owner = await user_repo.get_by_id(environment.owner_id)
                owner_label = owner.email if owner is not None else str(environment.owner_id)
                workspace_id = environment.workspace_id
                current_stage = ExecutionStage.INIT

                # Snapshot the last known-good deployment before mutating anything,
                # so a failed rebuild can roll the workload back to it. Only a
                # previously-RUNNING preview has a working state worth restoring.
                rollback_eligible = environment.status == EnvironmentStatus.RUNNING
                previous_image = environment.workload_image
                previous_commit = environment.latest_commit_sha
                # Set once the new revision is live and healthy; past this point a
                # failure is bookkeeping only, so we must NOT revert the workload.
                new_rollout_ok = False

                if environment.status == EnvironmentStatus.TEARDOWN_PENDING:
                    await _emit_stage(
                        log_repo,
                        session,
                        environment_id=env_uuid,
                        stage=ExecutionStage.INIT,
                        message="Skipping rebuild - environment is tearing down",
                        commit_sha=short_sha,
                        status=environment.status.value,
                        log_level=LogLevel.WARN,
                    )
                    return

                if environment.status != EnvironmentStatus.PROVISIONING:
                    await env_repo.update_status(
                        environment,
                        EnvironmentStatus.PROVISIONING,
                        latest_commit_sha=short_sha,
                        error_message=None,
                    )
                    await session.commit()
                    await _publish_status(
                        env_uuid,
                        status=EnvironmentStatus.PROVISIONING,
                        commit_sha=short_sha,
                        message="Rebuild started",
                        stage=ExecutionStage.INIT,
                    )

                await _emit_stage(
                    log_repo,
                    session,
                    environment_id=env_uuid,
                    stage=ExecutionStage.INIT,
                    message=f"INIT - GitOps rebuild for commit {short_sha}",
                    commit_sha=short_sha,
                )

                try:
                    await asyncio.sleep(settings.provision_step_delay_seconds)
                    current_stage = ExecutionStage.VALIDATE
                    await _emit_stage(
                        log_repo,
                        session,
                        environment_id=env_uuid,
                        stage=ExecutionStage.VALIDATE,
                        message=f"VALIDATE - pulling application revision {short_sha}",
                        commit_sha=short_sha,
                    )

                    await asyncio.sleep(settings.provision_step_delay_seconds)
                    current_stage = ExecutionStage.PLAN
                    await _emit_stage(
                        log_repo,
                        session,
                        environment_id=env_uuid,
                        stage=ExecutionStage.PLAN,
                        message=(
                            f"PLAN - workload update in {environment.namespace_name} "
                            f"(kubectl set image / Deployment roll)"
                        ),
                        commit_sha=short_sha,
                    )

                    rebuild_image: str | None = None
                    built_image, built_sha = await _maybe_build_preview_image(
                        log_repo,
                        settings=settings,
                        environment=environment,
                        env_uuid=env_uuid,
                        commit_sha=short_sha,
                        force=True,
                    )
                    if built_image:
                        rebuild_image = built_image
                        short_sha = built_sha or short_sha

                    current_stage = ExecutionStage.APPLY
                    deploy_mode = await _effective_deploy_mode(session, environment)
                    await session.commit()
                    workspace_root: Path | None = None
                    rebuild_wizard_snapshot: dict | None = None
                    if workspace_id is not None:
                        from app.services.provisioning import ProvisioningService

                        workspace_row = await session.get(ProvisioningWorkspace, workspace_id)
                        if workspace_row is not None and workspace_row.root_dir:
                            workspace_root = Path(workspace_row.root_dir)
                            rebuild_wizard_snapshot = ProvisioningService(session)._load_wizard_snapshot(
                                workspace_row
                            )

                    if deploy_mode == DeployMode.COMPOSE.value:
                        from app.services.compose_deploy import ComposeDeployError, deploy_compose

                        if workspace_root is None:
                            raise RuntimeError(
                                "Compose rebuild requires a linked workspace with docker-compose.yml"
                            )
                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message="APPLY - docker compose up --build (rebuild)",
                            commit_sha=short_sha,
                        )
                        try:
                            resources = await asyncio.to_thread(
                                deploy_compose,
                                workspace_root=workspace_root,
                                namespace=environment.namespace_name,
                                environment_id=str(environment.id),
                                name=environment.name,
                                image=rebuild_image,
                                settings=settings,
                                primary_host_preference=_compose_primary_host_preference(
                                    rebuild_wizard_snapshot
                                ),
                            )
                        except ComposeDeployError as exc:
                            raise RuntimeError(str(exc)) from exc
                        if resources.preview_url or resources.node_port:
                            await _attach_docker_host_preview_ingress(
                                env_uuid, environment, resources, provisioner
                            )
                            await env_repo.update_status(
                                environment,
                                EnvironmentStatus.PROVISIONING,
                                preview_url=resources.preview_url,
                                node_port=resources.node_port,
                                workload_image=resources.image or rebuild_image,
                            )
                    elif deploy_mode == DeployMode.ATTACH.value:
                        from app.schemas.cloud import (
                            KubernetesPackaging,
                            RunningInstanceConfig,
                            WorkspaceWizardConfig,
                        )
                        from app.services.attach_deploy import AttachDeployError, deploy_attach, prepare_attach_deploy
                        from app.services.provisioning import ProvisioningService

                        running_instance = RunningInstanceConfig()
                        attach_services = None
                        attach_runtime_mode = None
                        packaging: KubernetesPackaging | None = None
                        attach_credentials = None
                        workspace_encrypted: str | None = None
                        workspace_provider: str | None = None
                        wizard_cloud_provider: str | None = None
                        create_vpc = False
                        create_subnets = False
                        existing_vpc_id: str | None = None
                        existing_security_group_id: str | None = None
                        gcp_project_id: str | None = None
                        attach_org_slug = await _attach_org_slug(session, environment)
                        if workspace_id is not None:
                            provisioning = ProvisioningService(session)
                            workspace_row = await session.get(
                                ProvisioningWorkspace, workspace_id
                            )
                            if workspace_row is not None:
                                workspace_encrypted = workspace_row.encrypted_credentials
                                workspace_provider = workspace_row.provider
                                packaging = provisioning.get_workspace_kubernetes_packaging(
                                    workspace_row
                                )
                                snapshot = provisioning._load_wizard_snapshot(workspace_row)
                                wizard_cloud_provider = _wizard_cloud_provider(snapshot)
                                create_vpc, create_subnets = _wizard_network_flags(snapshot)
                                existing_vpc_id = _wizard_existing_vpc_id(snapshot)
                                existing_security_group_id = _wizard_existing_security_group_id(snapshot)
                                gcp_project_id = _wizard_gcp_project_id(snapshot)
                                if snapshot is not None:
                                    try:
                                        wizard = WorkspaceWizardConfig.model_validate(
                                            {**snapshot, "has_credentials": False}
                                        )
                                        running_instance = wizard.running_instance
                                        attach_services = list(
                                            wizard.container_scaffold.services or []
                                        )
                                        attach_runtime_mode = wizard.runtime_mode
                                    except Exception:
                                        pass
                        running_instance, attach_credentials, attach_provider = prepare_attach_deploy(
                            running_instance=running_instance,
                            cloud_provider=getattr(environment, "provider", None),
                            environment_name=environment.name,
                            encrypted_credentials=workspace_encrypted,
                            runtime_mode=attach_runtime_mode,
                            workspace_provider=workspace_provider,
                            wizard_cloud_provider=wizard_cloud_provider,
                        )
                        attach_credentials = await _merge_attach_credentials_from_vault(
                            session,
                            attach_credentials=attach_credentials,
                            owner_id=environment.owner_id,
                            provider=attach_provider,
                        )

                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message=(
                                "APPLY - redeploying running instance "
                                f"({running_instance.kind.value}/{attach_provider})"
                            ),
                            commit_sha=short_sha,
                        )
                        try:
                            rebuild_resources = await asyncio.to_thread(
                                deploy_attach,
                                namespace=environment.namespace_name,
                                environment_id=str(environment.id),
                                name=environment.name,
                                git_branch=environment.git_branch,
                                git_repo_url=environment.git_repo_url,
                                ttl_expires_at=environment.ttl_expires_at.isoformat(),
                                owner_label=owner_label,
                                image=rebuild_image,
                                enable_postgres=getattr(
                                    environment, "enable_postgres", False
                                ),
                                enable_redis=getattr(environment, "enable_redis", False),
                                running_instance=running_instance,
                                workspace_root=workspace_root,
                                packaging=packaging,
                                settings=settings,
                                services=attach_services,
                                cloud_provider=attach_provider,
                                credentials=attach_credentials,
                                org_slug=attach_org_slug,
                                workspace_provider=workspace_provider,
                                wizard_cloud_provider=wizard_cloud_provider,
                                create_vpc=create_vpc,
                                create_subnets=create_subnets,
                                gcp_project_id=gcp_project_id,
                                existing_vpc_id=existing_vpc_id,
                                existing_security_group_id=existing_security_group_id,
                            )
                            from app.schemas.environment import dump_preview_endpoints

                            await _attach_docker_host_preview_ingress(
                                env_uuid, environment, rebuild_resources, provisioner
                            )
                            rebuild_endpoints = list(
                                getattr(rebuild_resources, "preview_endpoints", None) or []
                            )
                            await env_repo.update_status(
                                environment,
                                EnvironmentStatus.PROVISIONING,
                                preview_url=rebuild_resources.preview_url,
                                node_port=rebuild_resources.node_port,
                                workload_image=rebuild_resources.image or rebuild_image,
                                preview_endpoints_json=(
                                    dump_preview_endpoints(rebuild_endpoints)
                                    if rebuild_endpoints
                                    else None
                                ),
                            )
                            await _sync_workspace_after_cloud_deploy(
                                session,
                                workspace_id=workspace_id,
                                resources=rebuild_resources,
                                cloud_provider=attach_provider,
                            )
                        except AttachDeployError as exc:
                            raise RuntimeError(str(exc)) from exc
                    else:
                        provisioner = await _retarget_provisioner_for_cloud_k8s(
                            session,
                            environment=environment,
                            provisioner=provisioner,
                            deploy_mode=deploy_mode,
                            create_cluster=False,
                            log_repo=log_repo,
                            env_uuid=env_uuid,
                            commit_sha=short_sha,
                        )
                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message="APPLY - rolling Deployment",
                            commit_sha=short_sha,
                        )
                        provisioner.rebuild_workload(
                            namespace=environment.namespace_name,
                            environment_id=str(environment.id),
                            name=environment.name,
                            git_branch=environment.git_branch,
                            git_repo_url=environment.git_repo_url,
                            commit_sha=short_sha,
                            owner_label=owner_label,
                            image=rebuild_image,
                            enable_postgres=getattr(environment, "enable_postgres", False),
                            enable_redis=getattr(environment, "enable_redis", False),
                        )
                    # New revision rolled out and passed readiness; from here a
                    # failure must not roll the healthy workload back.
                    new_rollout_ok = True

                    await asyncio.sleep(settings.provision_step_delay_seconds)
                    await _emit_stage(
                        log_repo,
                        session,
                        environment_id=env_uuid,
                        stage=ExecutionStage.APPLY,
                        message="APPLY - waiting for rollout to complete",
                        commit_sha=short_sha,
                    )

                    await asyncio.sleep(settings.provision_step_delay_seconds)
                    await env_repo.update_status(
                        environment,
                        EnvironmentStatus.RUNNING,
                        latest_commit_sha=short_sha,
                        error_message=None,
                        workload_image=rebuild_image or environment.workload_image,
                    )
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message=f"APPLY - rebuild completed, RUNNING at {short_sha}",
                        status=EnvironmentStatus.RUNNING.value,
                        commit_sha=short_sha,
                        stage=ExecutionStage.APPLY,
                    )
                    await _record_audit(
                        session,
                        action=AuditAction.REBUILD_SUCCEEDED,
                        actor_id=actor_id,
                        status=AuditStatus.SUCCESS,
                        environment_id=env_uuid,
                        workspace_id=workspace_id,
                        commit_sha=short_sha,
                    )
                    await session.commit()
                    await _publish_status(
                        env_uuid,
                        status=EnvironmentStatus.RUNNING,
                        commit_sha=short_sha,
                        message="Rebuild succeeded",
                        stage=ExecutionStage.APPLY,
                        preview_url=environment.preview_url,
                        node_port=environment.node_port,
                        app_ready=bool(environment.preview_url),
                    )
                    await _notify_integrations(
                        session,
                        env_uuid,
                        event="ready",
                        message="Rebuild succeeded",
                        correlation_id=correlation_id,
                    )
                    await session.commit()
                except Exception as exc:
                    logger.exception("rebuild_failed", environment_id=environment_id)
                    await session.rollback()
                    error_text = sanitize_log_message(str(exc))
                    # Prefer restoring the last working revision over leaving the
                    # preview broken. Fall back to FAILED only when there is no
                    # known-good state or the rollback itself fails.
                    rolled_back = await _attempt_rebuild_rollback(
                        session_factory,
                        provisioner,
                        env_uuid=env_uuid,
                        eligible=rollback_eligible and not new_rollout_ok,
                        previous_image=previous_image,
                        previous_commit=previous_commit,
                        failed_commit=short_sha,
                        stage=current_stage,
                        actor_id=actor_id,
                        workspace_id=workspace_id,
                        error_text=error_text,
                    )
                    if not rolled_back:
                        await _fail_execution(
                            session_factory,
                            environment_id=env_uuid,
                            error_text=f"Rebuild failed: {error_text}",
                            commit_sha=short_sha,
                            stage=current_stage,
                            audit_action=AuditAction.REBUILD_FAILED,
                            actor_id=actor_id,
                            workspace_id=workspace_id,
                        )
    except StateLockConflict:
        logger.warning(
            "rebuild_skipped_lock_held",
            environment_id=environment_id,
            message=PROVISIONING_IN_PROGRESS_MESSAGE,
        )
        async with session_factory() as session:
            log_repo = DeploymentLogRepository(session)
            await _emit_log(
                log_repo,
                environment_id=env_uuid,
                message=PROVISIONING_IN_PROGRESS_MESSAGE,
                log_level=LogLevel.WARN,
                commit_sha=short_sha,
                stage=ExecutionStage.INIT,
            )
            await _record_audit(
                session,
                action=AuditAction.REBUILD_INITIATED,
                actor_id=actor_id,
                status=AuditStatus.REJECTED,
                environment_id=env_uuid,
                workspace_id=workspace_id,
                commit_sha=short_sha,
                detail=PROVISIONING_IN_PROGRESS_MESSAGE,
            )
            await session.commit()


async def _run_teardown(environment_id: str, correlation_id: str) -> None:
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    settings = get_settings()
    session_factory = _session_factory()
    provisioner = KubernetesProvisioner(settings)
    env_uuid = UUID(environment_id)
    actor_id = f"system:celery:{correlation_id}"
    workspace_id: UUID | None = None

    try:
        async with acquire_state_lock(env_uuid, scope="environment", settings=settings):
            async with session_factory() as session:
                env_repo = EnvironmentRepository(session)
                log_repo = DeploymentLogRepository(session)
                environment = await env_repo.get_by_id(env_uuid)
                if environment is None:
                    return

                actor_id = str(environment.owner_id)
                workspace_id = environment.workspace_id
                current_stage = ExecutionStage.INIT
                # Snapshot scalars before any commit/rollback. After rollback SQLAlchemy
                # expires the instance and lazy refresh would raise MissingGreenlet.
                commit_sha = environment.latest_commit_sha
                namespace_name = environment.namespace_name
                workload_image = environment.workload_image
                env_provider = environment.provider
                env_name = environment.name

                if environment.status != EnvironmentStatus.TEARDOWN_PENDING:
                    await env_repo.update_status(
                        environment, EnvironmentStatus.TEARDOWN_PENDING
                    )
                    await session.commit()
                    await _publish_status(
                        env_uuid,
                        status=EnvironmentStatus.TEARDOWN_PENDING,
                        commit_sha=commit_sha,
                        message="Teardown started",
                        stage=ExecutionStage.INIT,
                    )

                await _emit_stage(
                    log_repo,
                    session,
                    environment_id=env_uuid,
                    stage=ExecutionStage.INIT,
                    message=f"INIT - tearing down namespace {namespace_name}",
                    commit_sha=commit_sha,
                    status=EnvironmentStatus.TEARDOWN_PENDING.value,
                )

                try:
                    current_stage = ExecutionStage.APPLY
                    await _emit_stage(
                        log_repo,
                        session,
                        environment_id=env_uuid,
                        stage=ExecutionStage.APPLY,
                        message="APPLY - deleting namespace and resources",
                        commit_sha=commit_sha,
                        status=EnvironmentStatus.TEARDOWN_PENDING.value,
                    )

                    # Best-effort cluster / compose cleanup: honor the destroy request even if
                    # the runtime is unreachable. A cleanup failure must NOT leave the
                    # environment stuck - we still mark it DESTROYED below.
                    deploy_mode = await _effective_deploy_mode(session, environment)
                    await session.commit()
                    workspace_root: Path | None = None
                    if workspace_id is not None:
                        workspace_row = await session.get(
                            ProvisioningWorkspace, workspace_id
                        )
                        if workspace_row is not None and workspace_row.root_dir:
                            workspace_root = Path(workspace_row.root_dir)
                    try:
                        if deploy_mode == DeployMode.COMPOSE.value:
                            from app.services.compose_deploy import teardown_compose

                            await asyncio.to_thread(
                                teardown_compose,
                                workspace_root=workspace_root,
                                namespace=namespace_name,
                                environment_id=str(env_uuid),
                            )
                            # Drop the ws-* Ingress bridge namespace (Docker host backend).
                            try:
                                await asyncio.wait_for(
                                    asyncio.to_thread(
                                        provisioner.teardown, namespace_name
                                    ),
                                    timeout=30.0,
                                )
                            except Exception as bridge_exc:
                                logger.warning(
                                    "compose_preview_ingress_teardown_failed",
                                    environment_id=environment_id,
                                    error=str(bridge_exc),
                                )
                        elif deploy_mode == DeployMode.ATTACH.value:
                            from app.schemas.cloud import (
                                RunningInstanceConfig,
                                WorkspaceRuntimeMode,
                                WorkspaceWizardConfig,
                            )
                            from app.services.attach_deploy import teardown_attach, prepare_attach_deploy
                            from app.services.provisioning import ProvisioningService
                            from app.services.teardown_context import (
                                owner_id_from_context,
                                parse_teardown_context,
                            )

                            running_instance = RunningInstanceConfig()
                            workspace_encrypted: str | None = None
                            attach_runtime_mode = None
                            workspace_provider: str | None = None
                            wizard_cloud_provider: str | None = None
                            attach_org_slug = await _attach_org_slug(session, environment)

                            ctx = parse_teardown_context(
                                getattr(environment, "teardown_context_json", None)
                            )
                            if ctx:
                                workspace_encrypted = ctx.get("encrypted_credentials")
                                workspace_provider = ctx.get("workspace_provider")
                                wizard_cloud_provider = ctx.get("wizard_cloud_provider")
                                runtime_raw = ctx.get("runtime_mode")
                                if runtime_raw:
                                    try:
                                        attach_runtime_mode = WorkspaceRuntimeMode(str(runtime_raw))
                                    except ValueError:
                                        attach_runtime_mode = None
                                ri = ctx.get("running_instance")
                                if isinstance(ri, dict):
                                    try:
                                        running_instance = RunningInstanceConfig.model_validate(ri)
                                    except Exception:
                                        pass

                            if workspace_id is not None and workspace_encrypted is None:
                                provisioning = ProvisioningService(session)
                                workspace_row = await session.get(
                                    ProvisioningWorkspace, workspace_id
                                )
                                if workspace_row is not None:
                                    workspace_encrypted = workspace_row.encrypted_credentials
                                    workspace_provider = workspace_provider or workspace_row.provider
                                    snapshot = provisioning._load_wizard_snapshot(workspace_row)
                                    wizard_cloud_provider = (
                                        wizard_cloud_provider or _wizard_cloud_provider(snapshot)
                                    )
                                    if snapshot is not None and not ctx:
                                        try:
                                            wizard = WorkspaceWizardConfig.model_validate(
                                                {**snapshot, "has_credentials": False}
                                            )
                                            running_instance = wizard.running_instance
                                            attach_runtime_mode = wizard.runtime_mode
                                        except Exception:
                                            pass

                            running_instance, attach_credentials, attach_provider = prepare_attach_deploy(
                                running_instance=running_instance,
                                cloud_provider=env_provider,
                                environment_name=env_name,
                                encrypted_credentials=workspace_encrypted,
                                runtime_mode=attach_runtime_mode,
                                workspace_provider=workspace_provider,
                                wizard_cloud_provider=wizard_cloud_provider,
                            )

                            owner_for_vault = owner_id_from_context(ctx) or getattr(
                                environment, "owner_id", None
                            )
                            attach_credentials = await _merge_attach_credentials_from_vault(
                                session,
                                attach_credentials=attach_credentials,
                                owner_id=owner_for_vault,
                                provider=attach_provider,
                            )

                            logger.info(
                                "attach_teardown_resolved",
                                environment_id=environment_id,
                                provider=attach_provider,
                                kind=getattr(running_instance.kind, "value", running_instance.kind),
                                has_credentials=bool(attach_credentials),
                                from_teardown_context=bool(ctx),
                            )

                            await asyncio.to_thread(
                                teardown_attach,
                                running_instance=running_instance,
                                namespace=namespace_name,
                                environment_id=str(env_uuid),
                                environment_name=env_name,
                                settings=settings,
                                cloud_provider=attach_provider,
                                credentials=attach_credentials,
                                org_slug=attach_org_slug,
                                workspace_provider=workspace_provider,
                                wizard_cloud_provider=wizard_cloud_provider,
                            )
                            try:
                                await asyncio.wait_for(
                                    asyncio.to_thread(
                                        provisioner.teardown, namespace_name
                                    ),
                                    timeout=30.0,
                                )
                            except Exception as bridge_exc:
                                logger.warning(
                                    "attach_preview_ingress_teardown_failed",
                                    environment_id=environment_id,
                                    error=str(bridge_exc),
                                )
                        else:
                            provisioner = await _retarget_provisioner_for_cloud_k8s(
                                session,
                                environment=environment,
                                provisioner=provisioner,
                                deploy_mode=deploy_mode,
                                create_cluster=False,
                            )
                            await asyncio.wait_for(
                                asyncio.to_thread(
                                    provisioner.teardown, namespace_name
                                ),
                                timeout=30.0,
                            )
                    except Exception as cleanup_exc:
                        logger.warning(
                            "teardown_namespace_cleanup_failed",
                            environment_id=environment_id,
                            namespace=namespace_name,
                            deploy_mode=deploy_mode,
                            error=str(cleanup_exc),
                        )
                        await _emit_log(
                            log_repo,
                            environment_id=env_uuid,
                            message=(
                                "APPLY - runtime cleanup could not complete "
                                f"({sanitize_log_message(str(cleanup_exc))}); marking DESTROYED anyway"
                            ),
                            log_level=LogLevel.WARN,
                            status=EnvironmentStatus.TEARDOWN_PENDING.value,
                            commit_sha=commit_sha,
                            stage=ExecutionStage.APPLY,
                        )
                    # Stop the per-preview cloudflared tunnel (if any) so we don't
                    # leak a cloudflared process for a destroyed environment.
                    try:
                        from app.services.preview_tunnel import stop_preview_tunnel

                        await asyncio.to_thread(stop_preview_tunnel, str(env_uuid))
                    except Exception:
                        logger.exception(
                            "preview_tunnel_stop_failed", environment_id=str(env_uuid)
                        )
                    # Reclaim cloud registry images (Artifact Registry / ECR).
                    try:
                        from app.core.secrets import decrypt_secret
                        from app.schemas.cloud import (
                            CloudCredentials,
                            RunningInstanceConfig,
                            WorkspaceWizardConfig,
                        )
                        from app.services.cloud_instance_compute import (
                            is_cloud_registry_image,
                            teardown_cloud_registry_images,
                        )
                        from app.services.provisioning import ProvisioningService
                        from app.services.teardown_context import parse_teardown_context

                        provider = (env_provider or "local").strip().lower()
                        if provider != "local":
                            teardown_creds: CloudCredentials | None = None
                            teardown_region = "us-central1"
                            ctx = parse_teardown_context(
                                getattr(environment, "teardown_context_json", None)
                            )
                            if ctx:
                                enc = ctx.get("encrypted_credentials")
                                if enc:
                                    try:
                                        teardown_creds = CloudCredentials.model_validate_json(
                                            decrypt_secret(str(enc))
                                        )
                                    except Exception:
                                        pass
                                ri = ctx.get("running_instance")
                                if isinstance(ri, dict):
                                    try:
                                        ri_cfg = RunningInstanceConfig.model_validate(ri)
                                        if ri_cfg.region:
                                            teardown_region = ri_cfg.region
                                    except Exception:
                                        pass
                            if workspace_id is not None and teardown_creds is None:
                                ws_row = await session.get(
                                    ProvisioningWorkspace, workspace_id
                                )
                                if ws_row is not None and ws_row.encrypted_credentials:
                                    try:
                                        teardown_creds = CloudCredentials.model_validate_json(
                                            decrypt_secret(ws_row.encrypted_credentials)
                                        )
                                    except Exception:
                                        pass
                                if ws_row is not None:
                                    snap = ProvisioningService(session)._load_wizard_snapshot(
                                        ws_row
                                    )
                                    if snap:
                                        try:
                                            wiz = WorkspaceWizardConfig.model_validate(
                                                {**snap, "has_credentials": False}
                                            )
                                            if wiz.running_instance.region:
                                                teardown_region = wiz.running_instance.region
                                        except Exception:
                                            pass
                            cloud_images = [
                                img
                                for img in ([workload_image] if workload_image else [])
                                if is_cloud_registry_image(img)
                            ]
                            if teardown_creds and (cloud_images or str(env_uuid)):
                                await asyncio.to_thread(
                                    teardown_cloud_registry_images,
                                    cloud_images,
                                    cloud_provider=provider,
                                    credentials=teardown_creds,
                                    region=teardown_region,
                                    environment_id=str(env_uuid),
                                )
                    except Exception:
                        logger.exception(
                            "teardown_cloud_registry_cleanup_failed",
                            environment_id=environment_id,
                        )
                    # Reclaim locally-built app images from kind/k3d + host Docker
                    # so deleted previews do not leak disk (leave shared base images).
                    try:
                        from app.services.image_cleanup import (
                            collect_preview_environment_images,
                            remove_local_docker_images,
                            resolve_local_cluster_short_name,
                        )

                        # Env teardown only reclaims env-scoped preview tags.
                        # Shared workspace images (launch-web:latest, etc.) stay
                        # until the workspace itself is destroyed.
                        preview_images = collect_preview_environment_images(
                            settings=settings,
                            environment_id=str(env_uuid),
                            workload_image=workload_image,
                            commit_sha=commit_sha,
                        )
                        images = list(dict.fromkeys(preview_images))
                        if images:
                            from app.services.cloud_kubernetes import is_cloud_kubernetes_provider

                            is_local = not is_cloud_kubernetes_provider(env_provider)
                            remove_local_docker_images(
                                images,
                                cluster_name=resolve_local_cluster_short_name(settings),
                                settings=settings,
                                remove_from_cluster=is_local,
                            )
                    except Exception:
                        logger.exception(
                            "teardown_image_cleanup_failed", environment_id=environment_id
                        )
                    await asyncio.sleep(settings.provision_step_delay_seconds)
                    environment.teardown_context_json = None
                    await env_repo.update_status(environment, EnvironmentStatus.DESTROYED)
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message="APPLY - teardown completed, DESTROYED",
                        status=EnvironmentStatus.DESTROYED.value,
                        commit_sha=commit_sha,
                        stage=ExecutionStage.APPLY,
                    )
                    await _record_audit(
                        session,
                        action=AuditAction.TEARDOWN_SUCCEEDED,
                        actor_id=actor_id,
                        status=AuditStatus.SUCCESS,
                        environment_id=env_uuid,
                        workspace_id=workspace_id,
                        commit_sha=commit_sha,
                    )
                    await session.commit()
                    await _publish_status(
                        env_uuid,
                        status=EnvironmentStatus.DESTROYED,
                        commit_sha=commit_sha,
                        message="Teardown completed",
                        stage=ExecutionStage.APPLY,
                    )
                except Exception as exc:
                    logger.exception("teardown_failed", environment_id=environment_id)
                    await session.rollback()
                    error_text = sanitize_log_message(str(exc))
                    await _fail_execution(
                        session_factory,
                        environment_id=env_uuid,
                        error_text=f"Teardown failed: {error_text}",
                        commit_sha=commit_sha,
                        stage=current_stage,
                        audit_action=AuditAction.TEARDOWN_FAILED,
                        actor_id=actor_id,
                        workspace_id=workspace_id,
                    )
    except StateLockConflict:
        logger.warning(
            "teardown_skipped_lock_held",
            environment_id=environment_id,
            message=PROVISIONING_IN_PROGRESS_MESSAGE,
        )
        # A user-requested destroy must not wait out the full provision lock TTL
        # (up to state_lock_timeout_seconds). Give an in-flight provision a grace
        # window to notice the cancel and release cooperatively; past that, the
        # holder is stuck or dead (e.g. worker restart left a stale key), so break
        # the lock and let the retry proceed. Destroy is terminal and authoritative.
        force_after = settings.teardown_state_lock_timeout_seconds
        should_force = False
        async with session_factory() as session:
            env_row = await EnvironmentRepository(session).get_by_id(env_uuid)
            if env_row is not None and env_row.status == EnvironmentStatus.TEARDOWN_PENDING:
                pending_since = env_row.updated_at
                if pending_since.tzinfo is None:
                    pending_since = pending_since.replace(tzinfo=UTC)
                pending_age = (datetime.now(UTC) - pending_since).total_seconds()
                should_force = pending_age >= force_after
            log_repo = DeploymentLogRepository(session)
            await _emit_log(
                log_repo,
                environment_id=env_uuid,
                message=(
                    "Destroy is overriding a stale provisioning lock and continuing."
                    if should_force
                    else PROVISIONING_IN_PROGRESS_MESSAGE
                ),
                log_level=LogLevel.WARN,
                stage=ExecutionStage.INIT,
            )
            await _record_audit(
                session,
                action=AuditAction.TEARDOWN_INITIATED,
                actor_id=actor_id,
                status=AuditStatus.REJECTED,
                environment_id=env_uuid,
                workspace_id=workspace_id,
                commit_sha=None,
                detail=PROVISIONING_IN_PROGRESS_MESSAGE,
            )
            await session.commit()

        if should_force:
            logger.warning(
                "teardown_forcing_stale_lock",
                environment_id=environment_id,
                pending_seconds=round(pending_age, 1),
            )
            try:
                await force_release_state_lock(
                    env_uuid, scope="environment", settings=settings
                )
            except Exception:
                logger.exception(
                    "teardown_force_release_failed", environment_id=environment_id
                )

        # Retry immediately after breaking a stale lock; otherwise leave a short
        # grace for a live provision to cancel and release on its own.
        try:
            teardown_environment_task.apply_async(
                kwargs={
                    "environment_id": str(env_uuid),
                    "correlation_id": f"teardown-retry:{env_uuid}",
                },
                countdown=1 if should_force else 15,
            )
        except Exception:
            logger.exception(
                "teardown_retry_enqueue_failed",
                environment_id=environment_id,
            )


async def _reclaim_environment_runtime(
    session: AsyncSession,
    environment: Environment,
    *,
    settings,
    provisioner: "KubernetesProvisioner | None" = None,
) -> str:
    """Stop runtime by deploy mode and remove local containers/images. Best-effort.

    Used for TTL expiry and to share image reclaim logic with destroy. Never raises.
    """
    notes: list[str] = []
    deploy_mode = DeployMode.PREVIEW.value
    try:
        deploy_mode = await _effective_deploy_mode(session, environment)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "reclaim_deploy_mode_failed",
            environment_id=str(environment.id),
            error=str(exc),
        )
        deploy_mode = normalize_deploy_mode(getattr(environment, "deploy_mode", None))

    workspace_root: Path | None = None
    if environment.workspace_id is not None:
        try:
            workspace_row = await session.get(ProvisioningWorkspace, environment.workspace_id)
            if workspace_row is not None and workspace_row.root_dir:
                workspace_root = Path(workspace_row.root_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reclaim_workspace_lookup_failed",
                environment_id=str(environment.id),
                error=str(exc),
            )

    try:
        if deploy_mode == DeployMode.COMPOSE.value:
            from app.services.compose_deploy import teardown_compose

            await asyncio.to_thread(
                teardown_compose,
                workspace_root=workspace_root,
                namespace=environment.namespace_name,
                environment_id=str(environment.id),
            )
            notes.append("compose down")
            if provisioner is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(provisioner.teardown, environment.namespace_name),
                        timeout=30.0,
                    )
                    notes.append("ingress namespace removed")
                except Exception as bridge_exc:  # noqa: BLE001
                    logger.warning(
                        "reclaim_compose_ingress_failed",
                        environment_id=str(environment.id),
                        error=str(bridge_exc),
                    )
        elif deploy_mode == DeployMode.ATTACH.value:
            from app.schemas.cloud import (
                RunningInstanceConfig,
                WorkspaceRuntimeMode,
                WorkspaceWizardConfig,
            )
            from app.services.attach_deploy import teardown_attach, prepare_attach_deploy
            from app.services.provisioning import ProvisioningService
            from app.services.teardown_context import (
                owner_id_from_context,
                parse_teardown_context,
            )

            running_instance = RunningInstanceConfig()
            workspace_encrypted: str | None = None
            attach_runtime_mode = None
            workspace_provider: str | None = None
            wizard_cloud_provider: str | None = None
            attach_org_slug = await _attach_org_slug(session, environment)
            ctx = parse_teardown_context(getattr(environment, "teardown_context_json", None))
            if ctx:
                workspace_encrypted = ctx.get("encrypted_credentials")
                workspace_provider = ctx.get("workspace_provider")
                wizard_cloud_provider = ctx.get("wizard_cloud_provider")
                runtime_raw = ctx.get("runtime_mode")
                if runtime_raw:
                    try:
                        attach_runtime_mode = WorkspaceRuntimeMode(str(runtime_raw))
                    except ValueError:
                        attach_runtime_mode = None
                ri = ctx.get("running_instance")
                if isinstance(ri, dict):
                    try:
                        running_instance = RunningInstanceConfig.model_validate(ri)
                    except Exception:
                        pass
            if environment.workspace_id is not None and workspace_encrypted is None:
                provisioning = ProvisioningService(session)
                workspace_row = await session.get(
                    ProvisioningWorkspace, environment.workspace_id
                )
                if workspace_row is not None:
                    workspace_encrypted = workspace_row.encrypted_credentials
                    workspace_provider = workspace_provider or workspace_row.provider
                    snapshot = provisioning._load_wizard_snapshot(workspace_row)
                    wizard_cloud_provider = wizard_cloud_provider or _wizard_cloud_provider(
                        snapshot
                    )
                    if snapshot is not None and not ctx:
                        try:
                            wizard = WorkspaceWizardConfig.model_validate(
                                {**snapshot, "has_credentials": False}
                            )
                            running_instance = wizard.running_instance
                            attach_runtime_mode = wizard.runtime_mode
                        except Exception:
                            pass
            running_instance, attach_credentials, attach_provider = prepare_attach_deploy(
                running_instance=running_instance,
                cloud_provider=getattr(environment, "provider", None),
                environment_name=environment.name,
                encrypted_credentials=workspace_encrypted,
                runtime_mode=attach_runtime_mode,
                workspace_provider=workspace_provider,
                wizard_cloud_provider=wizard_cloud_provider,
            )
            owner_for_vault = owner_id_from_context(ctx) or getattr(
                environment, "owner_id", None
            )
            attach_credentials = await _merge_attach_credentials_from_vault(
                session,
                attach_credentials=attach_credentials,
                owner_id=owner_for_vault,
                provider=attach_provider,
            )
            await asyncio.to_thread(
                teardown_attach,
                running_instance=running_instance,
                namespace=environment.namespace_name,
                environment_id=str(environment.id),
                environment_name=environment.name,
                settings=settings,
                cloud_provider=attach_provider,
                credentials=attach_credentials,
                org_slug=attach_org_slug,
                workspace_provider=workspace_provider,
                wizard_cloud_provider=wizard_cloud_provider,
            )
            notes.append("attach instance stopped")
            if provisioner is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(provisioner.teardown, environment.namespace_name),
                        timeout=30.0,
                    )
                    notes.append("ingress namespace removed")
                except Exception as bridge_exc:  # noqa: BLE001
                    logger.warning(
                        "reclaim_attach_ingress_failed",
                        environment_id=str(environment.id),
                        error=str(bridge_exc),
                    )
        else:
            k8s = provisioner or KubernetesProvisioner(settings=settings)
            k8s = await _retarget_provisioner_for_cloud_k8s(
                session,
                environment=environment,
                provisioner=k8s,
                deploy_mode=deploy_mode,
                create_cluster=False,
            )
            await asyncio.wait_for(
                asyncio.to_thread(k8s.teardown, environment.namespace_name),
                timeout=45.0,
            )
            notes.append("kubernetes namespace removed")
    except Exception as exc:  # noqa: BLE001 - still reclaim images below
        logger.warning(
            "reclaim_runtime_stop_failed",
            environment_id=str(environment.id),
            deploy_mode=deploy_mode,
            error=str(exc),
        )
        notes.append(f"runtime stop incomplete ({exc})")

    try:
        from app.services.preview_tunnel import stop_preview_tunnel

        await asyncio.to_thread(stop_preview_tunnel, str(environment.id))
    except Exception:
        logger.exception("reclaim_tunnel_stop_failed", environment_id=str(environment.id))

    try:
        from app.services.image_cleanup import (
            collect_preview_environment_images,
            remove_local_docker_images,
            resolve_local_cluster_short_name,
        )

        preview_images = collect_preview_environment_images(
            settings=settings,
            environment_id=str(environment.id),
            workload_image=environment.workload_image,
            commit_sha=environment.latest_commit_sha,
        )
        # Never delete shared workspace tags (e.g. launch-web:latest) on env
        # expire/destroy - those belong to the workspace and must stay loadable
        # for the next provision. Workspace destroy reclaims them separately.
        images = list(dict.fromkeys(preview_images))
        if images:
            from app.services.cloud_kubernetes import is_cloud_kubernetes_provider

            is_local = not is_cloud_kubernetes_provider(environment.provider)
            removed = remove_local_docker_images(
                images,
                cluster_name=resolve_local_cluster_short_name(settings),
                settings=settings,
                remove_from_cluster=is_local,
            )
            if removed:
                notes.append(f"removed {len(removed)} image(s)")
            else:
                notes.append("image reclaim attempted")
    except Exception:
        logger.exception(
            "reclaim_image_cleanup_failed",
            environment_id=str(environment.id),
        )
        notes.append("image reclaim failed")

    return "; ".join(notes) if notes else "cleanup attempted"


async def pause_expired_environment(
    session,
    environment,
    *,
    actor_id: str = "system:ttl-reaper",
    settings=None,
) -> bool:
    """Stop runtime + reclaim images/containers, then mark environment EXPIRED.

    TTL expiry is terminal (cannot resume), so compose/attach/k8s workloads are
    torn down and locally-built images are removed - same reclaim as destroy.
    """
    settings = settings or get_settings()
    if environment.status not in {EnvironmentStatus.RUNNING, EnvironmentStatus.FAILED}:
        return False
    expires = environment.ttl_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires > datetime.now(UTC):
        return False

    env_repo = EnvironmentRepository(session)
    log_repo = DeploymentLogRepository(session)
    provisioner = None
    try:
        provisioner = KubernetesProvisioner(settings=settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ttl_reaper_provisioner_init_failed",
            environment_id=str(environment.id),
            error=str(exc),
        )

    cleanup_note = await _reclaim_environment_runtime(
        session,
        environment,
        settings=settings,
        provisioner=provisioner,
    )

    await env_repo.update_status(environment, EnvironmentStatus.EXPIRED)
    await _emit_log(
        log_repo,
        environment_id=environment.id,
        message=(
            f"TTL expired - environment marked expired ({cleanup_note}) "
            f"(expires_at={environment.ttl_expires_at.isoformat()})"
        ),
        log_level=LogLevel.WARN,
        status=EnvironmentStatus.EXPIRED.value,
        commit_sha=environment.latest_commit_sha,
        stage=ExecutionStage.APPLY,
    )
    await _record_audit(
        session,
        action=AuditAction.PAUSE_SUCCEEDED,
        actor_id=actor_id,
        status=AuditStatus.SUCCESS,
        environment_id=environment.id,
        workspace_id=environment.workspace_id,
        commit_sha=environment.latest_commit_sha,
        detail=f"TTL expired ({cleanup_note})",
    )
    return True


# A preview stuck in PROVISIONING with no live worker (crashed mid-run) is failed
# after this many seconds. Comfortably above the 3-minute readiness cap + overhead
# so a legitimately slow-but-active provision is never falsely failed.
STALE_PROVISIONING_SECONDS = 600
# TEARDOWN_PENDING with no active lock older than this is re-queued (worker/beat
# restarts drop in-flight Celery tasks and leave environments "tearing down").
# Keep comfortably above Celery teardown time_limit, while still reducing the
# long "stuck for hours until worker restart" window.
STALE_TEARDOWN_SECONDS = 120


async def _reap_stale_provisioning(session, env_repo, *, now: datetime) -> list[tuple]:
    """Fail previews stuck in PROVISIONING with no active worker (watchdog).

    Skips rows still holding the provision state lock (a worker is actively
    provisioning them) so only genuinely orphaned rows flip to FAILED.
    """
    from sqlalchemy import select

    cutoff = now - timedelta(seconds=STALE_PROVISIONING_SECONDS)
    stmt = select(Environment).where(
        Environment.status == EnvironmentStatus.PROVISIONING,
        Environment.created_at < cutoff,
    )
    rows = (await session.execute(stmt)).scalars().all()
    failed: list[tuple] = []
    for env in rows:
        if await is_state_locked(env.id, scope="environment"):
            continue  # a worker is actively provisioning this environment
        await env_repo.update_status(
            env,
            EnvironmentStatus.FAILED,
            error_message=(
                "Provisioning timed out with no active worker "
                f"(stuck > {STALE_PROVISIONING_SECONDS}s). The provisioning worker "
                "likely crashed. Delete and relaunch the preview."
            ),
        )
        failed.append((env.id, env.latest_commit_sha))
    return failed


async def _requeue_stale_teardowns(
    session,
    *,
    now: datetime,
    min_age_seconds: int = STALE_TEARDOWN_SECONDS,
) -> int:
    """Re-enqueue TEARDOWN_PENDING rows that lost their worker mid-flight."""
    from sqlalchemy import select

    cutoff = now - timedelta(seconds=max(0, min_age_seconds))
    stmt = select(Environment).where(
        Environment.status == EnvironmentStatus.TEARDOWN_PENDING,
        Environment.updated_at < cutoff,
    )
    rows = list((await session.execute(stmt)).scalars().all())
    requeued = 0
    for env in rows:
        if await is_state_locked(env.id, scope="environment"):
            continue
        correlation_id = f"teardown-requeue:{env.id}:{uuid4().hex[:8]}"
        enqueue_teardown_environment(
            environment_id=str(env.id),
            correlation_id=correlation_id,
        )
        requeued += 1
        logger.info(
            "stale_teardown_requeued",
            environment_id=str(env.id),
            correlation_id=correlation_id,
            min_age_seconds=min_age_seconds,
        )
    return requeued


async def _requeue_pending_teardowns(*, min_age_seconds: int = 0) -> int:
    """Re-enqueue TEARDOWN_PENDING environments (used after worker restart / deploy)."""
    session_factory = _session_factory()
    async with session_factory() as session:
        requeued = await _requeue_stale_teardowns(
            session,
            now=datetime.now(UTC),
            min_age_seconds=min_age_seconds,
        )
        await session.commit()
    return requeued


def requeue_pending_teardowns_sync(*, min_age_seconds: int = 0) -> int:
    """Sync entrypoint for deploy scripts (``python -c`` inside the worker container)."""
    return asyncio.run(_requeue_pending_teardowns(min_age_seconds=min_age_seconds))


async def _run_ttl_reaper() -> int:
    settings = get_settings()
    session_factory = _session_factory()
    reaped = 0

    async with session_factory() as session:
        env_repo = EnvironmentRepository(session)
        now = datetime.now(UTC)
        expired = await env_repo.list_expired_running(now=now)
        expired_events: list[tuple] = []
        for environment in expired:
            commit_sha = environment.latest_commit_sha
            did_pause = await pause_expired_environment(
                session,
                environment,
                actor_id="system:ttl-reaper",
                settings=settings,
            )
            if did_pause:
                reaped += 1
                expired_events.append((environment.id, commit_sha))

        stale_failed = await _reap_stale_provisioning(session, env_repo, now=now)
        stale_teardowns = await _requeue_stale_teardowns(session, now=now)
        await session.commit()

        for environment_id, commit_sha in expired_events:
            await _publish_status(
                environment_id,
                status=EnvironmentStatus.EXPIRED,
                commit_sha=commit_sha,
                message="TTL expired",
                stage=ExecutionStage.APPLY,
            )
            await _notify_integrations(
                session,
                environment_id,
                event="ttl_expired",
                message="TTL expired",
            )
        for environment_id, commit_sha in stale_failed:
            reaped += 1
            await _publish_status(
                environment_id,
                status=EnvironmentStatus.FAILED,
                commit_sha=commit_sha,
                message="Provisioning timed out - no active worker",
                stage=ExecutionStage.APPLY,
                app_ready=False,
                error_message="Provisioning timed out - no active worker",
            )
            await _notify_integrations(
                session,
                environment_id,
                event="failed",
                message="Provisioning timed out - no active worker",
            )
        reaped += stale_teardowns
        await session.commit()

        # Soft TTL warnings for RUNNING previews approaching expiry.
        warning_hours = max(float(settings.ttl_warning_hours), 0.0)
        if warning_hours > 0:
            running = await env_repo.list_running()
            for environment in running:
                expires = environment.ttl_expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=UTC)
                remaining = (expires - now).total_seconds()
                if 0 < remaining <= warning_hours * 3600:
                    await _notify_integrations(
                        session,
                        environment.id,
                        event="ttl_warning",
                        message=f"TTL expires in {int(remaining // 60)} minutes",
                    )
            await session.commit()

    logger.info("ttl_reaper_complete", reaped=reaped, interval=settings.ttl_reaper_interval_seconds)
    return reaped


@celery_app.task(name="launchpad.requeue_pending_teardowns")
def requeue_pending_teardowns_task(min_age_seconds: int = 0) -> int:
    """Re-enqueue TEARDOWN_PENDING after worker restart (deploy / ops)."""
    return requeue_pending_teardowns_sync(min_age_seconds=min_age_seconds)


@celery_app.task(name="launchpad.provision_environment", bind=True, max_retries=0)
def provision_environment_task(self, environment_id: str, correlation_id: str) -> None:
    asyncio.run(_run_provision(environment_id, correlation_id))


@celery_app.task(name="launchpad.rebuild_environment", bind=True, max_retries=0)
def rebuild_environment_task(
    self,
    environment_id: str,
    commit_sha: str,
    correlation_id: str,
) -> None:
    asyncio.run(_run_rebuild(environment_id, commit_sha, correlation_id))


@celery_app.task(
    name="launchpad.teardown_environment",
    bind=True,
    max_retries=0,
    soft_time_limit=600,
    time_limit=720,
)
def teardown_environment_task(self, environment_id: str, correlation_id: str) -> None:
    asyncio.run(_run_teardown(environment_id, correlation_id))


async def _run_drift_scan() -> int:
    settings = get_settings()
    if not settings.drift_scan_enabled or not settings.kubernetes_enabled:
        return 0

    session_factory = _session_factory()
    provisioner = KubernetesProvisioner(settings)
    if not provisioner.clients_ready:
        # kubeconfig/context unreachable at scan time: skip this cycle rather than
        # crash every environment scan. The next beat retries once clients load.
        logger.warning("drift_scan_skipped_no_cluster_client")
        return 0
    recorded = 0
    scanned = 0

    async with session_factory() as session:
        env_repo = EnvironmentRepository(session)
        audit = AuditService(session)
        environments = await env_repo.list_running()
        scanned = len(environments)

        for environment in environments:
            workspace_root = None
            if environment.workspace_id is not None:
                workspace_row = await session.get(
                    ProvisioningWorkspace,
                    environment.workspace_id,
                )
                if workspace_row is not None and workspace_row.root_dir:
                    candidate = Path(workspace_row.root_dir)
                    if candidate.is_dir():
                        workspace_root = candidate
            finding = scan_environment(
                provisioner,
                environment,
                default_image=settings.default_workload_image,
                workspace_root=workspace_root,
            )
            if finding is None:
                continue
            if await record_drift_if_changed(
                audit,
                environment=environment,
                finding=finding,
                actor_id=DRIFT_SCANNER_ACTOR,
            ):
                recorded += 1

        await session.commit()

    logger.info("drift_scan_complete", recorded=recorded, scanned=scanned)
    return recorded


async def _run_cost_metering() -> int:
    """Sample namespace usage and accrue environment cost from the rate card."""
    settings = get_settings()
    if not settings.cost_metering_enabled:
        return 0

    session_factory = _session_factory()
    sampled = 0
    provisioner = None
    if settings.kubernetes_enabled:
        provisioner = KubernetesProvisioner(settings=settings)

    async with session_factory() as session:
        env_repo = EnvironmentRepository(session)
        environments = await env_repo.list_billable_for_cost_metering()
        now = datetime.now(UTC)
        for environment in environments:
            usage = None
            if provisioner is not None:
                try:
                    usage = provisioner.read_namespace_usage(environment.namespace_name)
                except Exception as exc:  # noqa: BLE001 - never fail the whole sweep
                    logger.warning(
                        "cost_metering_usage_failed",
                        environment_id=str(environment.id),
                        error=str(exc),
                    )
            from app.services.cost_metering import accrue_environment_cost

            rate = accrue_environment_cost(
                environment,
                settings=settings,
                usage=usage,
                now=now,
            )
            sampled += 1
            logger.info(
                "cost_metering_sampled",
                environment_id=str(environment.id),
                burn_rate=str(rate),
                accrued=str(environment.cost_accrued),
                source=environment.cost_source,
            )
            is_local = (environment.provider or "local") == "local" or (
                environment.cost_estimate_hourly == 0 and environment.workspace_id is None
            )
            if (
                not is_local
                and environment.cost_accrued >= settings.preview_soft_cost_cap
            ):
                await _notify_integrations(
                    session,
                    environment.id,
                    event="cost_cap",
                    message=(
                        f"Cost accrued ${environment.cost_accrued} meets soft cap "
                        f"${settings.preview_soft_cost_cap}"
                    ),
                )
        await session.commit()

    logger.info("cost_metering_complete", sampled=sampled)
    return sampled


@celery_app.task(name="launchpad.reap_expired_environments")
def reap_expired_environments_task() -> int:
    return asyncio.run(_run_ttl_reaper())


@celery_app.task(name="launchpad.scan_preview_drift")
def scan_preview_drift_task() -> int:
    return asyncio.run(_run_drift_scan())


@celery_app.task(name="launchpad.sample_environment_costs")
def sample_environment_costs_task() -> int:
    return asyncio.run(_run_cost_metering())


def enqueue_provision_environment(*, environment_id: str, correlation_id: str) -> str:
    result = provision_environment_task.delay(environment_id, correlation_id)
    return result.id


def enqueue_rebuild_environment(
    *,
    environment_id: str,
    commit_sha: str,
    correlation_id: str,
) -> str:
    result = rebuild_environment_task.delay(environment_id, commit_sha, correlation_id)
    return result.id


def enqueue_teardown_environment(*, environment_id: str, correlation_id: str) -> str:
    result = teardown_environment_task.delay(environment_id, correlation_id)
    return result.id


@celery_app.task(name="launchpad.build_dockerfile_image", bind=True, max_retries=0)
def build_dockerfile_image_task(self, job_id: str, request_payload: dict) -> None:
    del self
    _run_dockerfile_build(job_id, request_payload)


def enqueue_dockerfile_build(job_id: str, request_payload: dict) -> str:
    result = build_dockerfile_image_task.delay(job_id, request_payload)
    return result.id


def _run_dockerfile_build(job_id: str, request_payload: dict) -> None:
    import tempfile
    from pathlib import Path

    from app.schemas.dockerfile_schema import DockerfileBuildJobStatus, DockerfileBuildRequest
    from app.services.dockerfile_jobs import update_build_job_sync
    from app.services.dockerfile_registry import DockerfileRegistryError, build_and_push_sync
    from app.services.github_app import GitHubAppAuthError, resolve_git_clone_token
    from app.services.preview_build import PreviewBuildError, clone_git_repository

    update_build_job_sync(job_id, status=DockerfileBuildJobStatus.RUNNING, append_logs=["Build started"])

    try:
        request = DockerfileBuildRequest.model_validate(request_payload)
    except Exception as exc:
        update_build_job_sync(
            job_id,
            status=DockerfileBuildJobStatus.FAILED,
            error=f"Invalid build request: {exc}",
        )
        return

    settings = get_settings()
    try:
        token = resolve_git_clone_token(
            settings=settings,
            installation_id=request.installation_id,
            strict_app=True,
        )
    except GitHubAppAuthError as exc:
        update_build_job_sync(
            job_id,
            status=DockerfileBuildJobStatus.FAILED,
            error=f"GitHub auth failed: {exc}",
        )
        return

    repo_url = f"https://github.com/{request.full_name}.git"
    branch = (request.branch or "main").strip()

    try:
        with tempfile.TemporaryDirectory(prefix="launchpad-df-build-") as tmp:
            dest = Path(tmp) / "src"
            dest.mkdir()
            update_build_job_sync(job_id, append_logs=[f"Cloning {request.full_name}@{branch}"])
            try:
                clone_git_repository(
                    repo_url=repo_url,
                    branch=branch,
                    commit_sha=None,
                    token=token,
                    dest=dest,
                )
            except PreviewBuildError as exc:
                update_build_job_sync(
                    job_id,
                    status=DockerfileBuildJobStatus.FAILED,
                    error=str(exc),
                )
                return

            context = dest
            context_rel = (request.context_path or ".").strip().removeprefix("./") or "."
            if context_rel.startswith("/"):
                context_rel = context_rel.lstrip("/") or "."
            if ".." in context_rel.split("/"):
                update_build_job_sync(
                    job_id,
                    status=DockerfileBuildJobStatus.FAILED,
                    error="Invalid build context path",
                )
                return
            if context_rel != ".":
                candidate = dest / context_rel
                if not candidate.is_dir():
                    update_build_job_sync(
                        job_id,
                        status=DockerfileBuildJobStatus.FAILED,
                        error=f"Build context not found: {context_rel}",
                    )
                    return
                context = candidate

            dockerfile_rel = request.dockerfile_path.strip().removeprefix("./")
            if dockerfile_rel.startswith("/"):
                dockerfile_rel = dockerfile_rel.lstrip("/")
            if ".." in dockerfile_rel.split("/"):
                update_build_job_sync(
                    job_id,
                    status=DockerfileBuildJobStatus.FAILED,
                    error="Invalid dockerfile path",
                )
                return
            # If context is a subdirectory, make dockerfile path relative to it when possible.
            if context_rel != "." and dockerfile_rel.startswith(f"{context_rel}/"):
                dockerfile_rel = dockerfile_rel[len(context_rel) + 1 :]

            update_build_job_sync(
                job_id,
                append_logs=[f"Building with Dockerfile={dockerfile_rel}"],
            )
            result = build_and_push_sync(
                context=context,
                dockerfile_relpath=dockerfile_rel,
                registry=request.registry,
                tags=request.tags,
                dockerfile_content_override=request.dockerfile_content_override,
            )
            update_build_job_sync(
                job_id,
                status=DockerfileBuildJobStatus.SUCCEEDED,
                image_refs=result.image_refs,
                logs=result.logs,
            )
    except DockerfileRegistryError as exc:
        logger.exception("dockerfile_build_failed", job_id=job_id)
        update_build_job_sync(
            job_id,
            status=DockerfileBuildJobStatus.FAILED,
            error=sanitize_log_message(str(exc))[:800],
        )
    except Exception as exc:
        logger.exception("dockerfile_build_unexpected_error", job_id=job_id)
        update_build_job_sync(
            job_id,
            status=DockerfileBuildJobStatus.FAILED,
            error=sanitize_log_message(str(exc))[:800],
        )


async def _attach_docker_host_preview_ingress(
    env_uuid: object,
    environment: object,
    resources: object,
    provisioner: object,
) -> None:
    """Point attach/compose Open-app at ``ws-{id}.{PREVIEW_BASE_DOMAIN}``.

    Bridges the Docker-published host port through in-cluster Ingress so the named
    Cloudflare Tunnel (``*.domain`` → ingress :3080) serves the same URL shape as
    Kubernetes previews. No-op when k8s/tunnel/base domain are unavailable, or when
    deploy mode is already a native k8s Ingress workload.
    """
    try:
        if resources is None:
            logger.info(
                "docker_host_preview_ingress_attach_skipped",
                reason="no_resources",
                environment_id=str(env_uuid),
            )
            return
        deploy_mode = getattr(environment, "deploy_mode", None)
        deploy_mode_s = (
            deploy_mode.value if hasattr(deploy_mode, "value") else str(deploy_mode or "")
        ).lower()
        if deploy_mode_s not in {"attach", "compose"}:
            return
        # Treat missing provider as local (matches create defaults / rest of control plane).
        provider = (getattr(environment, "provider", None) or "local").lower()
        if provider != "local":
            logger.info(
                "docker_host_preview_ingress_attach_skipped",
                reason="non_local_provider",
                environment_id=str(env_uuid),
                provider=provider,
            )
            return
        node_port = getattr(resources, "node_port", None)
        if node_port is None:
            logger.info(
                "docker_host_preview_ingress_attach_skipped",
                reason="no_node_port",
                environment_id=str(env_uuid),
            )
            return

        # Attach/compose VALIDATE skips cluster ensure; reload clients so the bridge
        # can talk to k3d after Docker publish succeeds.
        reload = getattr(provisioner, "reload_clients", None) or getattr(
            provisioner, "_load_clients", None
        )
        if callable(reload):
            try:
                await asyncio.to_thread(reload)
            except Exception as exc:
                logger.warning(
                    "docker_host_preview_ingress_reload_failed",
                    environment_id=str(env_uuid),
                    error=str(exc),
                )

        apply = getattr(provisioner, "apply_docker_host_preview_ingress", None)
        if apply is None:
            return
        url = await asyncio.wait_for(
            asyncio.to_thread(
                apply,
                namespace=str(
                    getattr(resources, "namespace", None)
                    or getattr(environment, "namespace_name", "")
                ),
                environment_id=str(env_uuid),
                name=str(getattr(environment, "name", None) or "preview"),
                host_port=int(node_port),
                labels=dict(getattr(resources, "labels", None) or {}),
            ),
            timeout=45.0,
        )
        if not url:
            logger.warning(
                "docker_host_preview_ingress_attach_no_url",
                environment_id=str(env_uuid),
                node_port=node_port,
            )
            return
        resources.preview_url = url
        endpoints = list(getattr(resources, "preview_endpoints", None) or [])
        if not endpoints:
            return
        updated: list[dict[str, object]] = []
        frontend_updated = False
        for ep in endpoints:
            item = dict(ep)
            if not frontend_updated and item.get("app_kind") == "frontend":
                item["url"] = url
                frontend_updated = True
            updated.append(item)
        if not frontend_updated and updated:
            updated[0]["url"] = url
        resources.preview_endpoints = updated
        logger.info(
            "docker_host_preview_ingress_attach_ok",
            environment_id=str(env_uuid),
            preview_url=url,
        )
    except TimeoutError:
        logger.warning(
            "docker_host_preview_ingress_attach_timeout",
            environment_id=str(env_uuid),
        )
    except Exception:
        logger.exception(
            "docker_host_preview_ingress_attach_failed",
            environment_id=str(env_uuid),
        )


async def _attach_preview_tunnel(env_uuid: object, environment: object, resources: object) -> None:
    """Point a local preview's ``preview_url`` at a per-preview cloudflared tunnel.

    No-op unless ``PREVIEW_TUNNEL_MODE=cloudflared`` and this is a local NodePort /
    Docker-publish preview. Skips Kubernetes Ingress URLs (``ws-*``) which the named
    Cloudflare Tunnel already serves. On success it rewrites ``resources.preview_url``
    in place so the caller persists the public ``*.trycloudflare.com`` URL as the
    Open-app link. Any failure is swallowed - a missing tunnel just falls back to the
    NodePort URL.
    """
    try:
        node_port = getattr(resources, "node_port", None)
        if resources is None or node_port is None:
            return
        if (getattr(environment, "provider", None) or "").lower() != "local":
            return
        from app.services.preview_tunnel import (
            should_attach_preview_tunnel,
            start_preview_tunnel,
        )

        deploy_mode = getattr(environment, "deploy_mode", None)
        deploy_mode_s = (
            deploy_mode.value if hasattr(deploy_mode, "value") else str(deploy_mode or "")
        )
        if not should_attach_preview_tunnel(
            deploy_mode=deploy_mode_s,
            preview_url=getattr(resources, "preview_url", None),
        ):
            return
        url = await asyncio.to_thread(
            start_preview_tunnel, environment_id=str(env_uuid), node_port=node_port
        )
        if not url:
            return
        resources.preview_url = url
        endpoints = list(getattr(resources, "preview_endpoints", None) or [])
        if not endpoints:
            return
        updated: list[dict[str, object]] = []
        frontend_updated = False
        for ep in endpoints:
            item = dict(ep)
            if not frontend_updated and item.get("app_kind") == "frontend":
                item["url"] = url
                frontend_updated = True
            updated.append(item)
        if not frontend_updated and updated:
            updated[0]["url"] = url
        resources.preview_endpoints = updated
    except Exception:
        logger.exception("preview_tunnel_attach_failed", environment_id=str(env_uuid))


async def _attach_cloud_preview_url(
    env_uuid: object, environment: object, resources: object, provisioner: object
) -> None:
    """Point a cloud/production preview's ``preview_url`` at its public address.

    No-op for local previews. For cloud providers it reads the LoadBalancer/Ingress
    external address from the cluster and rewrites ``resources.preview_url`` so the
    Open-app link is the real production URL, not a NodePort/loopback guess. Any
    failure is swallowed - the caller keeps its default URL.
    """
    try:
        if resources is None:
            return
        provider = (getattr(environment, "provider", None) or "").lower()
        if provider in ("", "local"):
            return
        namespace = getattr(resources, "namespace", None)
        if not namespace:
            return
        timeout = get_settings().preview_cloud_url_timeout_seconds
        url = await asyncio.to_thread(
            provisioner.resolve_external_preview_url, namespace, timeout_seconds=timeout
        )
        if url:
            resources.preview_url = url
            return
        # Drop stale local-tunnel Open-app URLs if the cloud LB is not ready yet.
        current = str(getattr(resources, "preview_url", None) or "")
        if current and _preview_url_is_local_tunnel(current):
            logger.warning(
                "cloud_preview_url_cleared_local_tunnel",
                environment_id=str(env_uuid),
                previous=current[:120],
            )
            resources.preview_url = None
    except Exception:
        logger.exception("cloud_preview_url_resolve_failed", environment_id=str(env_uuid))


def _preview_url_is_local_tunnel(url: str) -> bool:
    from urllib.parse import urlparse

    from app.services.kubernetes import _is_local_preview_ingress_host

    host = (urlparse(url).hostname or "").strip().lower()
    if not host:
        return False
    return _is_local_preview_ingress_host(host, settings=get_settings())

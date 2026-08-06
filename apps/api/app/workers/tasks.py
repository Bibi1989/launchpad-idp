from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

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
    is_state_locked,
)
from app.workers.celery_app import celery_app

configure_logging()
logger = get_logger(__name__)


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
) -> None:
    await publish_env_event(
        environment_id,
        event_type="STATUS_CHANGE",
        status=status.value,
        commit_sha=commit_sha,
        message=message,
        stage=stage,
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
    """True when a force-delete flipped the environment to TEARDOWN_PENDING/DESTROYED.

    Re-queries the row so the running provision task observes a delete request
    issued after it started, and aborts at the next checkpoint.
    """
    fresh = await env_repo.get_by_id(env_uuid)
    return fresh is not None and fresh.status in {
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
        )


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
        async with acquire_state_lock(env_uuid, scope="environment", settings=settings):
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

                await _emit_stage(
                    log_repo,
                    session,
                    environment_id=env_uuid,
                    stage=ExecutionStage.INIT,
                    message="INIT - starting Kubernetes provision workflow",
                    commit_sha=commit_sha,
                )

                try:
                    current_stage = ExecutionStage.VALIDATE
                    if settings.kubernetes_enabled:
                        provider = getattr(environment, "provider", None)
                        is_local = str(provider or "").lower() == "local"
                        ctx = settings.resolved_kubernetes_context or settings.kubernetes_context or "default"
                        if is_local:
                            from app.services.kind_cluster import ensure_kind_cluster

                            await ensure_kind_cluster()
                            provisioner.reload_clients()
                        provisioner.assert_cluster_ready(timeout_seconds=5.0)
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
                    deploy_mode_for_plan = getattr(environment, "deploy_mode", DeployMode.PREVIEW.value)
                    plan_image_note = (
                        environment.workload_image or settings.default_workload_image
                        if deploy_mode_for_plan != DeployMode.MANIFEST.value
                        else "from workspace manifests"
                    )
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

                    deploy_mode = getattr(environment, "deploy_mode", DeployMode.PREVIEW.value)
                    deploy_image = environment.workload_image or settings.default_workload_image
                    built_image, built_sha = await _maybe_build_preview_image(
                        log_repo,
                        settings=settings,
                        environment=environment,
                        env_uuid=env_uuid,
                        commit_sha=commit_sha,
                    )
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
                    if workspace_id is not None:
                        workspace_row = await session.get(ProvisioningWorkspace, workspace_id)
                        if workspace_row is not None:
                            from app.services.provisioning import ProvisioningService

                            owner_row = await session.get(User, environment.owner_id)
                            if owner_row is not None:
                                from fastapi import HTTPException

                                provisioning = ProvisioningService(session)
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

                    _ctx = settings.resolved_kubernetes_context or settings.kubernetes_context or ""
                    if settings.kubernetes_enabled and (environment.provider == "local" or _ctx.startswith(("kind-", "k3d-"))) and workspace_root is not None:
                        local_cluster = (_ctx or "launchpad").removeprefix("kind-").removeprefix("k3d-")
                        _build_and_load_kind_docker_images(workspace_root, cluster_name=local_cluster)

                    if deploy_mode == DeployMode.MANIFEST.value and workspace_root is not None:
                        from app.services.manifest_deploy import workspace_has_helm_chart

                        helm_note = (
                            " (Helm chart)"
                            if workspace_has_helm_chart(workspace_root)
                            else ""
                        )
                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message=f"APPLY - deploying workspace Kubernetes manifests{helm_note}",
                            commit_sha=commit_sha,
                        )

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
                        )
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
                        )

                    if resources.simulated:
                        await _emit_stage(
                            log_repo,
                            session,
                            environment_id=env_uuid,
                            stage=ExecutionStage.APPLY,
                            message=(
                                "APPLY - simulated ResourceQuota, LimitRange, "
                                "NetworkPolicy, Deployment, and Service"
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
                    #  • local + tunnel on → cloudflared quick-tunnel URL
                    #  • local + tunnel off → localhost NodePort URL (provisioner default)
                    #  • cloud/production → LoadBalancer/Ingress public URL
                    await _attach_preview_tunnel(env_uuid, environment, resources)
                    await _attach_cloud_preview_url(env_uuid, environment, resources, provisioner)

                    await env_repo.update_status(
                        environment,
                        EnvironmentStatus.RUNNING,
                        preview_url=resources.preview_url,
                        node_port=resources.node_port,
                        workload_image=resources.image,
                    )
                    preview_note = (
                        f" Open app: {resources.preview_url}"
                        if resources.preview_url
                        else ""
                    )
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message=f"APPLY - provision completed, RUNNING.{preview_note}",
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
                    )
                except PreviewCancelled:
                    # Force-delete during provisioning. Leave status TEARDOWN_PENDING
                    # and let the enqueued teardown task reclaim the namespace + kind
                    # images once this task releases the state lock. Do not mark FAILED.
                    logger.info("provision_cancelled_by_delete", environment_id=environment_id)
                    await session.rollback()
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
                            )
                            environment = await env_repo.get_by_id(env_uuid)
                            if environment is not None:
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
                                )
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
                    )
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

                if environment.status != EnvironmentStatus.TEARDOWN_PENDING:
                    await env_repo.update_status(
                        environment, EnvironmentStatus.TEARDOWN_PENDING
                    )
                    await session.commit()
                    await _publish_status(
                        env_uuid,
                        status=EnvironmentStatus.TEARDOWN_PENDING,
                        commit_sha=environment.latest_commit_sha,
                        message="Teardown started",
                        stage=ExecutionStage.INIT,
                    )

                await _emit_stage(
                    log_repo,
                    session,
                    environment_id=env_uuid,
                    stage=ExecutionStage.INIT,
                    message=f"INIT - tearing down namespace {environment.namespace_name}",
                    commit_sha=environment.latest_commit_sha,
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
                        commit_sha=environment.latest_commit_sha,
                        status=EnvironmentStatus.TEARDOWN_PENDING.value,
                    )

                    # Best-effort cluster cleanup: honor the destroy request even if
                    # the cluster is unreachable. A cleanup failure must NOT leave the
                    # environment stuck - we still mark it DESTROYED below and let the
                    # cluster GC / TTL reaper reclaim any orphaned namespace.
                    try:
                        provisioner.teardown(environment.namespace_name)
                    except Exception as cleanup_exc:
                        logger.warning(
                            "teardown_namespace_cleanup_failed",
                            environment_id=environment_id,
                            namespace=environment.namespace_name,
                            error=str(cleanup_exc),
                        )
                        await _emit_log(
                            log_repo,
                            environment_id=env_uuid,
                            message=(
                                "APPLY - namespace cleanup could not reach the cluster "
                                f"({sanitize_log_message(str(cleanup_exc))}); marking DESTROYED anyway"
                            ),
                            log_level=LogLevel.WARN,
                            status=EnvironmentStatus.TEARDOWN_PENDING.value,
                            commit_sha=environment.latest_commit_sha,
                            stage=ExecutionStage.APPLY,
                        )
                    # Stop the per-preview cloudflared tunnel (if any) so we don't
                    # leak a cloudflared process for a destroyed environment.
                    try:
                        from app.services.preview_tunnel import stop_preview_tunnel

                        await asyncio.to_thread(stop_preview_tunnel, str(env_uuid))
                    except Exception:
                        logger.exception("preview_tunnel_stop_failed", environment_id=str(env_uuid))
                    # Reclaim locally-built app images from kind/k3d + host Docker
                    # so deleted previews do not leak disk (leave shared base images).
                    try:
                        from app.services.image_cleanup import (
                            collect_preview_environment_images,
                            remove_local_docker_images,
                            resolve_local_cluster_short_name,
                        )

                        preview_images = collect_preview_environment_images(
                            settings=settings,
                            environment_id=str(env_uuid),
                            workload_image=environment.workload_image,
                            commit_sha=environment.latest_commit_sha,
                        )
                        if preview_images:
                            is_local = environment.provider == "local" or (
                                (settings.resolved_kubernetes_context or settings.kubernetes_context or "")
                                .startswith(("kind-", "k3d-"))
                            )
                            remove_local_docker_images(
                                preview_images,
                                cluster_name=resolve_local_cluster_short_name(settings),
                                settings=settings,
                                remove_from_cluster=is_local,
                            )
                    except Exception:
                        logger.exception(
                            "teardown_image_cleanup_failed", environment_id=environment_id
                        )
                    await asyncio.sleep(settings.provision_step_delay_seconds)
                    await env_repo.update_status(environment, EnvironmentStatus.DESTROYED)
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message="APPLY - teardown completed, DESTROYED",
                        status=EnvironmentStatus.DESTROYED.value,
                        commit_sha=environment.latest_commit_sha,
                        stage=ExecutionStage.APPLY,
                    )
                    await _record_audit(
                        session,
                        action=AuditAction.TEARDOWN_SUCCEEDED,
                        actor_id=actor_id,
                        status=AuditStatus.SUCCESS,
                        environment_id=env_uuid,
                        workspace_id=workspace_id,
                        commit_sha=environment.latest_commit_sha,
                    )
                    await session.commit()
                    await _publish_status(
                        env_uuid,
                        status=EnvironmentStatus.DESTROYED,
                        commit_sha=environment.latest_commit_sha,
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
                        commit_sha=environment.latest_commit_sha,
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
                action=AuditAction.TEARDOWN_INITIATED,
                actor_id=actor_id,
                status=AuditStatus.REJECTED,
                environment_id=env_uuid,
                workspace_id=workspace_id,
                commit_sha=None,
                detail=PROVISIONING_IN_PROGRESS_MESSAGE,
            )
            await session.commit()


async def pause_expired_environment(
    session,
    environment,
    *,
    actor_id: str = "system:ttl-reaper",
    settings=None,
) -> bool:
    """Scale workloads to 0 and mark an expired environment EXPIRED.

    Returns True when the environment was expired in this call.
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
    scaled_ok = True
    try:
        provisioner = KubernetesProvisioner(settings=settings)
        scaled_ok = bool(
            provisioner.scale_deployment(namespace=environment.namespace_name, replicas=0)
        )
    except Exception as e:  # noqa: BLE001 - always mark expired; retry scale next cycle
        scaled_ok = False
        logger.error("ttl_reaper_scale_failed", environment_id=str(environment.id), error=str(e))

    await env_repo.update_status(environment, EnvironmentStatus.EXPIRED)
    scale_note = "scaled replicas to 0" if scaled_ok else "status expired (scale retry next cycle)"
    await _emit_log(
        log_repo,
        environment_id=environment.id,
        message=(
            f"TTL expired - environment marked expired ({scale_note}) "
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
        detail="TTL expired" if scaled_ok else "TTL expired (scale pending retry)",
    )
    return True


# A preview stuck in PROVISIONING with no live worker (crashed mid-run) is failed
# after this many seconds. Comfortably above the 3-minute readiness cap + overhead
# so a legitimately slow-but-active provision is never falsely failed.
STALE_PROVISIONING_SECONDS = 600


async def _reap_stale_provisioning(session, env_repo, *, now: datetime) -> list[tuple]:
    """Fail previews stuck in PROVISIONING with no active worker (watchdog).

    Skips rows still holding the provision state lock (a worker is actively
    provisioning them) so only genuinely orphaned rows flip to FAILED.
    """
    from datetime import timedelta

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
        await session.commit()

        for environment_id, commit_sha in expired_events:
            await _publish_status(
                environment_id,
                status=EnvironmentStatus.EXPIRED,
                commit_sha=commit_sha,
                message="TTL expired",
                stage=ExecutionStage.APPLY,
            )
        for environment_id, commit_sha in stale_failed:
            reaped += 1
            await _publish_status(
                environment_id,
                status=EnvironmentStatus.FAILED,
                commit_sha=commit_sha,
                message="Provisioning timed out - no active worker",
                stage=ExecutionStage.APPLY,
            )

    logger.info("ttl_reaper_complete", reaped=reaped, interval=settings.ttl_reaper_interval_seconds)
    return reaped


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


@celery_app.task(name="launchpad.teardown_environment", bind=True, max_retries=0)
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
    from app.services.github_app import (
        GitHubAppAuthError,
        get_installation_access_token,
        is_github_app_configured,
    )
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
    token: str | None = None
    if settings.github_pat:
        token = settings.github_pat.strip() or None
    elif is_github_app_configured(settings):
        try:
            token = get_installation_access_token(
                installation_id=request.installation_id,
                settings=settings,
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


async def _attach_preview_tunnel(env_uuid: object, environment: object, resources: object) -> None:
    """Point a local preview's ``preview_url`` at a per-preview cloudflared tunnel.

    No-op unless ``PREVIEW_TUNNEL_MODE=cloudflared`` and this is a local NodePort
    preview. On success it rewrites ``resources.preview_url`` in place so the caller
    persists the public ``*.trycloudflare.com`` URL as the Open-app link. Any failure
    is swallowed - a missing tunnel just falls back to the NodePort URL.
    """
    try:
        node_port = getattr(resources, "node_port", None)
        if resources is None or node_port is None:
            return
        if (getattr(environment, "provider", None) or "").lower() != "local":
            return
        from app.services.preview_tunnel import start_preview_tunnel, tunnel_enabled

        if not tunnel_enabled():
            return
        url = await asyncio.to_thread(
            start_preview_tunnel, environment_id=str(env_uuid), node_port=node_port
        )
        if url:
            resources.preview_url = url
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
    except Exception:
        logger.exception("cloud_preview_url_resolve_failed", environment_id=str(env_uuid))


def _build_and_load_kind_docker_images(workspace_root: Path, cluster_name: str = "launchpad") -> list[str]:
    """Build workspace Dockerfiles and load images into the local cluster.

    Delegates to ``manifest_deploy.build_and_load_kind_images`` so import
    ``launch-*:latest`` tags stay aligned with Deployment manifests.
    """
    from app.services.manifest_deploy import build_and_load_kind_images

    return build_and_load_kind_images(workspace_root, cluster_name=cluster_name)


def _remove_kind_docker_images(cluster_name: str, images: "list[str] | None") -> list[str]:
    """Compatibility wrapper around ``image_cleanup.remove_local_docker_images``."""
    from app.services.image_cleanup import remove_local_docker_images

    return remove_local_docker_images(images, cluster_name=cluster_name)

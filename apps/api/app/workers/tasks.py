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
    EnvironmentStatus,
    ExecutionStage,
    LogLevel,
    ProvisioningWorkspace,
)
from app.repositories.environment import DeploymentLogRepository, EnvironmentRepository
from app.repositories.user import UserRepository
from app.schemas.k8s import DeployMode
from app.services.audit import AuditService
from app.services.drift_scanner import DRIFT_SCANNER_ACTOR, record_drift_if_changed, scan_environment
from app.services.kubernetes import KubernetesProvisioner, ProvisionedResources
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
            f"BUILD — cloning {environment.git_repo_url} "
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
        message=f"BUILD — {mode} image {result.image} (commit {result.commit_sha})",
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

                await _emit_log(
                    log_repo,
                    environment_id=env_uuid,
                    message="INIT — starting Kubernetes provision workflow",
                    status=EnvironmentStatus.PROVISIONING.value,
                    commit_sha=commit_sha,
                    stage=ExecutionStage.INIT,
                )
                await session.commit()

                try:
                    current_stage = ExecutionStage.VALIDATE
                    if settings.kubernetes_enabled:
                        mode_msg = (
                            f"VALIDATE — cluster mode "
                            f"(context={settings.kubernetes_context or 'default'})"
                        )
                    else:
                        mode_msg = (
                            "VALIDATE — simulate mode (KUBERNETES_ENABLED=false); "
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
                    await session.commit()

                    await asyncio.sleep(settings.provision_step_delay_seconds)

                    current_stage = ExecutionStage.PLAN
                    deploy_mode_for_plan = getattr(environment, "deploy_mode", DeployMode.PREVIEW.value)
                    plan_image_note = (
                        environment.workload_image or settings.default_workload_image
                        if deploy_mode_for_plan != DeployMode.MANIFEST.value
                        else "from workspace manifests"
                    )
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message=(
                            f"PLAN — namespace {environment.namespace_name}, "
                            f"image {plan_image_note}"
                        ),
                        status=EnvironmentStatus.PROVISIONING.value,
                        commit_sha=commit_sha,
                        stage=ExecutionStage.PLAN,
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

                    current_stage = ExecutionStage.APPLY
                    if deploy_mode == DeployMode.MANIFEST.value and workspace_id is not None:
                        from app.services.manifest_deploy import workspace_has_helm_chart

                        workspace_row = await session.get(ProvisioningWorkspace, workspace_id)
                        if workspace_row is None:
                            raise RuntimeError(f"Workspace {workspace_id} not found for manifest deploy")
                        workspace_root = Path(workspace_row.root_dir)
                        helm_note = (
                            " (Helm chart)"
                            if workspace_has_helm_chart(workspace_root)
                            else ""
                        )
                        await _emit_log(
                            log_repo,
                            environment_id=env_uuid,
                            message=f"APPLY — deploying workspace Kubernetes manifests{helm_note}",
                            status=EnvironmentStatus.PROVISIONING.value,
                            commit_sha=commit_sha,
                            stage=ExecutionStage.APPLY,
                        )
                        await session.commit()
                        if settings.kubernetes_enabled and (environment.provider == "local" or (settings.kubernetes_context or "").startswith("kind-")):
                            kind_cluster = (settings.kubernetes_context or "launchpad").removeprefix("kind-")
                            _build_and_load_kind_docker_images(workspace_root, cluster_name=kind_cluster)

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
                        await _emit_log(
                            log_repo,
                            environment_id=env_uuid,
                            message="APPLY — mutating Kubernetes resources (preview profile)",
                            status=EnvironmentStatus.PROVISIONING.value,
                            commit_sha=commit_sha,
                            stage=ExecutionStage.APPLY,
                        )
                        await session.commit()
                        if getattr(environment, "enable_postgres", False) or getattr(environment, "enable_redis", False):
                            provisioner.apply_ephemeral_datastores(
                                namespace=environment.namespace_name,
                                name=environment.name,
                                enable_postgres=getattr(environment, "enable_postgres", False),
                                enable_redis=getattr(environment, "enable_redis", False),
                            )
                        resources = provisioner.provision(
                            namespace=environment.namespace_name,
                            environment_id=str(environment.id),
                            name=environment.name,
                            git_branch=environment.git_branch,
                            git_repo_url=environment.git_repo_url,
                            ttl_expires_at=environment.ttl_expires_at.isoformat(),
                            owner_label=owner_label,
                            image=deploy_image,
                        )

                    if resources.simulated:
                        await _emit_log(
                            log_repo,
                            environment_id=env_uuid,
                            message=(
                                "APPLY — simulated ResourceQuota, LimitRange, "
                                "NetworkPolicy, Deployment, and Service"
                            ),
                            status=EnvironmentStatus.PROVISIONING.value,
                            commit_sha=commit_sha,
                            stage=ExecutionStage.APPLY,
                        )
                    else:
                        port_note = (
                            f", NodePort {resources.node_port}"
                            if resources.node_port is not None
                            else ""
                        )
                        await _emit_log(
                            log_repo,
                            environment_id=env_uuid,
                            message=(
                                f"APPLY — ResourceQuota + LimitRange + zero-trust NetworkPolicy; "
                                f"Deployment/Service ready (image={resources.image}{port_note})"
                            ),
                            status=EnvironmentStatus.PROVISIONING.value,
                            commit_sha=commit_sha,
                            stage=ExecutionStage.APPLY,
                        )
                    await session.commit()

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
                        message=f"APPLY — provision completed, RUNNING.{preview_note}",
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
                                        "APPLY — provision completed after Ready deadline "
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
                        error_text=f"Provision failed — rolling back: {error_text}",
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

                if environment.status == EnvironmentStatus.TEARDOWN_PENDING:
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message="Skipping rebuild — environment is tearing down",
                        log_level=LogLevel.WARN,
                        status=environment.status.value,
                        commit_sha=short_sha,
                        stage=ExecutionStage.INIT,
                    )
                    await session.commit()
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

                await _emit_log(
                    log_repo,
                    environment_id=env_uuid,
                    message=f"INIT — GitOps rebuild for commit {short_sha}",
                    status=EnvironmentStatus.PROVISIONING.value,
                    commit_sha=short_sha,
                    stage=ExecutionStage.INIT,
                )
                await session.commit()

                try:
                    await asyncio.sleep(settings.provision_step_delay_seconds)
                    current_stage = ExecutionStage.VALIDATE
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message=f"VALIDATE — pulling application revision {short_sha}",
                        status=EnvironmentStatus.PROVISIONING.value,
                        commit_sha=short_sha,
                        stage=ExecutionStage.VALIDATE,
                    )
                    await session.commit()

                    await asyncio.sleep(settings.provision_step_delay_seconds)
                    current_stage = ExecutionStage.PLAN
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message=(
                            f"PLAN — workload update in {environment.namespace_name} "
                            f"(kubectl set image / Deployment roll)"
                        ),
                        status=EnvironmentStatus.PROVISIONING.value,
                        commit_sha=short_sha,
                        stage=ExecutionStage.PLAN,
                    )
                    await session.commit()

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
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message="APPLY — rolling Deployment",
                        status=EnvironmentStatus.PROVISIONING.value,
                        commit_sha=short_sha,
                        stage=ExecutionStage.APPLY,
                    )
                    await session.commit()

                    provisioner.rebuild_workload(
                        namespace=environment.namespace_name,
                        environment_id=str(environment.id),
                        name=environment.name,
                        git_branch=environment.git_branch,
                        git_repo_url=environment.git_repo_url,
                        commit_sha=short_sha,
                        owner_label=owner_label,
                        image=rebuild_image,
                    )

                    await asyncio.sleep(settings.provision_step_delay_seconds)
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message="APPLY — waiting for rollout to complete",
                        status=EnvironmentStatus.PROVISIONING.value,
                        commit_sha=short_sha,
                        stage=ExecutionStage.APPLY,
                    )
                    await session.commit()

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
                        message=f"APPLY — rebuild completed, RUNNING at {short_sha}",
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

                await _emit_log(
                    log_repo,
                    environment_id=env_uuid,
                    message=f"INIT — tearing down namespace {environment.namespace_name}",
                    status=EnvironmentStatus.TEARDOWN_PENDING.value,
                    commit_sha=environment.latest_commit_sha,
                    stage=ExecutionStage.INIT,
                )
                await session.commit()

                try:
                    current_stage = ExecutionStage.APPLY
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message="APPLY — deleting namespace and resources",
                        status=EnvironmentStatus.TEARDOWN_PENDING.value,
                        commit_sha=environment.latest_commit_sha,
                        stage=ExecutionStage.APPLY,
                    )
                    await session.commit()

                    provisioner.teardown(environment.namespace_name)
                    await asyncio.sleep(settings.provision_step_delay_seconds)
                    await env_repo.update_status(environment, EnvironmentStatus.DESTROYED)
                    await _emit_log(
                        log_repo,
                        environment_id=env_uuid,
                        message="APPLY — teardown completed, DESTROYED",
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
    """Scale workloads to 0 and mark an expired environment PAUSED.

    Returns True when the environment was paused in this call.
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
    except Exception as e:  # noqa: BLE001 — always mark paused; retry scale next cycle
        scaled_ok = False
        logger.error("ttl_reaper_scale_failed", environment_id=str(environment.id), error=str(e))

    await env_repo.update_status(environment, EnvironmentStatus.PAUSED)
    scale_note = "scaled replicas to 0" if scaled_ok else "status paused (scale retry next cycle)"
    await _emit_log(
        log_repo,
        environment_id=environment.id,
        message=(
            f"TTL expired — pausing environment ({scale_note}) "
            f"(expires_at={environment.ttl_expires_at.isoformat()})"
        ),
        log_level=LogLevel.WARN,
        status=EnvironmentStatus.PAUSED.value,
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


async def _run_ttl_reaper() -> int:
    settings = get_settings()
    session_factory = _session_factory()
    reaped = 0

    async with session_factory() as session:
        env_repo = EnvironmentRepository(session)
        expired = await env_repo.list_expired_running(now=datetime.now(UTC))
        paused: list[tuple] = []
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
                paused.append((environment.id, commit_sha))
        await session.commit()

        for environment_id, commit_sha in paused:
            await _publish_status(
                environment_id,
                status=EnvironmentStatus.PAUSED,
                commit_sha=commit_sha,
                message="TTL expired - paused",
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


@celery_app.task(name="launchpad.reap_expired_environments")
def reap_expired_environments_task() -> int:
    return asyncio.run(_run_ttl_reaper())


@celery_app.task(name="launchpad.scan_preview_drift")
def scan_preview_drift_task() -> int:
    return asyncio.run(_run_drift_scan())


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


def _build_and_load_kind_docker_images(workspace_root: Path, cluster_name: str = "launchpad") -> list[str]:
    """Build workspace Dockerfile(s) and load into local kind cluster so pods pull cleanly."""
    import subprocess
    import shutil

    if not (shutil.which("docker") and shutil.which("kind")):
        return []

    loaded: list[str] = []
    dockers_dir = workspace_root / "dockers"
    dockerfiles: list[Path] = []
    if dockers_dir.is_dir():
        dockerfiles.extend(dockers_dir.rglob("Dockerfile*"))
    root_df = workspace_root / "Dockerfile"
    if root_df.is_file():
        dockerfiles.append(root_df)

    for df in dockerfiles:
        if not df.is_file():
            continue
        rel = df.relative_to(workspace_root)
        svc_name = df.parent.name if df.parent != workspace_root and df.parent.name != "dockers" else "app"
        image_tag = f"launchpad/{svc_name.lower()}:latest"
        try:
            logger.info("building_kind_docker_image", image=image_tag, dockerfile=str(rel))
            build_res = subprocess.run(
                ["docker", "build", "-t", image_tag, "-f", str(df), str(workspace_root)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if build_res.returncode == 0:
                logger.info("loading_kind_docker_image", image=image_tag, cluster=cluster_name)
                load_res = subprocess.run(
                    ["kind", "load", "docker-image", image_tag, "--name", cluster_name],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if load_res.returncode == 0:
                    loaded.append(image_tag)
        except Exception as exc:
            logger.warning("kind_image_build_failed", dockerfile=str(rel), error=str(exc))

    return loaded

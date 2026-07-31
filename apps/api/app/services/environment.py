from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.events import publish_env_event
from app.core.logging import get_logger
from app.models.domain import (
    AuditAction,
    AuditStatus,
    EnvironmentStatus,
    ExecutionStage,
    LogLevel,
    OrgRole,
    User,
)
from app.repositories.environment import DeploymentLogRepository, EnvironmentRepository
from app.schemas.cloud import (
    AwsCloudConfig,
    AwsResources,
    AzureCloudConfig,
    AzureResources,
    CloudflareCloudConfig,
    CloudflareResources,
    CloudProvider,
    GcpCloudConfig,
    GcpResources,
    IaCEngine,
    KubernetesPackaging,
    ProvisioningWizardRequest,
)
from app.schemas.environment import (
    DeploymentLogRead,
    EnvironmentCreate,
    EnvironmentExtendRequest,
    EnvironmentPromoteRequest,
    EnvironmentRead,
    PreviewAppTemplateRead,
    PreviewLaunchRequest,
    PreviewProvider,
)
from app.schemas.k8s import DeployMode
from app.schemas.orgs import OrgCostEnvironmentItem, OrgCostSummary
from app.services.audit import AuditService
from app.services.preview_urls import stable_pr_preview_url
from app.services.drift_scanner import record_drift_if_changed, scan_environment
from app.services.kubernetes import KubernetesProvisioner
from app.services.orgs import OrganizationService, role_at_least
from app.services.preview_templates import get_preview_template, list_preview_templates
from app.services.provisioning import ProvisioningService
from app.services.state_lock import (
    PROVISIONING_IN_PROGRESS_MESSAGE,
    is_state_locked,
)
from app.workers.tasks import enqueue_provision_environment, enqueue_teardown_environment

logger = get_logger(__name__)

TERMINAL_STATUSES = {
    EnvironmentStatus.RUNNING,
    EnvironmentStatus.FAILED,
    EnvironmentStatus.DESTROYED,
}


class EnvironmentService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._environments = EnvironmentRepository(session)
        self._logs = DeploymentLogRepository(session)

    async def _pause_expired_rows(self, rows: list) -> None:
        """Pause any returned environments that are past TTL (eager, no Celery required)."""
        from app.workers.tasks import pause_expired_environment

        paused_any = False
        for row in rows:
            did = await pause_expired_environment(
                self._session,
                row,
                actor_id="system:ttl-eager",
                settings=self._settings,
            )
            if did:
                paused_any = True
                logger.info("environment_ttl_eager_paused", environment_id=str(row.id))
        if paused_any:
            await self._session.commit()

    async def list_environments(
        self,
        owner: User,
        *,
        org_id: UUID | None = None,
    ) -> list[EnvironmentRead]:
        orgs = OrganizationService(self._session)
        if org_id is not None:
            ctx = await orgs.resolve_context(user=owner, org_id=org_id)
            rows = await self._environments.list_for_org(ctx.org_id)
        else:
            org = await orgs.ensure_personal_org(owner)
            rows = await self._environments.list_for_org(org.id)
            if not rows:
                rows = await self._environments.list_for_owner(owner.id)
        await self._pause_expired_rows(rows)
        active = await self._environments.count_active_for_owner(owner.id)
        reads = [self._to_read(row, concurrent_active_count=active) for row in rows]
        return await self._enrich_drift(reads, [row.id for row in rows])

    async def get_environment(self, environment_id: UUID, owner: User) -> EnvironmentRead:
        environment = await self._require_access(environment_id, owner, mutate=False)
        await self._pause_expired_rows([environment])
        active = await self._environments.count_active_for_owner(owner.id)
        read = self._to_read(environment, concurrent_active_count=active)
        enriched = await self._enrich_drift([read], [environment.id])
        return enriched[0]

    async def get_environment_entity(self, environment_id: UUID, owner: User):
        return await self._require_access(environment_id, owner, mutate=False)

    async def scan_drift(self, environment_id: UUID, owner: User) -> EnvironmentRead:
        """Compare live K8s Deployment against control-plane expectations and record drift."""
        environment = await self._require_access(environment_id, owner, mutate=False)
        if not self._settings.kubernetes_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "kubernetes_disabled",
                    "message": "Drift scan requires KUBERNETES_ENABLED=true",
                },
            )
        if environment.status != EnvironmentStatus.RUNNING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "drift_scan_not_allowed",
                    "message": "Drift scan is only available while the environment is RUNNING",
                },
            )

        provisioner = KubernetesProvisioner(self._settings)
        workspace_root = await self._workspace_root_for_environment(environment)
        finding = scan_environment(
            provisioner,
            environment,
            default_image=self._settings.default_workload_image,
            workspace_root=workspace_root,
        )
        if finding is not None:
            audits = AuditService(self._session)
            await record_drift_if_changed(
                audits,
                environment=environment,
                finding=finding,
                actor_id=AuditService.user_actor(owner.id),
            )
            await self._session.commit()

        return await self.get_environment(environment_id, owner)

    async def _workspace_root_for_environment(self, environment) -> Path | None:
        if environment.workspace_id is None:
            return None
        from app.models.domain import ProvisioningWorkspace

        workspace = await self._session.get(ProvisioningWorkspace, environment.workspace_id)
        if workspace is None or not workspace.root_dir:
            return None
        root = Path(workspace.root_dir)
        return root if root.is_dir() else None

    async def org_cost_summary(self, org_id: UUID, user: User) -> OrgCostSummary:
        orgs = OrganizationService(self._session)
        ctx = await orgs.resolve_context(user=user, org_id=org_id)
        rows = await self._environments.list_for_org(ctx.org_id)

        items: list[OrgCostEnvironmentItem] = []
        cloud_accrued = Decimal("0.0000")
        local_accrued = Decimal("0.0000")
        active_count = 0
        cloud_environment_count = 0

        for row in rows:
            if row.status not in {
                EnvironmentStatus.RUNNING,
                EnvironmentStatus.PROVISIONING,
            }:
                continue
            read = self._to_read(row)
            active_count += 1
            items.append(
                OrgCostEnvironmentItem(
                    environment_id=row.id,
                    name=row.name,
                    status=row.status.value,
                    provider=row.provider,
                    is_local=read.is_local,
                    cost_estimate_hourly=read.cost_estimate_hourly,
                    cost_accrued=read.cost_accrued,
                )
            )
            if read.is_local:
                local_accrued += read.cost_accrued
            else:
                cloud_environment_count += 1
                cloud_accrued += read.cost_accrued

        cap = self._settings.preview_soft_cost_cap
        total = (cloud_accrued + local_accrued).quantize(Decimal("0.0001"))
        return OrgCostSummary(
            org_id=ctx.org_id,
            soft_cost_cap=cap,
            active_count=active_count,
            cloud_environment_count=cloud_environment_count,
            cloud_accrued=cloud_accrued.quantize(Decimal("0.0001")),
            local_accrued=local_accrued.quantize(Decimal("0.0001")),
            total_accrued=total,
            soft_cost_cap_exceeded=cloud_accrued >= cap and cloud_environment_count > 0,
            environments=items,
        )

    def list_preview_templates(self) -> list[PreviewAppTemplateRead]:
        return [
            PreviewAppTemplateRead(
                id=item.id,
                title=item.title,
                description=item.description,
                icon=item.icon,
                git_repo_url=item.git_repo_url,
                git_branch=item.git_branch,
                default_ttl_hours=item.default_ttl_hours,
                hourly_cost_hint=item.hourly_cost_hint,
                workload_image=item.workload_image,
                tags=list(item.tags),
            )
            for item in list_preview_templates()
        ]

    async def launch_preview(
        self,
        payload: PreviewLaunchRequest,
        *,
        owner: User,
        correlation_id: str,
        org_id: UUID | None = None,
    ) -> EnvironmentRead:
        """Connect cloud (workspace) + pick template, own repo, or workspace → queue preview."""
        template = None
        workload_image = self._settings.default_workload_image
        if payload.workspace_id is not None:
            git_repo_url, git_branch = self._workspace_git_source(payload.workspace_id)
            template_id = None
            default_ttl = self._settings.default_ttl_hours
            cost_from_template = None
        elif payload.template_id:
            try:
                template = get_preview_template(payload.template_id)
            except KeyError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "template_not_found", "message": str(exc)},
                ) from exc
            git_repo_url = template.git_repo_url
            git_branch = template.git_branch
            template_id = template.id
            default_ttl = template.default_ttl_hours
            cost_from_template = Decimal(template.hourly_cost_hint)
            workload_image = template.workload_image
        else:
            assert payload.git_repo_url is not None
            assert payload.git_branch is not None
            git_repo_url = payload.git_repo_url
            git_branch = payload.git_branch
            template_id = None
            default_ttl = self._settings.default_ttl_hours
            cost_from_template = None
        if payload.workload_image:
            workload_image = payload.workload_image

        existing = await self._environments.get_by_name(payload.name)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "environment_exists",
                    "message": f"Environment '{payload.name}' already exists",
                },
            )

        workspace_id: UUID | None = None
        cost_override: Decimal | None = None
        provisioning = ProvisioningService(self._session)

        if payload.provider == PreviewProvider.LOCAL:
            from app.services.kind_cluster import ensure_kind_cluster

            try:
                await ensure_kind_cluster()
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "kind_cluster_unavailable",
                        "message": str(exc),
                    },
                ) from exc
            cost_override = Decimal("0.0000")
            if payload.workspace_id is not None:
                await provisioning.get_workspace_for_owner(payload.workspace_id, owner)
                workspace_id = payload.workspace_id
        elif payload.workspace_id is not None:
            workspace = await provisioning.get_workspace_for_owner(payload.workspace_id, owner)
            workspace_id = payload.workspace_id
            if not workspace.encrypted_credentials:
                self._require_inline_cloud_credentials(payload)
            if cost_from_template is not None:
                cost_override = cost_from_template
        else:
            cloud = self._cloud_config_for_provider(CloudProvider(payload.provider.value))
            workspace_req = ProvisioningWizardRequest(
                name=f"cloud-{payload.name}"[:64],
                iac_engine=IaCEngine.TERRAFORM,
                cloud=cloud,
                credentials=payload.credentials,
                run_init=False,
            )
            workspace_bundle = await provisioning.generate_bundle(workspace_req, owner=owner)
            workspace_id = UUID(workspace_bundle.workspace_id)
            if cost_from_template is not None:
                cost_override = cost_from_template

        ttl_hours = payload.ttl_hours or min(default_ttl, 168)
        create_payload = EnvironmentCreate(
            name=payload.name,
            git_branch=git_branch,
            git_repo_url=git_repo_url,
            ttl_hours=ttl_hours,
            workspace_id=workspace_id,
            template_id=template_id,
            cost_estimate_hourly=cost_override,
            provider=payload.provider.value,
            workload_image=workload_image,
            github_pr_number=payload.github_pr_number,
            github_pr_url=payload.github_pr_url,
            enable_postgres=payload.enable_postgres,
            enable_redis=payload.enable_redis,
        )
        result = await self.enqueue_provision(
            create_payload,
            owner=owner,
            correlation_id=correlation_id,
            org_id=org_id,
        )
        logger.info(
            "preview_launch_enqueued",
            environment_id=str(result.id),
            template_id=template_id,
            provider=payload.provider.value,
            correlation_id=correlation_id,
        )
        return result

    async def enqueue_provision(
        self,
        payload: EnvironmentCreate,
        *,
        owner: User,
        correlation_id: str,
        org_id: UUID | None = None,
    ) -> EnvironmentRead:
        existing = await self._environments.get_by_name(payload.name)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "environment_exists",
                    "message": f"Environment '{payload.name}' already exists",
                },
            )

        await self._auto_pause_if_needed(owner)
        await self._enforce_soft_cost_cap(owner, payload)

        if payload.workspace_id is not None:
            provisioning = ProvisioningService(self._session)
            workspace = await provisioning.get_workspace_for_owner(payload.workspace_id, owner)
            deploy_mode, manifest_packaging = self._resolve_deploy_mode(payload, workspace, provisioning)
        else:
            deploy_mode = payload.deploy_mode or DeployMode.PREVIEW
            manifest_packaging = None

        ttl_expires_at = datetime.now(UTC) + timedelta(hours=payload.ttl_hours)
        placeholder_namespace = f"launchpad-env-pending-{payload.name}"[:253]
        if payload.cost_estimate_hourly is not None:
            hourly = payload.cost_estimate_hourly
        else:
            hourly = self._settings.cost_estimate_hourly
            if payload.template_id:
                try:
                    template = get_preview_template(payload.template_id)
                    hourly = Decimal(template.hourly_cost_hint)
                except KeyError:
                    pass

        workload_image = payload.workload_image or self._settings.default_workload_image
        if payload.template_id and not payload.workload_image:
            try:
                workload_image = get_preview_template(payload.template_id).workload_image
            except KeyError:
                pass

        if deploy_mode == DeployMode.MANIFEST and payload.workload_image is None and payload.workspace_id:
            try:
                from app.models.domain import ProvisioningWorkspace
                from app.services.manifest_deploy import (
                    _first_deployment_image,
                    load_workspace_manifest_documents,
                )

                workspace_row = await self._session.get(ProvisioningWorkspace, payload.workspace_id)
                if workspace_row and workspace_row.root_dir:
                    docs = load_workspace_manifest_documents(Path(workspace_row.root_dir))
                    extracted = _first_deployment_image(docs)
                    if extracted:
                        workload_image = extracted
            except Exception:
                pass

        workload_image_for_log = workload_image
        if deploy_mode == DeployMode.MANIFEST and payload.workload_image is None and workload_image == self._settings.default_workload_image:
            # For MANIFEST deploys (raw manifests/Helm), the real image is resolved later from the
            # workspace manifests. Until then, `workload_image` may still be the default preview image.
            workload_image_for_log = "from workspace manifests"

        environment = await self._environments.create(
            owner_id=owner.id,
            org_id=org_id or (await OrganizationService(self._session).ensure_personal_org(owner)).id,
            name=payload.name,
            git_branch=payload.git_branch,
            git_repo_url=payload.git_repo_url,
            namespace_name=placeholder_namespace,
            ttl_expires_at=ttl_expires_at,
            cost_estimate_hourly=hourly,
            workspace_id=payload.workspace_id,
            template_id=payload.template_id,
            provider=payload.provider,
            workload_image=workload_image,
            github_pr_number=payload.github_pr_number,
            github_pr_url=payload.github_pr_url,
            deploy_mode=deploy_mode.value,
            manifest_packaging=manifest_packaging,
            enable_postgres=payload.enable_postgres,
            enable_redis=payload.enable_redis,
        )
        environment.namespace_name = f"launchpad-env-{environment.id}"
        await self._session.flush()
        await self._session.refresh(environment)

        await self._logs.create(
            environment_id=environment.id,
            message=(
                f"Queued {deploy_mode.value} deploy for {payload.git_repo_url}@{payload.git_branch} "
                f"(TTL {payload.ttl_hours}h, image={workload_image_for_log}, "
                f"correlation_id={correlation_id})"
            ),
        )
        await AuditService(self._session).record(
            action=AuditAction.PROVISION_INITIATED,
            actor_id=AuditService.user_actor(owner.id),
            status=AuditStatus.PENDING,
            environment_id=environment.id,
            workspace_id=environment.workspace_id,
            commit_sha=None,
            detail=f"correlation_id={correlation_id}",
        )
        await self._session.commit()

        enqueue_provision_environment(
            environment_id=str(environment.id),
            correlation_id=correlation_id,
        )

        logger.info(
            "environment_provision_enqueued",
            environment_id=str(environment.id),
            correlation_id=correlation_id,
            git_branch=payload.git_branch,
            owner_id=str(owner.id),
        )
        active = await self._environments.count_active_for_owner(owner.id)
        return self._to_read(environment, concurrent_active_count=active)

    async def extend_ttl(
        self,
        environment_id: UUID,
        payload: EnvironmentExtendRequest,
        *,
        owner: User,
        correlation_id: str,
    ) -> EnvironmentRead:
        environment = await self._require_owned(environment_id, owner)
        if environment.status not in {
            EnvironmentStatus.RUNNING,
            EnvironmentStatus.FAILED,
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "ttl_extend_not_allowed",
                    "message": "TTL can only be extended while RUNNING or FAILED",
                },
            )

        hours = payload.hours or self._settings.ttl_extend_hours_default
        now = datetime.now(UTC)
        created = environment.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        expires = environment.ttl_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)

        base = max(expires, now)
        candidate = base + timedelta(hours=hours)
        max_expiry = created + timedelta(hours=self._settings.ttl_max_total_hours_from_create)
        if candidate > max_expiry:
            candidate = max_expiry
        if candidate <= expires:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "ttl_max_reached",
                    "message": (
                        f"Cannot extend beyond {self._settings.ttl_max_total_hours_from_create}h "
                        "from create time"
                    ),
                },
            )

        # Soft cost: refuse extend on cloud when accrued already over cap.
        if (environment.provider or "local") != "local":
            read = self._to_read(environment)
            if read.cost_accrued >= self._settings.preview_soft_cost_cap:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail={
                        "code": "soft_cost_cap_exceeded",
                        "message": (
                            f"Cost to date (${read.cost_accrued}) meets soft cap "
                            f"(${self._settings.preview_soft_cost_cap}). Destroy or wait."
                        ),
                    },
                )

        await self._environments.update_ttl(environment, candidate)
        await self._logs.create(
            environment_id=environment.id,
            message=(
                f"TTL extended by {hours}h → {candidate.isoformat()} "
                f"(correlation_id={correlation_id})"
            ),
        )
        await self._session.commit()
        return self._to_read(environment)

    async def promote_to_cloud(
        self,
        environment_id: UUID,
        payload: EnvironmentPromoteRequest,
        *,
        owner: User,
        correlation_id: str,
    ) -> EnvironmentRead:
        """Launch a new cloud preview from an existing environment's source."""
        source = await self._require_owned(environment_id, owner)
        suffix = payload.provider.value[:8]
        name = payload.name or f"{source.name}-{suffix}"[:64]
        if source.template_id:
            launch = PreviewLaunchRequest(
                name=name,
                template_id=source.template_id,
                provider=payload.provider,
                credentials=payload.credentials,
                ttl_hours=payload.ttl_hours,
                github_pr_number=source.github_pr_number,
                github_pr_url=source.github_pr_url,
            )
        else:
            launch = PreviewLaunchRequest(
                name=name,
                git_repo_url=source.git_repo_url,
                git_branch=source.git_branch,
                provider=payload.provider,
                credentials=payload.credentials,
                ttl_hours=payload.ttl_hours,
                github_pr_number=source.github_pr_number,
                github_pr_url=source.github_pr_url,
            )
        result = await self.launch_preview(
            launch,
            owner=owner,
            correlation_id=correlation_id,
        )
        await self._logs.create(
            environment_id=source.id,
            message=(
                f"Promoted to cloud preview {result.name} ({payload.provider.value}) "
                f"as {result.id}"
            ),
        )
        await self._session.commit()
        return result

    async def retry_provision(
        self,
        environment_id: UUID,
        *,
        owner: User,
        correlation_id: str,
    ) -> EnvironmentRead:
        """Re-queue provision for a FAILED or RUNNING environment (same namespace / config)."""
        environment = await self._require_owned(environment_id, owner)
        if environment.status not in {
            EnvironmentStatus.FAILED,
            EnvironmentStatus.RUNNING,
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "retry_not_allowed",
                    "message": "Only FAILED or RUNNING environments can be retried",
                },
            )
        if await is_state_locked(environment_id, scope="environment"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "provisioning_in_progress",
                    "message": PROVISIONING_IN_PROGRESS_MESSAGE,
                },
            )

        await self._environments.update_status(
            environment,
            EnvironmentStatus.PROVISIONING,
            error_message=None,
        )
        await self._logs.create(
            environment_id=environment.id,
            message=f"Retry provision requested (correlation_id={correlation_id})",
        )
        await AuditService(self._session).record(
            action=AuditAction.PROVISION_INITIATED,
            actor_id=AuditService.user_actor(owner.id),
            status=AuditStatus.PENDING,
            environment_id=environment.id,
            workspace_id=environment.workspace_id,
            commit_sha=environment.latest_commit_sha,
            detail=f"retry correlation_id={correlation_id}",
        )
        await self._session.commit()

        enqueue_provision_environment(
            environment_id=str(environment.id),
            correlation_id=correlation_id,
        )
        try:
            await publish_env_event(
                environment.id,
                event_type="STATUS_CHANGE",
                status=EnvironmentStatus.PROVISIONING.value,
                commit_sha=environment.latest_commit_sha,
                message="Retry provision queued",
                stage=ExecutionStage.INIT,
            )
        except Exception:
            logger.exception(
                "retry_status_publish_failed",
                environment_id=str(environment.id),
            )

        logger.info(
            "environment_retry_enqueued",
            environment_id=str(environment.id),
            correlation_id=correlation_id,
            owner_id=str(owner.id),
        )
        return self._to_read(environment)

    async def request_teardown(
        self,
        environment_id: UUID,
        *,
        owner: User,
        correlation_id: str,
    ) -> EnvironmentRead:
        environment = await self._require_owned(environment_id, owner)
        if environment.status in {
            EnvironmentStatus.TEARDOWN_PENDING,
            EnvironmentStatus.DESTROYED,
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "environment_already_tearing_down",
                    "message": "Environment is already tearing down or destroyed",
                },
            )
        if environment.status == EnvironmentStatus.PROVISIONING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "environment_still_provisioning",
                    "message": "Cannot teardown while provisioning is in progress",
                },
            )
        if await is_state_locked(environment_id, scope="environment"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "provisioning_in_progress",
                    "message": PROVISIONING_IN_PROGRESS_MESSAGE,
                },
            )

        await self._environments.update_status(environment, EnvironmentStatus.TEARDOWN_PENDING)
        await self._logs.create(
            environment_id=environment.id,
            message=f"Teardown requested (correlation_id={correlation_id})",
        )
        await AuditService(self._session).record(
            action=AuditAction.TEARDOWN_INITIATED,
            actor_id=AuditService.user_actor(owner.id),
            status=AuditStatus.PENDING,
            environment_id=environment.id,
            workspace_id=environment.workspace_id,
            commit_sha=environment.latest_commit_sha,
            detail=f"correlation_id={correlation_id}",
        )
        await self._session.commit()

        enqueue_teardown_environment(
            environment_id=str(environment.id),
            correlation_id=correlation_id,
        )
        return self._to_read(environment)

    async def list_logs(
        self,
        environment_id: UUID,
        owner: User,
    ) -> list[DeploymentLogRead]:
        await self._require_owned(environment_id, owner)
        rows = await self._logs.list_for_environment(environment_id)
        return [DeploymentLogRead.model_validate(row) for row in rows]

    async def pause_environment(
        self,
        environment_id: UUID,
        *,
        owner: User,
        correlation_id: str,
    ) -> EnvironmentRead:
        environment = await self._require_owned(environment_id, owner)
        if environment.status in {EnvironmentStatus.TEARDOWN_PENDING, EnvironmentStatus.DESTROYED}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "cannot_pause", "message": f"Cannot pause environment in status {environment.status}"},
            )
        if environment.status == EnvironmentStatus.PAUSED:
            return self._to_read(environment)

        provisioner = KubernetesProvisioner(settings=self._settings)
        provisioner.scale_deployment(namespace=environment.namespace_name, replicas=0)

        await self._environments.update_status(environment, EnvironmentStatus.PAUSED)
        await self._logs.create(
            environment_id=environment.id,
            log_level=LogLevel.INFO,
            stage=ExecutionStage.APPLY,
            message="Environment paused (scaled deployment replicas to 0).",
        )
        await AuditService(self._session).record(
            action=AuditAction.PAUSE_SUCCEEDED,
            actor_id=AuditService.user_actor(owner.id),
            status=AuditStatus.SUCCESS,
            environment_id=environment.id,
            workspace_id=environment.workspace_id,
            commit_sha=environment.latest_commit_sha,
            detail=f"correlation_id={correlation_id}",
        )
        await self._session.commit()
        return self._to_read(environment)

    async def resume_environment(
        self,
        environment_id: UUID,
        *,
        owner: User,
        correlation_id: str,
    ) -> EnvironmentRead:
        environment = await self._require_owned(environment_id, owner)
        if environment.status == EnvironmentStatus.RUNNING:
            return self._to_read(environment)

        await self._auto_pause_if_needed(owner, exclude_id=environment.id)

        provisioner = KubernetesProvisioner(settings=self._settings)
        provisioner.scale_deployment(namespace=environment.namespace_name, replicas=1)

        await self._environments.update_status(environment, EnvironmentStatus.RUNNING)
        await self._logs.create(
            environment_id=environment.id,
            log_level=LogLevel.INFO,
            stage=ExecutionStage.APPLY,
            message="Environment resumed (scaled deployment replicas to 1).",
        )
        await AuditService(self._session).record(
            action=AuditAction.RESUME_SUCCEEDED,
            actor_id=AuditService.user_actor(owner.id),
            status=AuditStatus.SUCCESS,
            environment_id=environment.id,
            workspace_id=environment.workspace_id,
            commit_sha=environment.latest_commit_sha,
            detail=f"correlation_id={correlation_id}",
        )
        await self._session.commit()
        return self._to_read(environment)

    async def _auto_pause_if_needed(self, owner: User, exclude_id: UUID | None = None) -> None:
        limit = self._settings.max_concurrent_environments
        rows = await self._environments.list_for_owner(owner.id)
        active_envs = [
            row
            for row in rows
            if row.status in {EnvironmentStatus.RUNNING, EnvironmentStatus.PROVISIONING}
            and row.id != exclude_id
        ]
        if len(active_envs) < limit:
            return

        active_envs.sort(key=lambda x: x.created_at)
        provisioner = KubernetesProvisioner(settings=self._settings)
        while len(active_envs) >= limit:
            oldest = active_envs.pop(0)
            provisioner.scale_deployment(namespace=oldest.namespace_name, replicas=0)
            await self._environments.update_status(oldest, EnvironmentStatus.PAUSED)
            await self._logs.create(
                environment_id=oldest.id,
                log_level=LogLevel.WARN,
                stage=ExecutionStage.APPLY,
                message=f"Environment auto-paused to enforce max active limit of {limit}.",
            )
            logger.info("environment_auto_paused", environment_id=str(oldest.id), limit=limit)

    async def _enforce_soft_cost_cap(
        self,
        owner: User,
        payload: EnvironmentCreate,
    ) -> None:
        provider = (payload.provider or "local").lower()
        if provider == "local":
            return
        if payload.cost_estimate_hourly is not None and payload.cost_estimate_hourly == 0:
            return

        rows = await self._environments.list_for_owner(owner.id)
        total = Decimal("0.0000")
        for row in rows:
            if row.status not in {
                EnvironmentStatus.RUNNING,
                EnvironmentStatus.PROVISIONING,
            }:
                continue
            if (row.provider or "local") == "local":
                continue
            total += self._to_read(row).cost_accrued

        cap = self._settings.preview_soft_cost_cap
        if total >= cap:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "soft_cost_cap_exceeded",
                    "message": (
                        f"Active cloud preview cost (${total}) meets soft cap (${cap}). "
                        "Destroy environments or raise PREVIEW_SOFT_COST_CAP."
                    ),
                    "details": {"accrued": str(total), "cap": str(cap)},
                },
            )

    def _to_read(
        self,
        environment,
        *,
        concurrent_active_count: int | None = None,
    ) -> EnvironmentRead:
        read = EnvironmentRead.model_validate(environment)
        base = self._settings.preview_public_base_url.rstrip("/")
        read.portal_url = f"{base}/p/{environment.id}"
        if environment.github_pr_number is not None:
            read.stable_pr_url = stable_pr_preview_url(
                environment.github_pr_number,
                settings=self._settings,
            )
        read.gitops_rebuild_enabled = bool((self._settings.webhook_secret or "").strip())
        read.app_ready = bool(environment.preview_url) and environment.status in {
            EnvironmentStatus.RUNNING,
            # Partial apply / Ready-timeout can leave the manifest image serving while
            # status is FAILED — still allow Open so users don't hit a different nginx port.
            EnvironmentStatus.FAILED,
        }
        read.is_local = (environment.provider or "local") == "local" or (
            environment.cost_estimate_hourly == 0 and environment.workspace_id is None
        )
        read.ttl_warning = (
            0 < read.time_remaining_seconds <= self._settings.ttl_warning_hours * 3600
            and environment.status == EnvironmentStatus.RUNNING
        )
        read.soft_cost_cap_exceeded = (
            not read.is_local and read.cost_accrued >= self._settings.preview_soft_cost_cap
        )
        read.max_concurrent_environments = self._settings.max_concurrent_environments
        read.concurrent_active_count = concurrent_active_count

        parts: list[str] = [f"ns={environment.namespace_name}"]
        image = environment.workload_image or self._settings.default_workload_image
        parts.append(f"image={image}")
        if environment.node_port is not None:
            parts.append(f"nodePort={environment.node_port}")
        if environment.provider:
            parts.append(f"provider={environment.provider}")
        if environment.deploy_mode:
            parts.append(f"deploy={environment.deploy_mode}")
        if environment.manifest_packaging:
            parts.append(f"packaging={environment.manifest_packaging}")
        read.runtime_summary = " · ".join(parts)
        return read

    async def _enrich_drift(
        self,
        reads: list[EnvironmentRead],
        environment_ids: list[UUID],
    ) -> list[EnvironmentRead]:
        if not self._settings.kubernetes_enabled:
            return reads
        audits = AuditService(self._session)
        for read, environment_id in zip(reads, environment_ids, strict=True):
            if read.status != EnvironmentStatus.RUNNING:
                read.drift_detected = False
                read.drift_summary = None
                continue
            read.drift_detected = await audits.has_unresolved_drift(environment_id)
            if read.drift_detected:
                latest = await audits.latest_for_environment(
                    environment_id,
                    AuditAction.DRIFT_DETECTED,
                )
                read.drift_summary = latest.detail if latest else None
            else:
                read.drift_summary = None
        return reads

    def _resolve_deploy_mode(
        self,
        payload: EnvironmentCreate,
        workspace,
        provisioning: ProvisioningService,
    ) -> tuple[DeployMode, str | None]:
        packaging = provisioning.get_workspace_kubernetes_packaging(workspace)
        if payload.deploy_mode == DeployMode.MANIFEST:
            if packaging not in {KubernetesPackaging.RAW_MANIFESTS, KubernetesPackaging.HELM}:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "manifest_unavailable",
                        "message": (
                            "Manifest deploy requires a workspace with either raw Kubernetes "
                            "manifests under infra/k8s/manifests/ or a Helm chart under "
                            "infra/helm/app-chart/"
                        ),
                    },
                )
            return DeployMode.MANIFEST, packaging.value
        if payload.deploy_mode == DeployMode.PREVIEW:
            return DeployMode.PREVIEW, packaging.value if packaging else None
        if packaging in {KubernetesPackaging.RAW_MANIFESTS, KubernetesPackaging.HELM}:
            return DeployMode.MANIFEST, packaging.value
        return DeployMode.PREVIEW, packaging.value if packaging else None

    def _workspace_git_source(self, workspace_id: UUID) -> tuple[str, str]:
        return f"https://launchpad.local/workspaces/{workspace_id}", "main"

    def _require_inline_cloud_credentials(self, payload: PreviewLaunchRequest) -> None:
        from app.core.secrets import has_aws_auth, has_gcp_auth

        creds = payload.credentials
        if payload.provider == PreviewProvider.GCP and not has_gcp_auth(creds):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "credentials_required",
                    "message": (
                        "GCP credentials required: paste a service account JSON, or configure "
                        "Workload Identity Federation (project number, pool, provider, SA email)"
                    ),
                },
            )
        if payload.provider == PreviewProvider.AWS and not has_aws_auth(creds):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "credentials_required",
                    "message": (
                        "AWS credentials required: access key + secret, or an IAM role ARN "
                        "for keyless OIDC web identity"
                    ),
                },
            )
        if payload.provider == PreviewProvider.AZURE and (
            not creds.azure_client_id
            or not creds.azure_client_secret
            or not creds.azure_tenant_id
            or not creds.azure_subscription_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "credentials_required",
                    "message": "Azure service principal fields are required",
                },
            )
        if payload.provider == PreviewProvider.CLOUDFLARE and not creds.cloudflare_api_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "credentials_required",
                    "message": "Cloudflare API token is required",
                },
            )

    def _cloud_config_for_provider(self, provider: CloudProvider):
        if provider == CloudProvider.GCP:
            return GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(project_id="launchpad-preview", vpc=True, subnets=True),
            )
        if provider == CloudProvider.AWS:
            return AwsCloudConfig(
                provider=CloudProvider.AWS,
                resources=AwsResources(vpc=True, subnets=True),
            )
        if provider == CloudProvider.AZURE:
            return AzureCloudConfig(
                provider=CloudProvider.AZURE,
                resources=AzureResources(resource_group="launchpad-preview", vnet=True, subnets=True),
            )
        return CloudflareCloudConfig(
            provider=CloudProvider.CLOUDFLARE,
            resources=CloudflareResources(account_id="00000000000000000000000000000000"),
        )

    async def _require_owned(self, environment_id: UUID, owner: User):
        return await self._require_access(environment_id, owner, mutate=True)

    async def _require_access(
        self,
        environment_id: UUID,
        owner: User,
        *,
        mutate: bool = False,
    ):
        environment = await self._environments.get_by_id(environment_id)
        if environment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "environment_not_found", "message": "Environment not found"},
            )
        if environment.owner_id == owner.id:
            return environment
        if environment.org_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "environment_not_found", "message": "Environment not found"},
            )
        orgs = OrganizationService(self._session)
        membership = await orgs.get_membership(org_id=environment.org_id, user_id=owner.id)
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "environment_not_found", "message": "Environment not found"},
            )
        if mutate and not (
            role_at_least(membership.role, OrgRole.ADMIN)
            or (
                role_at_least(membership.role, OrgRole.MEMBER)
                and environment.owner_id == owner.id
            )
        ):
            # Members can only mutate their own; viewers cannot mutate.
            # Keep 404 to avoid leaking existence across roles within shared orgs for viewers.
            if membership.role == OrgRole.VIEWER or environment.owner_id != owner.id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "environment_not_found", "message": "Environment not found"},
                )
        return environment

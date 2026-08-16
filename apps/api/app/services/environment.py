from __future__ import annotations

import asyncio
import json
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
    OrgPlan,
    EnvironmentStatus,
    ExecutionStage,
    LogLevel,
    OrgRole,
    Organization,
    ProvisioningWorkspace,
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
    WorkspaceWizardConfig,
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
from app.services.projects import ProjectService
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

_LEGACY_PLACEHOLDER_IMAGES = frozenset({"nginx:1.27-alpine", "app:latest", "app"})


def _is_placeholder_workload_image(
    image: str | None,
    *,
    default_image: str = "",
) -> bool:
    """True when the client did not supply a real override image."""
    value = (image or "").strip()
    if not value:
        return True
    lowered = value.lower()
    default = (default_image or "").strip().lower()
    if default and lowered == default:
        return True
    return lowered in _LEGACY_PLACEHOLDER_IMAGES


def _build_runtime_summary(
    *,
    namespace_name: str,
    workload_image: str | None,
    default_workload_image: str = "",
    node_port: int | None = None,
    provider: str | None = None,
    deploy_mode: str | None = None,
    manifest_packaging: str | None = None,
) -> str:
    """Compact header chips for the environment detail page."""
    parts: list[str] = [f"ns={namespace_name}"]
    stored = (workload_image or "").strip()
    if stored and not _is_placeholder_workload_image(
        stored,
        default_image=default_workload_image,
    ):
        parts.append(f"image={stored}")
    if node_port is not None:
        parts.append(f"nodePort={node_port}")
    if provider:
        parts.append(f"provider={provider}")
    if deploy_mode:
        parts.append(f"deploy={deploy_mode}")
    if manifest_packaging:
        parts.append(f"packaging={manifest_packaging}")
    return " · ".join(parts)


def _ttl_timedelta(*, ttl_hours: int | None, ttl_minutes: int | None) -> timedelta:
    if ttl_minutes is not None:
        return timedelta(minutes=ttl_minutes)
    return timedelta(hours=ttl_hours if ttl_hours is not None else 2)


def _ttl_label(*, ttl_hours: int | None, ttl_minutes: int | None) -> str:
    if ttl_minutes is not None:
        return f"{ttl_minutes}m"
    return f"{ttl_hours if ttl_hours is not None else 2}h"


def _ttl_is_past(expires_at: datetime | None, *, now: datetime | None = None) -> bool:
    if expires_at is None:
        return False
    cutoff = now or datetime.now(UTC)
    expires = expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires <= cutoff


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

    def _plan_concurrency_cap(self, plan: OrgPlan) -> int | None:
        if plan == OrgPlan.FREE:
            return self._settings.max_concurrent_environments
        return self._settings.max_concurrent_environments_pro

    async def _pause_expired_rows(self, rows: list) -> None:
        """Mark past-TTL environments EXPIRED (eager, no Celery required)."""
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
                logger.info("environment_ttl_eager_expired", environment_id=str(row.id))
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
        project_ids = {row.project_id for row in rows if row.project_id is not None}
        active_by_project: dict[UUID, int] = {}
        for pid in project_ids:
            active_by_project[pid] = await self._environments.count_active_for_owner_project(owner.id, pid)
        reads = [
            self._to_read(
                row,
                concurrent_active_count=(
                    active_by_project.get(row.project_id) if row.project_id is not None else None
                ),
            )
            for row in rows
        ]
        reads = await self._enrich_workspace_names(reads)
        return await self._enrich_drift(reads, [row.id for row in rows])

    async def get_environment(self, environment_id: UUID, owner: User) -> EnvironmentRead:
        environment = await self._require_access(environment_id, owner, mutate=False)
        await self._pause_expired_rows([environment])
        if environment.project_id is not None:
            active = await self._environments.count_active_for_owner_project(owner.id, environment.project_id)
        else:
            active = await self._environments.count_active_for_owner(owner.id)
        read = self._to_read(environment, concurrent_active_count=active)
        reads = await self._enrich_workspace_names([read])
        enriched = await self._enrich_drift(reads, [environment.id])
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
        from app.services.cost_metering import convert_display_cost

        cap_display = convert_display_cost(cap, settings=self._settings)
        total = (cloud_accrued + local_accrued).quantize(Decimal("0.0001"))
        return OrgCostSummary(
            org_id=ctx.org_id,
            soft_cost_cap=cap_display,
            active_count=active_count,
            cloud_environment_count=cloud_environment_count,
            cloud_accrued=cloud_accrued.quantize(Decimal("0.0001")),
            local_accrued=local_accrued.quantize(Decimal("0.0001")),
            total_accrued=total,
            soft_cost_cap_exceeded=cloud_accrued >= cap_display and cloud_environment_count > 0,
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
        workload_image = (self._settings.default_workload_image or "").strip() or None
        if payload.workspace_id is not None:
            git_repo_url, git_branch = await self._workspace_git_source(payload.workspace_id)
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
            if getattr(template, "enable_postgres", False):
                payload.enable_postgres = True
            if getattr(template, "enable_redis", False):
                payload.enable_redis = True
        elif payload.git_repo_url and payload.git_branch:
            git_repo_url = payload.git_repo_url
            git_branch = payload.git_branch
            template_id = None
            default_ttl = self._settings.default_ttl_hours
            cost_from_template = None
        else:
            # Local image-only preview (no catalog / git). Persist stable metadata placeholders.
            git_repo_url = "https://github.com/launchpad-idp/local-image-preview.git"
            git_branch = "main"
            template_id = None
            default_ttl = self._settings.default_ttl_hours
            cost_from_template = None
            if payload.workload_image:
                workload_image = payload.workload_image
        if payload.workload_image and not _is_placeholder_workload_image(
            payload.workload_image,
            default_image=self._settings.default_workload_image,
        ):
            workload_image = payload.workload_image

        if org_id is None:
            org = await OrganizationService(self._session).ensure_personal_org(owner)
            org_id = org.id

        existing = await self._environments.get_by_name(payload.name, org_id=org_id)
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
            # Cluster create (k3d) can take 1-2 min. Do not block POST /preview/launch;
            # the Celery worker calls ensure_kind_cluster before deploy. Fail-fast tool
            # checks stay on GET /preview/kind/status (Launch UI Refresh).
            cost_override = Decimal("0.0000")
            if payload.workspace_id is not None:
                await provisioning.get_workspace_for_owner(payload.workspace_id, owner)
                workspace_id = payload.workspace_id
        elif payload.workspace_id is not None:
            workspace = await provisioning.get_workspace_for_owner(payload.workspace_id, owner)
            workspace_id = payload.workspace_id
            if not workspace.encrypted_credentials:
                payload = payload.model_copy(
                    update={
                        "credentials": await provisioning.fill_cloud_credentials_from_account_vault(
                            payload.credentials,
                            owner,
                        )
                    }
                )
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

        ttl_hours = payload.ttl_hours
        ttl_minutes = payload.ttl_minutes
        max_total_hours = max(1, int(self._settings.ttl_max_total_hours_from_create))
        if ttl_hours is None and ttl_minutes is None:
            ttl_hours = min(int(default_ttl), max_total_hours)
        create_payload = EnvironmentCreate(
            name=payload.name,
            git_branch=git_branch,
            git_repo_url=git_repo_url,
            ttl_hours=ttl_hours,
            ttl_minutes=ttl_minutes,
            workspace_id=workspace_id,
            template_id=template_id,
            cost_estimate_hourly=cost_override,
            provider=payload.provider.value,
            workload_image=workload_image,
            github_pr_number=payload.github_pr_number,
            github_pr_url=payload.github_pr_url,
            deploy_mode=payload.deploy_mode,
            enable_postgres=payload.enable_postgres,
            enable_redis=payload.enable_redis,
            kubernetes_image_source=(
                payload.kubernetes_image_source.value
                if payload.kubernetes_image_source is not None
                else None
            ),
            kubernetes_image_scan_json=(
                payload.kubernetes_image_scan.model_dump_json()
                if getattr(payload, "kubernetes_image_scan", None) is not None
                else None
            ),
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
        now = datetime.now(UTC)
        if org_id is None:
            org = await OrganizationService(self._session).ensure_personal_org(owner)
            org_id = org.id
        else:
            org = await self._session.get(Organization, org_id)
            if org is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "org_not_found", "message": "Organization not found"},
                )

        existing = await self._environments.get_by_name(payload.name, org_id=org_id)

        cap = self._plan_concurrency_cap(org.plan)

        reuse_row = None
        if existing is not None:
            if existing.owner_id != owner.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "environment_exists",
                        "message": f"Environment '{payload.name}' already exists",
                    },
                )
            if existing.status in {EnvironmentStatus.EXPIRED, EnvironmentStatus.PAUSED} and _ttl_is_past(
                existing.ttl_expires_at, now=now
            ):
                reuse_row = existing
            else:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "environment_exists",
                        "message": f"Environment '{payload.name}' already exists",
                    },
                )

        project_id: UUID | None = None
        if payload.workspace_id is not None:
            provisioning = ProvisioningService(self._session)
            workspace = await provisioning.get_workspace_for_owner(payload.workspace_id, owner)
            project_id = workspace.project_id
            if project_id is None:
                project_id = (
                    await ProjectService(self._session, self._settings).ensure_default_project(org=org, actor=owner)
                ).id
            deploy_mode, manifest_packaging = self._resolve_deploy_mode(payload, workspace, provisioning)
        else:
            deploy_mode = payload.deploy_mode or DeployMode.PREVIEW
            manifest_packaging = None
            project_id = (
                await ProjectService(self._session, self._settings).ensure_default_project(org=org, actor=owner)
            ).id

        ttl_hours = payload.ttl_hours
        ttl_minutes = payload.ttl_minutes
        # Governance: cannot exceed ttl_max_total_hours_from_create from create time.
        max_total_hours = max(1, int(self._settings.ttl_max_total_hours_from_create))
        if ttl_hours is not None:
            ttl_hours = min(ttl_hours, max_total_hours)
        if ttl_minutes is not None:
            ttl_minutes = min(ttl_minutes, max_total_hours * 60)

        ttl_expires_at = now + _ttl_timedelta(ttl_hours=ttl_hours, ttl_minutes=ttl_minutes)
        placeholder_namespace = f"launchpad-env-pending-{payload.name}"[:253]

        await self._auto_pause_if_needed(owner, project_id=project_id, cap=cap, exclude_id=reuse_row.id if reuse_row else None)
        await self._enforce_soft_cost_cap(owner, payload)
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

        workload_image = (payload.workload_image or self._settings.default_workload_image or "").strip() or None
        if payload.template_id and _is_placeholder_workload_image(
            payload.workload_image,
            default_image=self._settings.default_workload_image,
        ):
            try:
                workload_image = get_preview_template(payload.template_id).workload_image
            except KeyError:
                pass

        # Workspace manifests are the source of truth for MANIFEST deploys, so
        # extract the real workload image (preferring the exposed preview-target
        # deployment) whenever the client did NOT supply a *custom* image.
        client_image = (payload.workload_image or "").strip()
        client_wants_default = _is_placeholder_workload_image(
            client_image,
            default_image=self._settings.default_workload_image,
        )
        if deploy_mode == DeployMode.MANIFEST and payload.workspace_id and client_wants_default:
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
            except Exception as exc:  # noqa: BLE001 - best-effort; falls back to default
                logger.warning(
                    "manifest_image_extract_failed",
                    workspace_id=str(payload.workspace_id),
                    error=str(exc),
                )

        if deploy_mode in {DeployMode.ATTACH, DeployMode.COMPOSE} and _is_placeholder_workload_image(
            workload_image,
            default_image=self._settings.default_workload_image,
        ):
            # Instance/compose without an explicit container image: do not persist the
            # platform default (e.g. nginx) as if it were the running workload.
            workload_image = None

        workload_image_for_log = workload_image or "from workspace manifests"
        if deploy_mode == DeployMode.MANIFEST and _is_placeholder_workload_image(
            payload.workload_image,
            default_image=self._settings.default_workload_image,
        ):
            # For MANIFEST deploys, the real image is resolved later from workspace manifests.
            workload_image_for_log = "from workspace manifests"
        elif deploy_mode in {DeployMode.ATTACH, DeployMode.COMPOSE} and not workload_image:
            workload_image_for_log = "linked repo / workspace"

        if reuse_row is not None:
            environment = reuse_row
            environment.project_id = project_id
            environment.org_id = org_id
            environment.workspace_id = payload.workspace_id
            environment.git_branch = payload.git_branch
            environment.git_repo_url = payload.git_repo_url
            environment.template_id = payload.template_id
            environment.provider = payload.provider
            environment.workload_image = workload_image
            environment.github_pr_number = payload.github_pr_number
            environment.github_pr_url = payload.github_pr_url
            environment.deploy_mode = deploy_mode.value
            environment.manifest_packaging = manifest_packaging
            environment.kubernetes_image_source = payload.kubernetes_image_source
            environment.kubernetes_image_scan_json = payload.kubernetes_image_scan_json
            environment.enable_postgres = payload.enable_postgres
            environment.enable_redis = payload.enable_redis
            environment.ttl_expires_at = ttl_expires_at
            environment.cost_estimate_hourly = hourly
            environment.cost_accrued = Decimal("0.0000")
            environment.cost_sampled_at = None
            environment.cost_source = None
            environment.error_message = None
            environment.failure_summary = None
            environment.seed_status = None
            environment.preview_url = None
            environment.preview_endpoints_json = None
            environment.node_port = None
            environment.latest_commit_sha = None
            environment.status = EnvironmentStatus.PROVISIONING
            await self._session.flush()
            await self._session.refresh(environment)
        else:
            environment = await self._environments.create(
                owner_id=owner.id,
                org_id=org_id,
                project_id=project_id,
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
                kubernetes_image_source=payload.kubernetes_image_source,
                kubernetes_image_scan_json=payload.kubernetes_image_scan_json,
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
                f"(TTL {_ttl_label(ttl_hours=ttl_hours, ttl_minutes=ttl_minutes)}, "
                f"image={workload_image_for_log}, "
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
        if project_id is not None:
            active = await self._environments.count_active_for_owner_project(owner.id, project_id)
        else:
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
        if environment.ttl_expires_at is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "ttl_disabled",
                    "message": "This environment has no TTL (staging/production permanent)",
                },
            )

        hours = payload.hours
        minutes = payload.minutes
        if hours is None and minutes is None:
            hours = self._settings.ttl_extend_hours_default
        extend_delta = (
            timedelta(minutes=minutes)
            if minutes is not None
            else timedelta(hours=hours if hours is not None else self._settings.ttl_extend_hours_default)
        )
        now = datetime.now(UTC)
        created = environment.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        expires = environment.ttl_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)

        base = max(expires, now)
        candidate = base + extend_delta
        max_total_hours = max(1, int(self._settings.ttl_max_total_hours_from_create))
        max_expiry = created + timedelta(hours=max_total_hours)
        if candidate > max_expiry:
            candidate = max_expiry
        if candidate <= expires:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "ttl_max_reached",
                    "message": (
                        f"Cannot extend beyond {max_total_hours}h from create time"
                    ),
                },
            )

        # Soft cost: refuse extend on cloud when accrued already over cap.
        if (environment.provider or "local") != "local":
            from app.services.cost_metering import convert_display_cost, display_cost_accrued

            accrued_usd = display_cost_accrued(
                cost_accrued=environment.cost_accrued or Decimal("0.0000"),
                cost_estimate_hourly=environment.cost_estimate_hourly,
                cost_sampled_at=environment.cost_sampled_at,
                created_at=environment.created_at,
                status=environment.status,
            )
            cap_display = convert_display_cost(
                self._settings.preview_soft_cost_cap,
                settings=self._settings,
            )
            accrued_display = convert_display_cost(accrued_usd, settings=self._settings)
            if accrued_display >= cap_display:
                currency = (self._settings.cost_display_currency or "USD").strip().upper()
                symbol = "€" if currency == "EUR" else "$"
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail={
                        "code": "soft_cost_cap_exceeded",
                        "message": (
                            f"Cost to date ({symbol}{accrued_display}) meets soft cap "
                            f"({symbol}{cap_display}). Destroy or wait."
                        ),
                    },
                )

        await self._environments.update_ttl(environment, candidate)
        extend_label = f"{minutes}m" if minutes is not None else f"{hours}h"
        await self._logs.create(
            environment_id=environment.id,
            message=(
                f"TTL extended by {extend_label} → {candidate.isoformat()} "
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
        org_id: UUID | None = None,
    ) -> EnvironmentRead:
        """Launch a new cloud preview from an existing environment's source."""
        from app.services.cloud_promote import needs_cloud_retarget, promote_cloud_deploy_mode

        source = await self._require_owned(environment_id, owner)
        provisioning = ProvisioningService(self._session)
        filled_credentials = await provisioning.fill_cloud_credentials_from_account_vault(
            payload.credentials,
            owner,
        )

        # If credentials are still incomplete after vault-fill, fail fast with a
        # structured 4xx instead of allowing later unhandled exceptions.
        from app.core.secrets import validate_cloud_credentials

        try:
            validate_cloud_credentials(payload.provider.value, filled_credentials)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "credentials_required",
                    "message": str(exc),
                },
            )

        suffix = payload.provider.value[:8]
        if payload.name is not None:
            name = payload.name
        else:
            # Promotion retries should be frictionless: if the user didn't
            # provide an explicit name, pick the next available candidate to
            # avoid environment_exists conflicts.
            base = f"{source.name}-{suffix}"[:64]
            name = base
            for i in range(1, 10):
                existing = await self._environments.get_by_name(name, org_id=org_id)
                if existing is None:
                    break
                # Keep within schema max length (64) and allowed characters.
                # Example: my-env-gcp -> my-env-gcp-1
                suffix_part = f"-{i}"
                name = f"{base[:64 - len(suffix_part)]}{suffix_part}"
                if len(name) < 3:
                    name = base[:3]

        target_provider = CloudProvider(payload.provider.value)
        retarget = needs_cloud_retarget(
            source_provider=source.provider,
            deploy_mode=source.deploy_mode,
        )
        cloud_deploy_mode = promote_cloud_deploy_mode(
            source.deploy_mode,
            retarget=retarget,
        )
        workspace_id = source.workspace_id

        if retarget and source.workspace_id is not None:
            workspace_id = await provisioning.clone_workspace_for_cloud_promote(
                source.workspace_id,
                owner=owner,
                org_id=org_id,
                target_provider=target_provider,
                credentials=filled_credentials,
                workspace_name=name,
                primary_service=payload.primary_service,
                code_source=payload.code_source,
                region=payload.region,
                create_vpc=payload.create_vpc,
                create_subnets=payload.create_subnets,
                existing_vpc_id=payload.existing_vpc_id,
                existing_security_group_id=payload.existing_security_group_id,
                project_id=source.project_id,
                image_scan=payload.kubernetes_image_scan,
            )
        elif retarget and source.workspace_id is None:
            from app.schemas.cloud import (
                InstanceCodeSource,
                ProvisioningWizardRequest,
                RunningInstanceKind,
                WorkspaceRuntimeMode,
            )
            from app.services.cloud_promote import (
                build_cloud_promote_wizard_request,
                build_cloud_running_instance,
            )

            resolved_code = (
                InstanceCodeSource(payload.code_source)
                if payload.code_source
                else InstanceCodeSource.SSH
            )
            stub = WorkspaceWizardConfig.model_validate(
                {
                    "name": name,
                    "iac_engine": IaCEngine.TERRAFORM.value,
                    "cloud": self._cloud_config_for_provider(target_provider).model_dump(
                        mode="json",
                    ),
                    "credentials": {},
                    "has_credentials": True,
                    "runtime_mode": WorkspaceRuntimeMode.RUNNING_INSTANCE.value,
                    "running_instance": build_cloud_running_instance(
                        provider=target_provider,
                        environment_name=name,
                        primary_service=payload.primary_service,
                        source=None,
                        target_kind=RunningInstanceKind.VM,
                        code_source=resolved_code,
                        region=payload.region,
                    ).model_dump(mode="json"),
                }
            )
            bundle_req = build_cloud_promote_wizard_request(
                stub,
                workspace_name=name,
                provider=target_provider,
                credentials=filled_credentials,
                primary_service=payload.primary_service,
                code_source=resolved_code,
                region=payload.region,
                create_vpc=payload.create_vpc,
                create_subnets=payload.create_subnets,
                existing_vpc_id=payload.existing_vpc_id,
                existing_security_group_id=payload.existing_security_group_id,
                image_scan=payload.kubernetes_image_scan,
            )
            bundle = await provisioning.generate_bundle(
                bundle_req,
                owner=owner,
                org_id=org_id,
                project_id=source.project_id,
            )
            workspace_id = UUID(bundle.workspace_id)

        if workspace_id is not None:
            launch = PreviewLaunchRequest(
                name=name,
                provider=payload.provider,
                credentials=filled_credentials,
                ttl_hours=payload.ttl_hours,
                ttl_minutes=payload.ttl_minutes,
                github_pr_number=source.github_pr_number,
                github_pr_url=source.github_pr_url,
                workspace_id=workspace_id,
                deploy_mode=cloud_deploy_mode,
                kubernetes_image_source=source.kubernetes_image_source,
                kubernetes_image_scan=payload.kubernetes_image_scan,
            )
        elif source.template_id:
            launch = PreviewLaunchRequest(
                name=name,
                template_id=source.template_id,
                provider=payload.provider,
                credentials=filled_credentials,
                ttl_hours=payload.ttl_hours,
                ttl_minutes=payload.ttl_minutes,
                github_pr_number=source.github_pr_number,
                github_pr_url=source.github_pr_url,
                deploy_mode=cloud_deploy_mode,
                kubernetes_image_scan=payload.kubernetes_image_scan,
            )
        else:
            launch = PreviewLaunchRequest(
                name=name,
                git_repo_url=source.git_repo_url,
                git_branch=source.git_branch,
                provider=payload.provider,
                credentials=filled_credentials,
                ttl_hours=payload.ttl_hours,
                ttl_minutes=payload.ttl_minutes,
                github_pr_number=source.github_pr_number,
                github_pr_url=source.github_pr_url,
                deploy_mode=cloud_deploy_mode,
                kubernetes_image_scan=payload.kubernetes_image_scan,
            )
        result = await self.launch_preview(
            launch,
            owner=owner,
            correlation_id=correlation_id,
            org_id=org_id,
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
                app_ready=False,
                error_message=None,
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

    async def cancel_provision(
        self,
        environment_id: UUID,
        *,
        owner: User,
        correlation_id: str,
    ) -> EnvironmentRead:
        """Stop an in-flight provision without tearing down resources.

        Flips ``PROVISIONING`` → ``FAILED`` so the Celery provision task aborts at
        its next cooperative checkpoint. Does **not** enqueue teardown; partial
        resources (if any) stay until the user destroys or retries.
        """
        environment = await self._require_owned(environment_id, owner)
        if environment.status != EnvironmentStatus.PROVISIONING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "cancel_not_allowed",
                    "message": "Only PROVISIONING environments can be stopped this way",
                },
            )

        stop_message = "Provisioning stopped by user"
        await self._environments.update_status(
            environment,
            EnvironmentStatus.FAILED,
            error_message=stop_message,
        )
        await self._logs.create(
            environment_id=environment.id,
            message=(
                f"Provisioning stopped by user (correlation_id={correlation_id}). "
                "No teardown was queued."
            ),
            log_level=LogLevel.WARN,
            stage=ExecutionStage.APPLY,
        )
        await AuditService(self._session).record(
            action=AuditAction.PROVISION_FAILED,
            actor_id=AuditService.user_actor(owner.id),
            status=AuditStatus.FAILURE,
            environment_id=environment.id,
            workspace_id=environment.workspace_id,
            commit_sha=environment.latest_commit_sha,
            detail=f"stopped correlation_id={correlation_id}",
        )
        await self._session.commit()

        try:
            await publish_env_event(
                environment.id,
                event_type="STATUS_CHANGE",
                status=EnvironmentStatus.FAILED.value,
                commit_sha=environment.latest_commit_sha,
                message=stop_message,
                stage=ExecutionStage.APPLY,
                app_ready=False,
                error_message=stop_message,
            )
        except Exception:
            logger.exception(
                "cancel_provision_status_publish_failed",
                environment_id=str(environment.id),
            )

        logger.info(
            "environment_provision_cancelled",
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
        force: bool = False,
    ) -> EnvironmentRead:
        """Request teardown of an environment.

        ``force=True`` allows teardown even while the environment is still
        ``PROVISIONING`` (or state-locked). Setting the status to
        ``TEARDOWN_PENDING`` doubles as a cooperative cancellation signal: the
        in-flight provision task re-checks the status at each stage and aborts,
        and the teardown task cleans up any stranded resources.
        """
        environment = await self._require_owned(environment_id, owner)
        if environment.status == EnvironmentStatus.DESTROYED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "environment_already_destroyed",
                    "message": "Environment is already destroyed",
                },
            )
        # A TEARDOWN_PENDING environment is allowed to be re-requested: a previous
        # teardown may have been dropped (worker restart) or crashed before it
        # reached DESTROYED. Re-enqueue is idempotent - the teardown task acquires
        # the state lock, so a genuinely in-flight teardown just no-ops.
        if environment.status == EnvironmentStatus.PROVISIONING and not force:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "environment_still_provisioning",
                    "message": (
                        "Cannot teardown while provisioning is in progress. "
                        "Use stop provisioning to cancel without teardown, "
                        "or retry with force=true to cancel and delete."
                    ),
                },
            )
        if not force and await is_state_locked(environment_id, scope="environment"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "provisioning_in_progress",
                    "message": PROVISIONING_IN_PROGRESS_MESSAGE,
                },
            )

        prior_status = environment.status
        from app.services.teardown_context import capture_environment_teardown_context

        await capture_environment_teardown_context(self._session, environment)
        await self._environments.update_status(environment, EnvironmentStatus.TEARDOWN_PENDING)
        await self._logs.create(
            environment_id=environment.id,
            message=(
                f"Force teardown requested during {prior_status.value} "
                f"(correlation_id={correlation_id})"
                if force
                else f"Teardown requested (correlation_id={correlation_id})"
            ),
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

        try:
            # Never let a slow Redis/Celery publish hang the HTTP delete.
            await asyncio.wait_for(
                asyncio.to_thread(
                    enqueue_teardown_environment,
                    environment_id=str(environment.id),
                    correlation_id=correlation_id,
                ),
                timeout=5.0,
            )
        except Exception:
            logger.exception(
                "teardown_enqueue_failed",
                environment_id=str(environment.id),
                correlation_id=correlation_id,
            )
            # Status is already TEARDOWN_PENDING; beat/requeue will pick it up.
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

        deploy_mode = (environment.deploy_mode or DeployMode.PREVIEW.value).lower()
        if deploy_mode == DeployMode.COMPOSE.value:
            from app.services.compose_deploy import stop_compose

            workspace_root = None
            if environment.workspace_id is not None:
                row = await self._session.get(ProvisioningWorkspace, environment.workspace_id)
                if row is not None and row.root_dir:
                    workspace_root = Path(row.root_dir)
            await asyncio.to_thread(
                stop_compose,
                workspace_root=workspace_root,
                namespace=environment.namespace_name,
                environment_id=str(environment.id),
            )
            pause_msg = "Environment paused (docker compose stop)."
        elif deploy_mode == DeployMode.ATTACH.value:
            from app.schemas.cloud import RunningInstanceKind, WorkspaceWizardConfig
            from app.services.provisioning import ProvisioningService

            kind = RunningInstanceKind.LOCAL_MACHINE
            if environment.workspace_id is not None:
                provisioning = ProvisioningService(self._session)
                row = await self._session.get(ProvisioningWorkspace, environment.workspace_id)
                if row is not None:
                    snapshot = provisioning._load_wizard_snapshot(row)
                    if snapshot is not None:
                        try:
                            wizard = WorkspaceWizardConfig.model_validate(
                                {**snapshot, "has_credentials": False}
                            )
                            kind = wizard.running_instance.kind
                        except Exception:
                            pass
            if kind == RunningInstanceKind.LOCAL_MACHINE:
                from app.schemas.cloud import RunningInstanceConfig
                from app.services.attach_deploy import teardown_attach

                await asyncio.to_thread(
                    teardown_attach,
                    running_instance=RunningInstanceConfig(kind=RunningInstanceKind.LOCAL_MACHINE),
                    namespace=environment.namespace_name,
                    environment_id=str(environment.id),
                )
                pause_msg = "Environment paused (stopped local instance container)."
            elif kind == RunningInstanceKind.VM:
                pause_msg = (
                    "Environment paused in control plane "
                    "(VM container left running; destroy to remove)."
                )
            else:
                pause_msg = (
                    "Environment paused in control plane "
                    "(serverless service left running externally)."
                )
        else:
            provisioner = KubernetesProvisioner(settings=self._settings)
            provisioner.scale_deployment(namespace=environment.namespace_name, replicas=0)
            pause_msg = "Environment paused (scaled deployment replicas to 0)."

        await self._environments.update_status(environment, EnvironmentStatus.PAUSED)
        await self._logs.create(
            environment_id=environment.id,
            log_level=LogLevel.INFO,
            stage=ExecutionStage.APPLY,
            message=pause_msg,
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

        if environment.status == EnvironmentStatus.EXPIRED or _ttl_is_past(
            environment.ttl_expires_at
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "ttl_expired",
                    "message": (
                        "This environment’s TTL has expired and cannot be resumed. "
                        "Destroy it or launch a new preview."
                    ),
                },
            )

        if environment.status != EnvironmentStatus.PAUSED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "cannot_resume",
                    "message": f"Cannot resume environment in status {environment.status.value}",
                },
            )

        # Free tier governance: pause oldest previews in the same project/user scope
        # so resume/relaunch can take a runtime slot.
        cap: int | None = None
        if environment.org_id is not None:
            org_row = await self._session.get(Organization, environment.org_id)
            if org_row is not None:
                cap = self._plan_concurrency_cap(org_row.plan)
        if cap is None and environment.org_id is None:
            org = await OrganizationService(self._session).ensure_personal_org(owner)
            cap = self._plan_concurrency_cap(org.plan)
        await self._auto_pause_if_needed(owner, project_id=environment.project_id, cap=cap, exclude_id=environment.id)

        deploy_mode = (environment.deploy_mode or DeployMode.PREVIEW.value).lower()
        if deploy_mode == DeployMode.COMPOSE.value:
            from app.services.compose_deploy import start_compose

            workspace_root = None
            if environment.workspace_id is not None:
                row = await self._session.get(ProvisioningWorkspace, environment.workspace_id)
                if row is not None and row.root_dir:
                    workspace_root = Path(row.root_dir)
            await asyncio.to_thread(
                start_compose,
                workspace_root=workspace_root,
                namespace=environment.namespace_name,
                environment_id=str(environment.id),
            )
            resume_msg = "Environment resumed (docker compose start)."
        elif deploy_mode == DeployMode.ATTACH.value:
            from app.schemas.cloud import RunningInstanceKind, WorkspaceWizardConfig
            from app.services.provisioning import ProvisioningService

            kind = RunningInstanceKind.LOCAL_MACHINE
            if environment.workspace_id is not None:
                provisioning = ProvisioningService(self._session)
                row = await self._session.get(ProvisioningWorkspace, environment.workspace_id)
                if row is not None:
                    snapshot = provisioning._load_wizard_snapshot(row)
                    if snapshot is not None:
                        try:
                            wizard = WorkspaceWizardConfig.model_validate(
                                {**snapshot, "has_credentials": False}
                            )
                            kind = wizard.running_instance.kind
                        except Exception:
                            pass
            if kind == RunningInstanceKind.LOCAL_MACHINE:
                from app.schemas.cloud import RunningInstanceConfig
                from app.services.attach_deploy import deploy_attach

                await asyncio.to_thread(
                    deploy_attach,
                    namespace=environment.namespace_name,
                    environment_id=str(environment.id),
                    name=environment.name,
                    git_branch=environment.git_branch,
                    git_repo_url=environment.git_repo_url,
                    ttl_expires_at=(
                        environment.ttl_expires_at.isoformat()
                        if environment.ttl_expires_at is not None
                        else datetime.now(UTC).isoformat()
                    ),
                    image=environment.workload_image,
                    running_instance=RunningInstanceConfig(
                        kind=RunningInstanceKind.LOCAL_MACHINE,
                        listen_port=environment.node_port or 8080,
                    ),
                    settings=self._settings,
                )
                resume_msg = "Environment resumed (restarted local instance container)."
            elif kind == RunningInstanceKind.VM:
                resume_msg = (
                    "Environment resumed in control plane "
                    "(VM assumed still reachable)."
                )
            else:
                resume_msg = (
                    "Environment resumed in control plane "
                    "(serverless service assumed still reachable)."
                )
        else:
            provisioner = KubernetesProvisioner(settings=self._settings)
            provisioner.scale_deployment(namespace=environment.namespace_name, replicas=1)
            resume_msg = "Environment resumed (scaled deployment replicas to 1)."

        await self._environments.update_status(environment, EnvironmentStatus.RUNNING)
        await self._logs.create(
            environment_id=environment.id,
            log_level=LogLevel.INFO,
            stage=ExecutionStage.APPLY,
            message=resume_msg,
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

    async def relaunch_environment(
        self,
        environment_id: UUID,
        *,
        owner: User,
        correlation_id: str,
    ) -> EnvironmentRead:
        """Relaunch an EXPIRED/ttl-past environment using the same configuration row."""
        environment = await self._require_owned(environment_id, owner)
        now = datetime.now(UTC)

        ttl_past = _ttl_is_past(environment.ttl_expires_at, now=now)
        if not ttl_past or environment.status not in {EnvironmentStatus.EXPIRED, EnvironmentStatus.PAUSED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "ttl_expired", "message": "Environment TTL is not eligible for relaunch"},
            )

        cap: int | None = None
        if environment.org_id is not None:
            org_row = await self._session.get(Organization, environment.org_id)
            if org_row is not None:
                cap = self._plan_concurrency_cap(org_row.plan)
        if cap is None and environment.org_id is None:
            org = await OrganizationService(self._session).ensure_personal_org(owner)
            cap = self._plan_concurrency_cap(org.plan)

        await self._auto_pause_if_needed(
            owner,
            project_id=environment.project_id,
            cap=cap,
            exclude_id=environment.id,
        )

        environment.status = EnvironmentStatus.PROVISIONING
        environment.error_message = None
        environment.preview_url = None
        environment.preview_endpoints_json = None
        environment.node_port = None
        environment.latest_commit_sha = None
        environment.created_at = now
        max_total_hours = max(1, int(self._settings.ttl_max_total_hours_from_create))
        environment.ttl_expires_at = now + _ttl_timedelta(
            ttl_hours=max_total_hours,
            ttl_minutes=None,
        )
        environment.cost_accrued = Decimal("0.0000")
        environment.cost_sampled_at = None
        environment.cost_source = None

        await self._logs.create(
            environment_id=environment.id,
            message=(
                "Relaunch queued after TTL expiry "
                f"(ttl cap {max_total_hours}h, "
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

        if environment.project_id is not None:
            active = await self._environments.count_active_for_owner_project(owner.id, environment.project_id)
        else:
            active = await self._environments.count_active_for_owner(owner.id)
        await self._session.refresh(environment)
        return self._to_read(environment, concurrent_active_count=active)

    async def _auto_pause_if_needed(
        self,
        owner: User,
        *,
        project_id: UUID | None,
        cap: int | None,
        exclude_id: UUID | None = None,
    ) -> None:
        if cap is None or project_id is None:
            return
        active_envs = await self._environments.list_active_for_owner_project(owner.id, project_id)
        active_envs = [row for row in active_envs if row.id != exclude_id]
        if len(active_envs) < cap:
            return
        provisioner = KubernetesProvisioner(settings=self._settings)
        while len(active_envs) >= cap:
            oldest = active_envs.pop(0)
            provisioner.scale_deployment(namespace=oldest.namespace_name, replicas=0)
            await self._environments.update_status(oldest, EnvironmentStatus.PAUSED)
            await self._logs.create(
                environment_id=oldest.id,
                log_level=LogLevel.WARN,
                stage=ExecutionStage.APPLY,
                message=f"Environment auto-paused to enforce plan max active limit of {cap} per project.",
            )
            logger.info("environment_auto_paused", environment_id=str(oldest.id), cap=cap)

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

        from app.services.cost_metering import display_cost_accrued

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
            total += display_cost_accrued(
                cost_accrued=row.cost_accrued or Decimal("0.0000"),
                cost_estimate_hourly=row.cost_estimate_hourly,
                cost_sampled_at=row.cost_sampled_at,
                created_at=row.created_at,
                status=row.status,
            )

        cap = self._settings.preview_soft_cost_cap
        if total >= cap:
            from app.services.cost_metering import convert_display_cost

            cap_display = convert_display_cost(cap, settings=self._settings)
            total_display = convert_display_cost(total, settings=self._settings)
            currency = (self._settings.cost_display_currency or "USD").strip().upper()
            symbol = "€" if currency == "EUR" else "$"
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "soft_cost_cap_exceeded",
                    "message": (
                        f"Active cloud preview cost ({symbol}{total_display}) meets soft cap "
                        f"({symbol}{cap_display}). Destroy environments or raise "
                        "PREVIEW_SOFT_COST_CAP."
                    ),
                    "details": {"accrued": str(total_display), "cap": str(cap_display)},
                },
            )

    def _to_read(
        self,
        environment,
        *,
        concurrent_active_count: int | None = None,
    ) -> EnvironmentRead:
        read = EnvironmentRead.model_validate(environment)
        from app.services.cost_metering import convert_display_cost

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
            # status is FAILED - still allow Open so users don't hit a different nginx port.
            EnvironmentStatus.FAILED,
        }
        read.is_local = (environment.provider or "local") == "local" or (
            environment.cost_estimate_hourly == 0 and environment.workspace_id is None
        )
        read.ttl_warning = (
            not read.ttl_disabled
            and 0 < read.time_remaining_seconds <= self._settings.ttl_warning_hours * 3600
            and environment.status == EnvironmentStatus.RUNNING
        )
        read.soft_cost_cap_exceeded = (
            not read.is_local
            and read.cost_accrued
            >= convert_display_cost(
                self._settings.preview_soft_cost_cap,
                settings=self._settings,
            )
        )
        read.max_concurrent_environments = self._settings.max_concurrent_environments
        read.concurrent_active_count = concurrent_active_count

        if _is_placeholder_workload_image(
            environment.workload_image,
            default_image=self._settings.default_workload_image,
        ):
            # Hide platform defaults / legacy placeholders from the UI (instance mode
            # often has no Docker image at all).
            read.workload_image = None

        read.runtime_summary = _build_runtime_summary(
            namespace_name=environment.namespace_name,
            workload_image=read.workload_image,
            default_workload_image=self._settings.default_workload_image,
            node_port=environment.node_port,
            provider=environment.provider,
            deploy_mode=environment.deploy_mode,
            manifest_packaging=environment.manifest_packaging,
        )
        stage = (getattr(environment, "lifecycle_stage", None) or "preview").lower()
        read.lifecycle_stage = stage
        read.promotion_lineage_id = getattr(environment, "promotion_lineage_id", None)
        read.promoted_from_id = getattr(environment, "promoted_from_id", None)
        promotable = environment.status in {
            EnvironmentStatus.RUNNING,
            EnvironmentStatus.FAILED,
        }
        read.can_promote_to_staging = promotable and stage == "preview"
        read.can_promote_to_production = promotable and stage in {"preview", "staging"}
        return read

    async def _enrich_workspace_names(
        self,
        reads: list[EnvironmentRead],
    ) -> list[EnvironmentRead]:
        ws_ids = {read.workspace_id for read in reads if read.workspace_id is not None}
        if not ws_ids:
            return reads
        from sqlalchemy import select

        result = await self._session.execute(
            select(ProvisioningWorkspace.id, ProvisioningWorkspace.name).where(
                ProvisioningWorkspace.id.in_(ws_ids)
            )
        )
        names = {row.id: row.name for row in result.all()}
        for read in reads:
            if read.workspace_id is not None:
                read.workspace_name = names.get(read.workspace_id)
        return reads

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
        from app.services.preview_deploy_plan import resolve_preview_deploy_plan

        config = None
        try:
            snapshot = provisioning._load_wizard_snapshot(workspace)
            if snapshot is not None:
                from app.schemas.cloud import WorkspaceWizardConfig

                config = WorkspaceWizardConfig.model_validate(
                    {**snapshot, "has_credentials": False}
                )
        except Exception:
            config = None

        if config is not None:
            plan = resolve_preview_deploy_plan(
                config,
                requested_deploy_mode=payload.deploy_mode,
            )
            # Merge smart dependency defaults when the client left them off.
            if not payload.enable_postgres and plan.enable_postgres:
                payload.enable_postgres = True
            if not payload.enable_redis and plan.enable_redis:
                payload.enable_redis = True
            if plan.deploy_mode == DeployMode.MANIFEST:
                packaging = provisioning.get_workspace_kubernetes_packaging(workspace)
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
            return plan.deploy_mode, plan.manifest_packaging

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
        if payload.deploy_mode in {DeployMode.COMPOSE, DeployMode.ATTACH}:
            return payload.deploy_mode, packaging.value if packaging else None
        if packaging in {KubernetesPackaging.RAW_MANIFESTS, KubernetesPackaging.HELM}:
            return DeployMode.MANIFEST, packaging.value
        return DeployMode.PREVIEW, packaging.value if packaging else None

    async def _workspace_git_source(self, workspace_id: UUID) -> tuple[str, str]:
        """Resolve upstream repo+branch for workspace-linked previews.

        GitOps rebuild matching (webhooks) relies on Environment.git_repo_url and
        Environment.git_branch. For repo_import workspaces, those values are stored
        in the workspace's `wizard_config_json`, so we extract them here instead of
        hardcoding a launchpad.local URL.
        """
        from app.models.domain import ProvisioningWorkspace

        row = await self._session.get(ProvisioningWorkspace, workspace_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "workspace_not_found", "message": "Workspace not found"},
            )

        try:
            raw = row.wizard_config_json or ""
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {}

        if not isinstance(payload, dict):
            payload = {}

        git_repo_url = str(payload.get("git_repo_url") or "").strip()
        git_branch = str(payload.get("git_branch") or "").strip() or "main"

        linked = payload.get("linked_app_repo")
        if isinstance(linked, dict):
            linked_url = str(linked.get("git_repo_url") or "").strip()
            linked_branch = str(linked.get("git_branch") or "").strip()
            if linked_url:
                git_repo_url = linked_url
            if linked_branch:
                git_branch = linked_branch

        if not git_repo_url:
            # Backward-compatible fallback: previews still deploy, but push-based
            # webhook matching can only work when the workspace includes repo_import
            # wizard metadata (git_repo_url/git_branch).
            return f"https://launchpad.local/workspaces/{workspace_id}", "main"

        return git_repo_url, git_branch

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
        from app.schemas.cloud import RunningInstanceKind
        from app.services.cloud_promote import cloud_config_for_promote

        return cloud_config_for_promote(
            provider,
            CloudCredentials(),
            target_kind=RunningInstanceKind.VM,
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

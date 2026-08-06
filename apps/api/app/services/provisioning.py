from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

import yaml
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.secrets import decrypt_secret, encrypt_secret, project_id_from_gcp_sa_json
from app.services.gcp_api_enablement import (
    GcpApiEnablementError,
    enable_gcp_apis,
)
from app.services.terraform_bundle import _apis_tf, gcp_required_apis
from app.models.domain import ProvisioningWorkspace, TerminalSessionRecord, User
from app.schemas.cloud import (
    AwsCloudConfig,
    AwsResources,
    AzureCloudConfig,
    AzureResources,
    CloudflareCloudConfig,
    CloudflareResources,
    CloudCredentials,
    CloudProvider,
    GcpCloudConfig,
    GcpResources,
    GitHubRepoRequest,
    GitHubRepoResult,
    IaCBundleSummary,
    IaCEngine,
    KubernetesPackaging,
    KubernetesWorkloadOptions,
    LocalCloudConfig,
    LocalResources,
    ProvisioningWizardRequest,
    WorkspaceFileContent,
    WorkspaceFileNode,
    WorkspaceFormatResponse,
    WorkspaceListItem,
    WorkspacePushRequest,
    WorkspaceTemplateApplyRequest,
    WorkspaceTemplateInfo,
    WorkspaceWizardConfig,
    WorkspaceArtifactsMode,
    GcpApiEnablementResponse,
)
from app.services.github_service import GitHubProvisioningService
from app.services.iac_generator import IaCGenerator
from app.services.sandbox_runner import SandboxSession, build_provision_bootstrap, get_sandbox_runner
from app.services import workspace_files as ws_files
from app.services.kind_cluster import delete_kind_cluster, ensure_kind_cluster
from app.services.state_lock import (
    PROVISIONING_IN_PROGRESS_MESSAGE,
    is_state_locked,
)
from app.services.workspace_templates import get_template, list_templates

logger = get_logger(__name__)


class ProvisioningService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._iac = IaCGenerator()
        self._github = GitHubProvisioningService(self._iac)
        self._sandbox = get_sandbox_runner()

    async def generate_bundle(
        self,
        request: ProvisioningWizardRequest,
        *,
        owner: User,
        org_id: UUID | None = None,
    ) -> IaCBundleSummary:
        if isinstance(request.cloud, LocalCloudConfig):
            try:
                await ensure_kind_cluster(cluster_name=request.cloud.resources.cluster_name)
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "kind_cluster_unavailable",
                        "message": str(exc),
                    },
                ) from exc

        request = await self._with_account_credentials(request, owner)
        request = self._with_gcp_project_from_sa(request)
        bundle = self._iac.generate(request)
        encrypted = encrypt_secret(request.credentials.model_dump_json())
        from app.services.orgs import OrganizationService

        personal = await OrganizationService(self._session).ensure_personal_org(owner)
        row = ProvisioningWorkspace(
            id=UUID(bundle.workspace_id),
            owner_id=owner.id,
            org_id=org_id or personal.id,
            name=request.name,
            engine=bundle.engine.value,
            provider=bundle.provider.value,
            root_dir=bundle.root_dir,
            status="ready",
            encrypted_credentials=encrypted,
            wizard_config_json=self._wizard_config_json(request),
        )
        self._session.add(row)
        await self._session.commit()
        logger.info(
            "provisioning_workspace_persisted",
            workspace_id=bundle.workspace_id,
            provider=bundle.provider.value,
            engine=bundle.engine.value,
            owner_id=str(owner.id),
        )
        return IaCBundleSummary(
            workspace_id=bundle.workspace_id,
            engine=bundle.engine,
            provider=bundle.provider,
            root_dir=bundle.root_dir,
            files=bundle.files,
            artifact_mode=request.artifact_mode,
            name=request.name,
            status="ready",
            created_at=datetime.now().astimezone(),
        )

    async def list_workspaces(
        self,
        owner: User,
        *,
        org_id: UUID | None = None,
    ) -> list[WorkspaceListItem]:
        from app.services.orgs import OrganizationService

        orgs = OrganizationService(self._session)
        if org_id is not None:
            ctx = await orgs.resolve_context(user=owner, org_id=org_id)
            target_org_id = ctx.org_id
            result = await self._session.execute(
                select(ProvisioningWorkspace)
                .where(ProvisioningWorkspace.org_id == target_org_id)
                .order_by(ProvisioningWorkspace.created_at.desc())
                .limit(100)
            )
            rows = list(result.scalars().all())
        else:
            org = await orgs.ensure_personal_org(owner)
            result = await self._session.execute(
                select(ProvisioningWorkspace)
                .where(ProvisioningWorkspace.org_id == org.id)
                .order_by(ProvisioningWorkspace.created_at.desc())
                .limit(100)
            )
            rows = list(result.scalars().all())
            if not rows:
                result = await self._session.execute(
                    select(ProvisioningWorkspace)
                    .where(ProvisioningWorkspace.owner_id == owner.id)
                    .order_by(ProvisioningWorkspace.created_at.desc())
                    .limit(100)
                )
                rows = list(result.scalars().all())
        items: list[WorkspaceListItem] = []
        for row in rows:
            items.append(
                WorkspaceListItem(
                    id=row.id,
                    name=row.name,
                    engine=row.engine,
                    provider=row.provider,
                    status=row.status,
                    artifact_mode=self.get_workspace_artifact_mode(row),
                    created_at=row.created_at,
                    root_dir=row.root_dir,
                )
            )
        return items

    async def get_workspace(self, workspace_id: UUID) -> ProvisioningWorkspace:
        row = await self._session.get(ProvisioningWorkspace, workspace_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "workspace_not_found",
                    "message": "Provisioning workspace not found",
                },
            )
        return row

    async def get_workspace_for_owner(
        self,
        workspace_id: UUID,
        owner: User,
    ) -> ProvisioningWorkspace:
        row = await self.get_workspace(workspace_id)
        if row.owner_id == owner.id:
            return row
        if row.org_id is not None:
            from app.services.orgs import OrganizationService

            membership = await OrganizationService(self._session).get_membership(
                org_id=row.org_id,
                user_id=owner.id,
            )
            if membership is not None:
                return row
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "workspace_not_found",
                "message": "Provisioning workspace not found",
            },
        )

    async def get_bundle_summary(
        self,
        workspace_id: UUID,
        owner: User,
    ) -> IaCBundleSummary:
        row = await self.get_workspace_for_owner(workspace_id, owner)
        root = Path(row.root_dir)
        files: list[str] = []
        if root.exists():
            files = sorted(
                str(p.relative_to(root)).replace("\\", "/")
                for p in root.rglob("*")
                if p.is_file() and not ws_files.is_denied_workspace_path(p.relative_to(root))
            )
        return IaCBundleSummary(
            workspace_id=str(row.id),
            engine=IaCEngine(row.engine),
            provider=CloudProvider(row.provider),
            root_dir=row.root_dir,
            files=files,
            artifact_mode=self.get_workspace_artifact_mode(row),
            name=row.name,
            status=row.status,
            created_at=row.created_at,
        )

    @staticmethod
    def _credential_display_label(
        encrypted: str | None,
        provider: str,
    ) -> str | None:
        """Derive a safe UI label from stored credentials (never return secret material)."""
        if not encrypted:
            return None
        try:
            creds = CloudCredentials.model_validate_json(decrypt_secret(encrypted))
        except Exception:
            return "Stored cloud credentials"

        if provider == CloudProvider.GCP.value:
            if creds.gcp_wif_pool_id and creds.gcp_wif_provider_id:
                return f"GCP Keyless OIDC (WIF pool: {creds.gcp_wif_pool_id})"
            if creds.gcp_sa_key_json:
                try:
                    data = json.loads(creds.gcp_sa_key_json)
                    email = data.get("client_email")
                    if isinstance(email, str) and email.strip():
                        return email.strip()
                    project = data.get("project_id")
                    if isinstance(project, str) and project.strip():
                        return f"GCP service account ({project.strip()})"
                except Exception:
                    pass
                return "GCP service account key"

        if provider == CloudProvider.AWS.value:
            if creds.aws_role_arn:
                return f"AWS Keyless OIDC ({creds.aws_role_arn})"
            if creds.aws_access_key_id:
                key = creds.aws_access_key_id.strip()
                suffix = key[-4:] if len(key) >= 4 else key
                return f"AWS access key ···{suffix}"

        if provider == CloudProvider.AZURE.value and (
            creds.azure_client_id or creds.azure_subscription_id
        ):
            client = (creds.azure_client_id or "").strip()
            if len(client) >= 8:
                return f"Azure app ···{client[-4:]}"
            sub = (creds.azure_subscription_id or "").strip()
            if len(sub) >= 8:
                return f"Azure subscription ···{sub[-4:]}"
            return "Azure service principal"

        if provider == CloudProvider.CLOUDFLARE.value and creds.cloudflare_api_token:
            return "Cloudflare API token"

        if any(
            getattr(creds, field, None)
            for field in (
                "gcp_sa_key_json",
                "gcp_wif_pool_id",
                "aws_access_key_id",
                "aws_secret_access_key",
                "aws_role_arn",
                "azure_client_id",
                "azure_client_secret",
                "cloudflare_api_token",
            )
        ):
            return "Stored cloud credentials"
        return None


    async def get_wizard_config(
        self,
        workspace_id: UUID,
        owner: User,
    ) -> WorkspaceWizardConfig:
        row = await self.get_workspace_for_owner(workspace_id, owner)
        has_credentials = bool(row.encrypted_credentials)
        credential_label = self._credential_display_label(
            row.encrypted_credentials,
            row.provider,
        )
        # Empty encrypted blob still counts as "has credentials" only if decrypt yields usable keys.
        if has_credentials and credential_label is None:
            has_credentials = False

        snapshot = self._load_wizard_snapshot(row)
        if snapshot is not None:
            try:
                config = WorkspaceWizardConfig.model_validate(
                    {
                        **snapshot,
                        "has_credentials": has_credentials,
                        "credential_label": credential_label,
                    }
                )
                return config
            except Exception:
                logger.warning(
                    "wizard_snapshot_invalid",
                    workspace_id=str(workspace_id),
                )

        provider = CloudProvider(row.provider)
        engine = IaCEngine(row.engine)
        return WorkspaceWizardConfig(
            name=row.name,
            iac_engine=engine,
            cloud=_default_cloud_for_provider(provider),
            run_init=True,
            artifact_mode=(
                WorkspaceArtifactsMode.MANIFEST_ONLY
                if provider == CloudProvider.LOCAL
                else WorkspaceArtifactsMode.IAC_ONLY
            ),
            kubernetes_packaging=(
                KubernetesPackaging.RAW_MANIFESTS
                if provider == CloudProvider.LOCAL
                else KubernetesPackaging.NONE
            ),
            kubernetes_options=KubernetesWorkloadOptions(),
            has_credentials=has_credentials,
            credential_label=credential_label,
        )

    async def enable_required_cloud_apis(
        self,
        workspace_id: UUID,
        owner: User,
    ) -> GcpApiEnablementResponse:
        """Enable required cloud APIs (GCP Service Usage) before Terraform provision."""
        row = await self.get_workspace_for_owner(workspace_id, owner)
        config = await self.get_wizard_config(workspace_id, owner)
        if not isinstance(config.cloud, GcpCloudConfig):
            return GcpApiEnablementResponse(
                project_id="",
                message="No GCP APIs to enable for this provider",
            )

        raw = row.encrypted_credentials
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "credentials_required",
                    "message": "Save GCP credentials (WIF or service account JSON) before enabling APIs",
                },
            )
        credentials = CloudCredentials.model_validate_json(decrypt_secret(raw))
        sa_json = credentials.gcp_sa_key_json
        if not sa_json or not sa_json.strip():
            from app.core.secrets import gcp_wif_complete

            if gcp_wif_complete(credentials):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "credentials_required",
                        "message": (
                            "Keyless WIF is configured for the sandbox, but the control-plane "
                            "'enable APIs' step still needs a one-time GCP service account JSON "
                            "(or enable APIs manually / via terraform apply with WIF)."
                        ),
                    },
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "credentials_required",
                    "message": "GCP service account JSON is missing from stored credentials",
                },
            )

        project_id = (
            project_id_from_gcp_sa_json(sa_json)
            or config.cloud.resources.project_id
        )
        apis = gcp_required_apis(config.cloud.resources)

        # Keep apis.tf in sync so later terraform apply can manage the same set.
        try:
            workspace_path = self._workspace_root(row)
            apis_tf = _apis_tf(config.cloud)
            if apis_tf:
                target = workspace_path / "infra" / "terraform" / "apis.tf"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(apis_tf, encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "gcp_apis_tf_write_failed",
                workspace_id=str(workspace_id),
                error=str(exc),
            )

        try:
            result = await asyncio.to_thread(
                enable_gcp_apis,
                sa_json=sa_json,
                project_id=project_id,
                apis=apis,
            )
        except GcpApiEnablementError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "gcp_api_enable_failed", "message": str(exc)},
            ) from exc

        newly = result.newly_enabled
        already = result.already_enabled
        if newly:
            message = (
                f"Enabled {len(newly)} API(s) on {result.project_id} "
                f"(waited {result.waited_seconds}s). "
                f"{len(already)} already enabled."
            )
        else:
            message = (
                f"All {len(already)} required API(s) already enabled on {result.project_id}."
            )
        return GcpApiEnablementResponse(
            project_id=result.project_id,
            required=result.required,
            already_enabled=already,
            newly_enabled=newly,
            waited_seconds=result.waited_seconds,
            message=message,
        )

    async def update_workspace(
        self,
        workspace_id: UUID,
        request: ProvisioningWizardRequest,
        *,
        owner: User,
    ) -> IaCBundleSummary:
        row = await self.get_workspace_for_owner(workspace_id, owner)
        if request.name != row.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "workspace_name_immutable",
                    "message": "Workspace name cannot be changed after creation",
                },
            )

        was_local = row.provider == CloudProvider.LOCAL.value
        is_local = isinstance(request.cloud, LocalCloudConfig)

        if is_local:
            try:
                await ensure_kind_cluster(cluster_name=request.cloud.resources.cluster_name)
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "kind_cluster_unavailable",
                        "message": str(exc),
                    },
                ) from exc

        workspace_path = self._workspace_root(row)
        try:
            workspace_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "workspace_path_unavailable",
                    "message": "Unable to prepare workspace directory for regeneration",
                },
            ) from exc

        merged = self._merge_credentials(row.encrypted_credentials, request.credentials)
        merged = await self._fill_from_account_vault(merged, owner.id)
        effective = request.model_copy(update={"credentials": merged})
        effective = self._with_gcp_project_from_sa(effective)
        files = self._iac.regenerate(workspace_path, effective)

        row.engine = effective.iac_engine.value
        row.provider = effective.cloud.provider.value
        row.encrypted_credentials = encrypt_secret(merged.model_dump_json())
        row.wizard_config_json = self._wizard_config_json(effective)
        row.status = "ready"
        await self._session.commit()

        if was_local and not is_local:
            await self._maybe_teardown_kind(owner)

        logger.info(
            "provisioning_workspace_updated",
            workspace_id=str(workspace_id),
            provider=row.provider,
            engine=row.engine,
        )
        return IaCBundleSummary(
            workspace_id=str(row.id),
            engine=effective.iac_engine,
            provider=effective.cloud.provider,
            root_dir=row.root_dir,
            files=files,
            artifact_mode=effective.artifact_mode,
            name=row.name,
            status=row.status,
            created_at=row.created_at,
        )

    @staticmethod
    def _with_gcp_project_from_sa(
        request: ProvisioningWizardRequest,
    ) -> ProvisioningWizardRequest:
        """Prefer project_id from the GCP service-account JSON over the wizard form value."""
        if not isinstance(request.cloud, GcpCloudConfig):
            return request
        project_id = project_id_from_gcp_sa_json(request.credentials.gcp_sa_key_json)
        if not project_id or project_id == request.cloud.resources.project_id:
            return request
        resources = request.cloud.resources.model_copy(update={"project_id": project_id})
        cloud = request.cloud.model_copy(update={"resources": resources})
        logger.info(
            "gcp_project_id_synced_from_sa",
            project_id=project_id,
            previous=request.cloud.resources.project_id,
        )
        return request.model_copy(update={"cloud": cloud})

    @staticmethod
    def _merge_credentials(
        encrypted: str | None,
        incoming: CloudCredentials,
    ) -> CloudCredentials:
        if encrypted:
            existing = CloudCredentials.model_validate_json(decrypt_secret(encrypted))
        else:
            existing = CloudCredentials()
        updates = {
            key: value
            for key, value in incoming.model_dump().items()
            if value is not None and str(value).strip()
        }
        return existing.model_copy(update=updates)

    async def _with_account_credentials(
        self,
        request: ProvisioningWizardRequest,
        owner: User,
    ) -> ProvisioningWizardRequest:
        filled = await self._fill_from_account_vault(request.credentials, owner.id)
        if filled == request.credentials:
            return request
        return request.model_copy(update={"credentials": filled})

    async def _fill_from_account_vault(
        self,
        credentials: CloudCredentials,
        user_id: UUID,
    ) -> CloudCredentials:
        """Fill blank credential fields from the user's account vault."""
        from app.core.secrets import has_aws_auth, has_gcp_auth
        from app.services.user_credentials import UserCloudCredentialsService

        vault = await UserCloudCredentialsService(self._session).get_credentials(user_id)
        updates: dict[str, str | None] = {}
        data = credentials.model_dump()
        vault_data = vault.model_dump()

        gcp_ok = has_gcp_auth(credentials)
        aws_ok = has_aws_auth(credentials)
        azure_ok = bool(
            credentials.azure_client_id
            and credentials.azure_client_secret
            and credentials.azure_tenant_id
            and credentials.azure_subscription_id
        )
        cf_ok = bool(credentials.cloudflare_api_token)

        for key, value in vault_data.items():
            if not value or not str(value).strip():
                continue
            if key.startswith("gcp_") and gcp_ok:
                continue
            if key.startswith("aws_") and aws_ok:
                continue
            if key.startswith("azure_") and azure_ok:
                continue
            if key.startswith("cloudflare_") and cf_ok:
                continue
            if not data.get(key):
                updates[key] = value
        return credentials.model_copy(update=updates) if updates else credentials

    async def destroy_workspace(self, workspace_id: UUID, owner: User) -> None:
        row = await self.get_workspace_for_owner(workspace_id, owner)
        was_local = row.provider == CloudProvider.LOCAL.value

        result = await self._session.execute(
            select(TerminalSessionRecord).where(
                TerminalSessionRecord.workspace_id == workspace_id,
                TerminalSessionRecord.status == "active",
            )
        )
        for record in result.scalars().all():
            await self._sandbox.kill(str(record.id))
            record.status = "destroyed"

        # Collect Docker tags before cascading teardown / deleting the workspace
        # tree (image-builds.json and Dockerfiles would otherwise be gone).
        from app.models.domain import Environment
        from app.services.image_cleanup import (
            collect_workspace_destroy_images,
            remove_local_docker_images,
            resolve_local_cluster_short_name,
        )

        env_rows = (
            await self._session.execute(
                select(Environment).where(Environment.workspace_id == workspace_id)
            )
        ).scalars().all()
        destroy_images = collect_workspace_destroy_images(
            row.root_dir,
            workload_images=[
                env.workload_image for env in env_rows if env.workload_image
            ],
        )

        # Cascade: force-tear-down every Launch Preview tied to this workspace so
        # its namespace, ingress, PVCs, secrets and kind images are reclaimed
        # (the Environment.workspace_id FK is SET NULL, which would otherwise
        # orphan running preview namespaces after the workspace row is deleted).
        await self._teardown_workspace_previews(workspace_id)

        # Remove linked "Your services" catalog entries so they don't linger
        # after the workspace (and its golden-path scaffold) is gone.
        from app.services.catalog import CatalogServiceManager

        removed = await CatalogServiceManager(self._session).delete_services_for_workspace(
            workspace_id
        )
        if removed:
            logger.info(
                "workspace_catalog_services_cascaded",
                workspace_id=str(workspace_id),
                count=removed,
            )

        if destroy_images:
            try:
                remove_local_docker_images(
                    destroy_images,
                    cluster_name=resolve_local_cluster_short_name(),
                    remove_from_cluster=was_local,
                )
            except Exception:
                logger.exception(
                    "workspace_image_cleanup_failed",
                    workspace_id=str(workspace_id),
                )

        self._iac.destroy_workspace(row.root_dir)
        await self._session.delete(row)
        await self._session.commit()
        logger.info("provisioning_workspace_destroyed", workspace_id=str(workspace_id))

        if was_local:
            await self._maybe_teardown_kind(owner)

    async def _teardown_workspace_previews(self, workspace_id: UUID) -> None:
        """Force teardown of all preview environments belonging to a workspace.

        Each non-destroyed environment is marked TEARDOWN_PENDING (which also
        cancels any in-flight provision) and its teardown task is enqueued -
        reusing the full teardown path (namespace + kind image cleanup + audit).
        """
        from app.models.domain import Environment, EnvironmentStatus
        from app.repositories.environment import EnvironmentRepository
        from app.workers.tasks import enqueue_teardown_environment

        result = await self._session.execute(
            select(Environment).where(Environment.workspace_id == workspace_id)
        )
        environments = result.scalars().all()
        active = [
            env
            for env in environments
            if env.status not in {EnvironmentStatus.DESTROYED, EnvironmentStatus.TEARDOWN_PENDING}
        ]
        if not active:
            return
        env_repo = EnvironmentRepository(self._session)
        for env in active:
            await env_repo.update_status(env, EnvironmentStatus.TEARDOWN_PENDING)
        await self._session.commit()
        for env in active:
            enqueue_teardown_environment(
                environment_id=str(env.id),
                correlation_id=f"workspace-destroy:{workspace_id}",
            )
        logger.info(
            "workspace_preview_teardown_cascaded",
            workspace_id=str(workspace_id),
            environment_count=len(active),
        )

    async def _maybe_teardown_kind(self, owner: User) -> None:
        """Delete the kind cluster when no Dev (kind) workspaces remain for this owner."""
        remaining = await self._session.execute(
            select(ProvisioningWorkspace.id)
            .where(
                ProvisioningWorkspace.owner_id == owner.id,
                ProvisioningWorkspace.provider == CloudProvider.LOCAL.value,
            )
            .limit(1)
        )
        if remaining.scalar_one_or_none() is not None:
            logger.info("kind_cluster_retained", reason="other_local_workspaces")
            return
        try:
            await delete_kind_cluster()
        except RuntimeError as exc:
            # Destroy already succeeded; surface as warning rather than failing the API call.
            logger.warning("kind_cluster_down_after_destroy_failed", error=str(exc))

    async def open_terminal(
        self,
        workspace_id: UUID,
        *,
        owner: User,
        cols: int = 120,
        rows: int = 40,
        run_init: bool = True,
    ) -> SandboxSession:
        workspace = await self.get_workspace_for_owner(workspace_id, owner)
        raw = workspace.encrypted_credentials
        if raw:
            credentials = CloudCredentials.model_validate_json(decrypt_secret(raw))
        else:
            credentials = CloudCredentials()
        credentials = await self._fill_from_account_vault(credentials, owner.id)

        workspace_path = self._iac.get_workspace(workspace.root_dir)

        if run_init:
            bootstrap = build_provision_bootstrap(
                workspace_path,
                engine=workspace.engine,
            )
        else:
            bootstrap = None

        if bootstrap is not None:
            if await is_state_locked(workspace_id, scope="workspace"):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "provisioning_in_progress",
                        "message": PROVISIONING_IN_PROGRESS_MESSAGE,
                    },
                )
            active = await self._session.execute(
                select(TerminalSessionRecord).where(
                    TerminalSessionRecord.workspace_id == workspace_id,
                    TerminalSessionRecord.status == "active",
                )
            )
            if list(active.scalars().all()):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "provisioning_in_progress",
                        "message": PROVISIONING_IN_PROGRESS_MESSAGE,
                    },
                )

        from app.core.secrets import cloud_credentials_to_map

        cred_map = cloud_credentials_to_map(credentials)

        session = await self._sandbox.create_session(
            workspace_id=str(workspace.id),
            workspace_path=workspace_path,
            credentials=cred_map,
            bootstrap_command=bootstrap,
            cols=cols,
            rows=rows,
        )
        record = TerminalSessionRecord(
            id=UUID(session.session_id),
            workspace_id=workspace.id,
            mode=session.mode,
            status="active",
        )
        self._session.add(record)
        await self._session.commit()
        return session

    async def create_github_repo(
        self,
        request: GitHubRepoRequest,
        *,
        owner: User,
    ) -> GitHubRepoResult:
        if request.workspace_id:
            row = await self.get_workspace_for_owner(UUID(request.workspace_id), owner)
            root_dir = row.root_dir
        else:
            root_dir = None
        import asyncio

        try:
            return await asyncio.to_thread(
                self._github.create_repository_with_workflow,
                request,
                root_dir=root_dir,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "github_provisioning_failed", "message": str(exc)},
            ) from exc

    def _workspace_root(self, workspace: ProvisioningWorkspace) -> Path:
        return Path(workspace.root_dir)

    def _workspace_path(self, workspace: ProvisioningWorkspace) -> Path:
        root = self._workspace_root(workspace)
        if root.is_dir():
            return root
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "workspace_files_missing",
                "message": (
                    "Workspace files are unavailable on disk. "
                    "Use Restore files, or destroy this workspace and create a new one."
                ),
            },
        )

    @staticmethod
    def _wizard_config_json(request: ProvisioningWizardRequest) -> str:
        payload = {
            "name": request.name,
            "iac_engine": request.iac_engine.value,
            "cloud": request.cloud.model_dump(mode="json"),
            "run_init": request.run_init,
            "artifact_mode": request.artifact_mode.value,
            "kubernetes_packaging": request.kubernetes_packaging.value,
            "kubernetes_options": request.kubernetes_options.model_dump(mode="json"),
            "cost_optimization": request.cost_optimization.model_dump(mode="json"),
            "container_scaffold": request.container_scaffold.model_dump(mode="json"),
            "dependencies": request.dependencies.model_dump(mode="json"),
        }
        return json.dumps(payload)

    def _load_wizard_snapshot(
        self,
        row: ProvisioningWorkspace,
    ) -> dict[str, object] | None:
        if row.wizard_config_json:
            try:
                raw = json.loads(row.wizard_config_json)
            except json.JSONDecodeError:
                logger.warning(
                    "wizard_config_json_invalid",
                    workspace_id=str(row.id),
                )
            else:
                if isinstance(raw, dict):
                    return raw
        root = Path(row.root_dir)
        if root.is_dir():
            return self._iac.read_wizard_snapshot(root)
        return None

    def _relocate_ephemeral_root(self, row: ProvisioningWorkspace) -> Path:
        """Move workspaces off /tmp onto the durable iac_workspace_root when needed."""
        from app.core.config import get_settings

        root = Path(row.root_dir)
        settings_root = Path(get_settings().iac_workspace_root).expanduser().resolve()
        try:
            resolved = root.expanduser().resolve()
        except OSError:
            resolved = root
        tmp_roots = (
            Path("/tmp").resolve(),
            Path("/var/tmp").resolve(),
            Path("/private/tmp").resolve(),
        )
        under_tmp = any(
            resolved == tmp or tmp in resolved.parents for tmp in tmp_roots
        )
        if not under_tmp:
            return root
        # Always leave /tmp, even if a bad IAC_WORKSPACE_ROOT pointed there before.
        candidate = settings_root / root.name
        if candidate.exists() and candidate.resolve() != resolved:
            candidate = settings_root / f"{root.name}-{row.id.hex[:8]}"
        row.root_dir = str(candidate)
        logger.info(
            "workspace_root_relocated",
            workspace_id=str(row.id),
            from_dir=str(root),
            to_dir=str(candidate),
        )
        return candidate

    async def _ensure_workspace_on_disk(
        self,
        workspace: ProvisioningWorkspace,
        owner: User,
    ) -> ProvisioningWorkspace:
        from app.services.manifest_deploy import workspace_has_application_source
        from app.services.repo_import import RepoImportService

        importer = RepoImportService(self._session)
        if importer.needs_rehydrate(workspace):
            try:
                importer.rehydrate_workspace(workspace)
            except Exception as exc:
                logger.warning(
                    "repo_import_rehydrate_failed",
                    workspace_id=str(workspace.id),
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "workspace_source_missing",
                        "message": (
                            "Imported workspace files are missing on disk and could not "
                            f"be re-cloned: {exc}"
                        ),
                    },
                ) from exc
            await self._session.commit()
            return await self.get_workspace(workspace.id)

        root = self._workspace_root(workspace)
        if root.is_dir() and workspace_has_application_source(root):
            # Still move off /tmp when present so the next reboot does not wipe it.
            relocated = self._relocate_ephemeral_root(workspace)
            if relocated != root and not relocated.exists() and root.exists():
                relocated.parent.mkdir(parents=True, exist_ok=True)
                import shutil

                shutil.move(str(root), str(relocated))
                await self._session.commit()
                return await self.get_workspace(workspace.id)
            return workspace

        if root.is_dir():
            # Hollow scaffold (nginx only, no source) without repo_import metadata.
            return workspace

        await self.restore_workspace_files(workspace.id, owner)
        return await self.get_workspace_for_owner(workspace.id, owner)

    def _request_from_wizard_config(
        self,
        config: WorkspaceWizardConfig,
        credentials: CloudCredentials,
    ) -> ProvisioningWizardRequest:
        return ProvisioningWizardRequest(
            name=config.name,
            iac_engine=config.iac_engine,
            cloud=config.cloud,
            credentials=credentials,
            run_init=config.run_init,
            artifact_mode=config.artifact_mode,
            kubernetes_packaging=config.kubernetes_packaging,
            kubernetes_options=config.kubernetes_options,
            cost_optimization=config.cost_optimization,
            container_scaffold=config.container_scaffold,
            dependencies=config.dependencies,
        )

    async def restore_workspace_files(
        self,
        workspace_id: UUID,
        owner: User,
    ) -> IaCBundleSummary:
        """Recreate missing on-disk workspace files from the DB wizard snapshot."""
        row = await self.get_workspace_for_owner(workspace_id, owner)
        from app.services.repo_import import RepoImportService

        importer = RepoImportService(self._session)
        if importer.needs_rehydrate(row):
            root = importer.rehydrate_workspace(row)
            row.status = "ready"
            await self._session.commit()
            return IaCBundleSummary(
                workspace_id=str(row.id),
                engine=IaCEngine(row.engine),
                provider=CloudProvider(row.provider),
                root_dir=str(root),
                files=sorted(
                    str(p.relative_to(root))
                    for p in root.rglob("*")
                    if p.is_file()
                )[:200],
                artifact_mode=WorkspaceArtifactsMode.MANIFEST_ONLY,
                name=row.name,
                status=row.status,
                created_at=row.created_at,
            )

        root = self._relocate_ephemeral_root(row)
        config = await self.get_wizard_config(workspace_id, owner)

        credentials = CloudCredentials()
        if row.encrypted_credentials:
            try:
                credentials = CloudCredentials.model_validate_json(
                    decrypt_secret(row.encrypted_credentials)
                )
            except Exception:
                logger.warning(
                    "workspace_restore_credentials_unreadable",
                    workspace_id=str(workspace_id),
                )

        request = self._request_from_wizard_config(config, credentials)
        if isinstance(request.cloud, LocalCloudConfig):
            try:
                await ensure_kind_cluster(cluster_name=request.cloud.resources.cluster_name)
            except Exception as exc:
                logger.warning(
                    "workspace_restore_kind_skipped",
                    workspace_id=str(workspace_id),
                    error=str(exc),
                )

        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "workspace_path_unavailable",
                    "message": "Unable to prepare workspace directory for restore",
                },
            ) from exc

        files = self._iac.regenerate(root, request)
        row.wizard_config_json = self._wizard_config_json(request)
        row.status = "ready"
        await self._session.commit()

        logger.info(
            "workspace_files_restored",
            workspace_id=str(workspace_id),
            root_dir=row.root_dir,
            file_count=len(files),
        )
        return IaCBundleSummary(
            workspace_id=str(row.id),
            engine=request.iac_engine,
            provider=request.cloud.provider,
            root_dir=row.root_dir,
            files=files,
            artifact_mode=request.artifact_mode,
            name=row.name,
            status=row.status,
            created_at=row.created_at,
        )

    def get_workspace_kubernetes_packaging(
        self,
        workspace: ProvisioningWorkspace,
    ) -> KubernetesPackaging | None:
        snapshot = self._load_wizard_snapshot(workspace)
        if snapshot and snapshot.get("kubernetes_packaging"):
            raw = str(snapshot["kubernetes_packaging"])
            try:
                packaging = KubernetesPackaging(raw)
            except ValueError:
                logger.warning(
                    "workspace_packaging_invalid",
                    workspace_id=str(workspace.id),
                    packaging=raw,
                )
                packaging = None
            else:
                if packaging != KubernetesPackaging.NONE:
                    return packaging

        root = self._workspace_root(workspace)
        if not root.is_dir():
            if workspace.provider == CloudProvider.LOCAL.value:
                return KubernetesPackaging.RAW_MANIFESTS
            return None

        from app.services.manifest_deploy import (
            workspace_has_helm_chart,
            workspace_has_raw_manifests,
        )

        if workspace_has_raw_manifests(root):
            return KubernetesPackaging.RAW_MANIFESTS
        if workspace_has_helm_chart(root):
            return KubernetesPackaging.HELM
        return None

    def get_workspace_artifact_mode(
        self,
        workspace: ProvisioningWorkspace,
    ) -> WorkspaceArtifactsMode:
        snapshot = self._load_wizard_snapshot(workspace)
        if snapshot and snapshot.get("artifact_mode"):
            try:
                return WorkspaceArtifactsMode(str(snapshot["artifact_mode"]))
            except ValueError:
                logger.warning(
                    "workspace_artifact_mode_invalid",
                    workspace_id=str(workspace.id),
                    artifact_mode=snapshot.get("artifact_mode"),
                )

        root = self._workspace_root(workspace)
        if not root.is_dir():
            if workspace.provider == CloudProvider.LOCAL.value:
                return WorkspaceArtifactsMode.MANIFEST_ONLY
            return WorkspaceArtifactsMode.IAC_ONLY

        has_manifests = self.get_workspace_kubernetes_packaging(workspace) is not None
        has_iac = (root / "infra" / "terraform").is_dir() or (root / "Pulumi.yaml").is_file()
        if has_iac and has_manifests:
            return WorkspaceArtifactsMode.BOTH
        if has_manifests:
            return WorkspaceArtifactsMode.MANIFEST_ONLY
        return WorkspaceArtifactsMode.IAC_ONLY

    async def list_workspace_files(
        self, workspace_id: UUID, owner: User
    ) -> list[WorkspaceFileNode]:
        workspace = await self.get_workspace_for_owner(workspace_id, owner)
        workspace = await self._ensure_workspace_on_disk(workspace, owner)
        nodes = ws_files.list_file_tree(self._workspace_path(workspace))
        return [WorkspaceFileNode.model_validate(node) for node in nodes]

    async def read_workspace_file(
        self, workspace_id: UUID, owner: User, relative_path: str
    ) -> WorkspaceFileContent:
        workspace = await self.get_workspace_for_owner(workspace_id, owner)
        workspace = await self._ensure_workspace_on_disk(workspace, owner)
        try:
            content = ws_files.read_file(self._workspace_path(workspace), relative_path)
        except ws_files.WorkspaceFileError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "workspace_file_error", "message": str(exc)},
            ) from exc
        return WorkspaceFileContent(path=relative_path, content=content)

    async def write_workspace_file(
        self,
        workspace_id: UUID,
        owner: User,
        *,
        relative_path: str,
        content: str,
    ) -> WorkspaceFileContent:
        workspace = await self.get_workspace_for_owner(workspace_id, owner)
        try:
            path = ws_files.write_file(
                self._workspace_path(workspace), relative_path, content
            )
        except ws_files.WorkspaceFileError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "workspace_file_error", "message": str(exc)},
            ) from exc
        return WorkspaceFileContent(path=path, content=content)

    async def mkdir_workspace(
        self, workspace_id: UUID, owner: User, relative_path: str
    ) -> WorkspaceFileNode:
        workspace = await self.get_workspace_for_owner(workspace_id, owner)
        try:
            path = ws_files.mkdir(self._workspace_path(workspace), relative_path)
        except ws_files.WorkspaceFileError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "workspace_file_error", "message": str(exc)},
            ) from exc
        return WorkspaceFileNode(path=path, type="directory", size=None)

    async def delete_workspace_path(
        self, workspace_id: UUID, owner: User, relative_path: str
    ) -> None:
        workspace = await self.get_workspace_for_owner(workspace_id, owner)
        try:
            ws_files.delete_path(self._workspace_path(workspace), relative_path)
        except ws_files.WorkspaceFileError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "workspace_file_error", "message": str(exc)},
            ) from exc

    async def rename_workspace_path(
        self,
        workspace_id: UUID,
        owner: User,
        *,
        from_path: str,
        to_path: str,
    ) -> WorkspaceFileNode:
        workspace = await self.get_workspace_for_owner(workspace_id, owner)
        try:
            path = ws_files.rename_path(
                self._workspace_path(workspace), from_path, to_path
            )
        except ws_files.WorkspaceFileError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "workspace_file_error", "message": str(exc)},
            ) from exc
        root = self._workspace_path(workspace)
        target = root / path
        node_type = "directory" if target.is_dir() else "file"
        size = target.stat().st_size if target.is_file() else None
        return WorkspaceFileNode(path=path, type=node_type, size=size)

    async def format_workspace_content(
        self,
        workspace_id: UUID,
        owner: User,
        *,
        relative_path: str,
        content: str,
    ) -> WorkspaceFormatResponse:
        await self.get_workspace_for_owner(workspace_id, owner)
        try:
            formatted = ws_files.format_content(relative_path, content)
        except (ws_files.WorkspaceFileError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "workspace_format_error", "message": str(exc)},
            ) from exc
        return WorkspaceFormatResponse(path=relative_path, content=formatted)

    def list_file_templates(
        self, category: str | None = None
    ) -> list[WorkspaceTemplateInfo]:
        return [
            WorkspaceTemplateInfo(
                id=item.id,
                label=item.label,
                category=item.category,
                description=item.description,
                default_path=item.default_path,
            )
            for item in list_templates(category=category)
        ]

    async def apply_file_template(
        self,
        workspace_id: UUID,
        owner: User,
        request: WorkspaceTemplateApplyRequest,
    ) -> WorkspaceFileContent:
        workspace = await self.get_workspace_for_owner(workspace_id, owner)
        try:
            template = get_template(request.template_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "template_not_found", "message": str(exc)},
            ) from exc
        target_path = (request.path or template.default_path).strip()
        root = self._workspace_path(workspace)
        try:
            resolved = ws_files.resolve_safe_path(root, target_path)
            if resolved.exists() and not request.overwrite:
                raise ws_files.WorkspaceFileError(
                    f"File already exists: {target_path} (set overwrite=true)"
                )
            path = ws_files.write_file(root, target_path, template.content)
        except ws_files.WorkspaceFileError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "workspace_file_error", "message": str(exc)},
            ) from exc
        return WorkspaceFileContent(path=path, content=template.content)

    async def push_workspace_to_github(
        self,
        workspace_id: UUID,
        owner: User,
        request: WorkspacePushRequest,
    ) -> GitHubRepoResult:
        workspace = await self.get_workspace_for_owner(workspace_id, owner)
        import asyncio

        try:
            return await asyncio.to_thread(
                self._github.push_workspace_files,
                installation_id=request.installation_id,
                existing_full_name=request.existing_full_name,
                root_dir=workspace.root_dir,
                commit_message=request.commit_message,
                include_workflow=request.include_workflow,
                include_dockerfiles=request.include_dockerfiles,
                provider=CloudProvider(workspace.provider),
                engine=IaCEngine(workspace.engine),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "github_push_failed", "message": str(exc)},
            ) from exc

    async def _gitlab_credentials(self, owner: User) -> tuple[str, str]:
        from app.services.gitlab_service import GitLabAuthError, GitLabAuthService

        auth = GitLabAuthService(self._session)
        row = await auth.get_connection(owner.id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "gitlab_not_connected",
                    "message": "Connect GitLab under Integrations before pushing",
                },
            )
        try:
            return row.base_url, auth.decrypt_token(row)
        except Exception as exc:  # noqa: BLE001
            raise GitLabAuthError("Stored GitLab token could not be decrypted") from exc

    async def list_gitlab_projects(self, *, owner: User):
        from app.schemas.cloud import GitlabProjectItem
        from app.services.gitlab_service import (
            GitLabAuthError,
            GitLabProvisioningService,
            http_error_from_gitlab,
        )

        base_url, token = await self._gitlab_credentials(owner)
        try:
            rows = await asyncio.to_thread(
                GitLabProvisioningService(self._iac).list_projects,
                base_url=base_url,
                token=token,
            )
        except GitLabAuthError as exc:
            raise http_error_from_gitlab(exc) from exc
        return [GitlabProjectItem.model_validate(item) for item in rows]

    async def create_gitlab_repo(self, request, *, owner: User):
        from app.schemas.cloud import GitlabRepoResult
        from app.services.gitlab_service import (
            GitLabAuthError,
            GitLabProvisioningService,
            http_error_from_gitlab,
        )

        base_url, token = await self._gitlab_credentials(owner)
        root_dir: str | None = None
        if request.workspace_id:
            row = await self.get_workspace_for_owner(UUID(request.workspace_id), owner)
            root_dir = row.root_dir
        try:
            result = await asyncio.to_thread(
                GitLabProvisioningService(self._iac).create_or_open_project,
                base_url=base_url,
                token=token,
                name=request.name,
                description=request.description,
                private=request.private,
                existing_path=request.existing_path,
                root_dir=root_dir,
                include_ci=request.include_ci,
            )
        except GitLabAuthError as exc:
            raise http_error_from_gitlab(exc) from exc
        return GitlabRepoResult.model_validate(result)

    async def push_workspace_to_gitlab(
        self,
        workspace_id: UUID,
        owner: User,
        request,
    ):
        from app.schemas.cloud import GitlabRepoResult
        from app.services.gitlab_service import (
            GitLabAuthError,
            GitLabProvisioningService,
            http_error_from_gitlab,
        )

        workspace = await self.get_workspace_for_owner(workspace_id, owner)
        base_url, token = await self._gitlab_credentials(owner)
        try:
            result = await asyncio.to_thread(
                GitLabProvisioningService(self._iac).push_workspace_files,
                base_url=base_url,
                token=token,
                project_path=request.project_path,
                root_dir=workspace.root_dir,
                commit_message=request.commit_message,
            )
        except GitLabAuthError as exc:
            raise http_error_from_gitlab(exc) from exc
        return GitlabRepoResult.model_validate(result)


def _default_cloud_for_provider(provider: CloudProvider):
    if provider == CloudProvider.LOCAL:
        return LocalCloudConfig(resources=LocalResources())
    if provider == CloudProvider.GCP:
        return GcpCloudConfig(resources=GcpResources(project_id="my-project"))
    if provider == CloudProvider.AWS:
        return AwsCloudConfig(resources=AwsResources())
    if provider == CloudProvider.AZURE:
        return AzureCloudConfig(resources=AzureResources(resource_group="lp-rg"))
    return CloudflareCloudConfig(resources=CloudflareResources(account_id="00000000000000000000000000000000"))

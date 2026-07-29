from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

import yaml
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.secrets import decrypt_secret, encrypt_secret
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
            files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
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

    async def get_wizard_config(
        self,
        workspace_id: UUID,
        owner: User,
    ) -> WorkspaceWizardConfig:
        row = await self.get_workspace_for_owner(workspace_id, owner)
        has_credentials = bool(row.encrypted_credentials)
        root = Path(row.root_dir)
        snapshot = self._iac.read_wizard_snapshot(root) if root.is_dir() else None
        if snapshot is not None:
            try:
                config = WorkspaceWizardConfig.model_validate(
                    {**snapshot, "has_credentials": has_credentials}
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
            kubernetes_packaging=KubernetesPackaging.NONE,
            kubernetes_options=KubernetesWorkloadOptions(),
            has_credentials=has_credentials,
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
        files = self._iac.regenerate(workspace_path, request)

        merged = self._merge_credentials(row.encrypted_credentials, request.credentials)
        row.engine = request.iac_engine.value
        row.provider = request.cloud.provider.value
        row.encrypted_credentials = encrypt_secret(merged.model_dump_json())
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
            engine=request.iac_engine,
            provider=request.cloud.provider,
            root_dir=row.root_dir,
            files=files,
            artifact_mode=request.artifact_mode,
            name=row.name,
            status=row.status,
            created_at=row.created_at,
        )

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

        self._iac.destroy_workspace(row.root_dir)
        await self._session.delete(row)
        await self._session.commit()
        logger.info("provisioning_workspace_destroyed", workspace_id=str(workspace_id))

        if was_local:
            await self._maybe_teardown_kind(owner)

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

        cred_map = {
            "GCP_SA_KEY": credentials.gcp_sa_key_json,
            "AWS_ACCESS_KEY_ID": credentials.aws_access_key_id,
            "AWS_SECRET_ACCESS_KEY": credentials.aws_secret_access_key,
            "AWS_SESSION_TOKEN": credentials.aws_session_token,
            "AZURE_CLIENT_ID": credentials.azure_client_id,
            "AZURE_CLIENT_SECRET": credentials.azure_client_secret,
            "AZURE_TENANT_ID": credentials.azure_tenant_id,
            "AZURE_SUBSCRIPTION_ID": credentials.azure_subscription_id,
            "CLOUDFLARE_API_TOKEN": credentials.cloudflare_api_token,
        }

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
                    "Re-provision this workspace or destroy it and create a new one."
                ),
            },
        )

    def get_workspace_kubernetes_packaging(
        self,
        workspace: ProvisioningWorkspace,
    ) -> KubernetesPackaging | None:
        root = self._workspace_root(workspace)
        if not root.is_dir():
            return None
        snapshot = self._iac.read_wizard_snapshot(root)
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
        root = self._workspace_root(workspace)
        if not root.is_dir():
            if workspace.provider == CloudProvider.LOCAL.value:
                return WorkspaceArtifactsMode.MANIFEST_ONLY
            return WorkspaceArtifactsMode.IAC_ONLY

        snapshot = self._iac.read_wizard_snapshot(root)
        if snapshot and snapshot.get("artifact_mode"):
            try:
                return WorkspaceArtifactsMode(str(snapshot["artifact_mode"]))
            except ValueError:
                logger.warning(
                    "workspace_artifact_mode_invalid",
                    workspace_id=str(workspace.id),
                    artifact_mode=snapshot.get("artifact_mode"),
                )

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
        nodes = ws_files.list_file_tree(self._workspace_path(workspace))
        return [WorkspaceFileNode.model_validate(node) for node in nodes]

    async def read_workspace_file(
        self, workspace_id: UUID, owner: User, relative_path: str
    ) -> WorkspaceFileContent:
        workspace = await self.get_workspace_for_owner(workspace_id, owner)
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

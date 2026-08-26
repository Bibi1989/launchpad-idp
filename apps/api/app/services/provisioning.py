from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

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
    CloudPluginTarget,
    CloudProvider,
    CostEstimateLineItem,
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
    ProvisioningCostEstimate,
    WorkspaceCdMode,
    WorkspaceFileContent,
    WorkspaceFileNode,
    WorkspaceFormatResponse,
    WorkspaceLinkedAppRepo,
    WorkspaceLinkedAppRepoRequest,
    WorkspaceLinkedAppRepoResponse,
    WorkspaceLinkedRepoItem,
    WorkspaceLinkedReposRequest,
    WorkspaceLinkedReposResponse,
    WorkspaceRepoKind,
    WorkspaceGitSourceRequest,
    WorkspaceGitSourceResponse,
    WorkspaceListItem,
    WorkspacePushRequest,
    WorkspaceTemplateApplyRequest,
    WorkspaceTemplateInfo,
    WorkspacePromotionTarget,
    WorkspacePromoteRequest,
    WorkspaceWizardConfig,
    WorkspaceArtifactsMode,
    WorkspaceRuntimeMode,
    GcpApiEnablementResponse,
    RunningInstanceConfig,
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

_DEFAULT_MONTH_HOURS = Decimal("730")
_DEFAULT_GCP_COMPUTE_HOURLY = Decimal("0.19")
_DEFAULT_AWS_COMPUTE_HOURLY = Decimal("0.21")
_DEFAULT_AZURE_COMPUTE_HOURLY = Decimal("0.23")
_DEFAULT_CLOUDFLARE_SERVICE_HOURLY = Decimal("0.03")

_GCP_MACHINE_RATES: dict[str, Decimal] = {
    "e2-small": Decimal("0.03"),
    "e2-medium": Decimal("0.04"),
    "e2-standard-2": Decimal("0.09"),
    "e2-standard-4": Decimal("0.19"),
    "n2-standard-2": Decimal("0.13"),
    "n2-standard-4": Decimal("0.26"),
}
_AWS_INSTANCE_RATES: dict[str, Decimal] = {
    "t3.micro": Decimal("0.0104"),
    "t3.small": Decimal("0.0208"),
    "t3.medium": Decimal("0.0416"),
    "t3.large": Decimal("0.0832"),
    "m5.large": Decimal("0.096"),
}
_AZURE_VM_RATES: dict[str, Decimal] = {
    "Standard_B2s": Decimal("0.0464"),
    "Standard_D2_v2": Decimal("0.096"),
    "Standard_D4_v3": Decimal("0.192"),
}


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
        project_id: UUID | None = None,
    ) -> IaCBundleSummary:
        # Kind/k3d cluster bring-up is deferred to open_terminal / preview deploy.
        # Blocking create on ensure_kind_cluster made "Generate IaC" hang past the
        # client timeout when the local cluster was cold or slow to start.

        request = await self._with_account_credentials(request, owner)
        request = self._with_gcp_project_from_sa(request)
        bundle = self._iac.generate(request)
        encrypted = encrypt_secret(request.credentials.model_dump_json())
        from app.models.domain import Organization
        from app.services.orgs import OrganizationService
        from app.services.plans import assert_can_create_workspace
        from app.services.projects import ProjectService

        orgs = OrganizationService(self._session)
        resolved_org_id = org_id
        org: Organization | None = None
        if resolved_org_id is None:
            org = await orgs.ensure_personal_org(owner)
            resolved_org_id = org.id
            await self._session.commit()
            logger.warning(
                "provisioning_org_context_missing_autofixed",
                owner_id=str(owner.id),
                resolved_org_id=str(resolved_org_id),
            )
        if org is None:
            org = await self._session.get(Organization, resolved_org_id)
            if org is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "org_not_found", "message": "Organization not found"},
                )
        await assert_can_create_workspace(self._session, org)
        org_ctx = await orgs.resolve_context(user=owner, org_id=resolved_org_id)
        project = await ProjectService(self._session).resolve_project_for_workspace(
            org=org_ctx,
            project_id=project_id,
        )
        existing_ws = await self._session.execute(
            select(ProvisioningWorkspace).where(
                ProvisioningWorkspace.org_id == resolved_org_id,
                ProvisioningWorkspace.name == request.name,
            )
        )
        if existing_ws.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "workspace_exists",
                    "message": f"Workspace '{request.name}' already exists in this organization",
                },
            )
        row = ProvisioningWorkspace(
            id=UUID(bundle.workspace_id),
            owner_id=owner.id,
            org_id=resolved_org_id,
            project_id=project.id,
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
            runtime_mode=request.runtime_mode,
            name=request.name,
            status="ready",
            created_at=datetime.now().astimezone(),
            starred=False,
        )

    async def fill_cloud_credentials_from_account_vault(
        self,
        credentials: CloudCredentials,
        owner: User,
        *,
        provider: str | None = None,
    ) -> CloudCredentials:
        """Fill blank cloud credential fields from the user's account vault.

        This is used for flows like "Deploy to cloud" where a workspace may have
        been created without storing encrypted cloud credentials yet.
        """
        return await self._fill_from_account_vault(credentials, owner.id, provider=provider)

    async def list_workspaces(
        self,
        owner: User,
        *,
        org_id: UUID | None = None,
        starred_only: bool = False,
        project_id: UUID | None = None,
    ) -> list[WorkspaceListItem]:
        from app.models.domain import OrgRole
        from app.services.orgs import OrganizationService
        from app.services.projects import ProjectService

        orgs = OrganizationService(self._session)
        if project_id is not None:
            ctx = await orgs.resolve_context(user=owner, org_id=org_id)
            await ProjectService(self._session).require_project_access(
                user=owner, project_id=project_id, minimum=OrgRole.VIEWER
            )
            stmt = (
                select(ProvisioningWorkspace)
                .where(
                    ProvisioningWorkspace.org_id == ctx.org_id,
                    ProvisioningWorkspace.project_id == project_id,
                )
                .order_by(ProvisioningWorkspace.created_at.desc())
                .limit(100)
            )
            if starred_only:
                stmt = stmt.where(ProvisioningWorkspace.starred_at.is_not(None))
            result = await self._session.execute(stmt)
            rows = list(result.scalars().all())
            return await self._workspace_list_items(self._exclude_deleting_workspaces(rows))

        if org_id is not None:
            ctx = await orgs.resolve_context(user=owner, org_id=org_id)
            target_org_id = ctx.org_id
            stmt = (
                select(ProvisioningWorkspace)
                .where(ProvisioningWorkspace.org_id == target_org_id)
                .order_by(ProvisioningWorkspace.created_at.desc())
                .limit(100)
            )
            if starred_only:
                stmt = stmt.where(ProvisioningWorkspace.starred_at.is_not(None))
            result = await self._session.execute(stmt)
            rows = list(result.scalars().all())
        else:
            memberships = await orgs.list_for_user(owner)
            if not memberships:
                return []
            org = memberships[0][0]
            stmt = (
                select(ProvisioningWorkspace)
                .where(ProvisioningWorkspace.org_id == org.id)
                .order_by(ProvisioningWorkspace.created_at.desc())
                .limit(100)
            )
            if starred_only:
                stmt = stmt.where(ProvisioningWorkspace.starred_at.is_not(None))
            result = await self._session.execute(stmt)
            rows = list(result.scalars().all())
            if not rows and not starred_only:
                result = await self._session.execute(
                    select(ProvisioningWorkspace)
                    .where(ProvisioningWorkspace.owner_id == owner.id)
                    .order_by(ProvisioningWorkspace.created_at.desc())
                    .limit(100)
                )
                rows = list(result.scalars().all())
            elif not rows and starred_only:
                result = await self._session.execute(
                    select(ProvisioningWorkspace)
                    .where(
                        ProvisioningWorkspace.owner_id == owner.id,
                        ProvisioningWorkspace.starred_at.is_not(None),
                    )
                    .order_by(ProvisioningWorkspace.created_at.desc())
                    .limit(100)
                )
                rows = list(result.scalars().all())
        return await self._workspace_list_items(self._exclude_deleting_workspaces(rows))

    @staticmethod
    def _exclude_deleting_workspaces(
        rows: list[ProvisioningWorkspace],
    ) -> list[ProvisioningWorkspace]:
        """Hide in-flight deletes from the list; keep destroy_failed for retry."""
        return [row for row in rows if row.status != "deleting"]

    async def _workspace_list_items(
        self,
        rows: list[ProvisioningWorkspace],
    ) -> list[WorkspaceListItem]:
        from app.models.domain import Project

        project_ids = {row.project_id for row in rows if row.project_id}
        project_names: dict[UUID, str] = {}
        if project_ids:
            result = await self._session.execute(
                select(Project.id, Project.name).where(Project.id.in_(project_ids))
            )
            project_names = {pid: name for pid, name in result.all()}
        return [
            self._to_workspace_list_item(
                row,
                project_name=project_names.get(row.project_id) if row.project_id else None,
            )
            for row in rows
        ]

    def _to_workspace_list_item(
        self,
        row: ProvisioningWorkspace,
        *,
        project_name: str | None = None,
    ) -> WorkspaceListItem:
        return WorkspaceListItem(
            id=row.id,
            name=row.name,
            engine=row.engine,
            provider=row.provider,
            status=row.status,
            artifact_mode=self.get_workspace_artifact_mode(row),
            created_at=row.created_at,
            root_dir=row.root_dir,
            starred=row.starred_at is not None,
            project_id=row.project_id,
            project_name=project_name,
            runtime_mode=self.get_workspace_runtime_mode(row),
        )

    async def set_workspace_starred(
        self,
        workspace_id: UUID,
        owner: User,
        *,
        starred: bool,
    ) -> WorkspaceListItem:
        from datetime import UTC, datetime

        row = await self.get_workspace_for_owner(workspace_id, owner)
        row.starred_at = datetime.now(UTC) if starred else None
        await self._session.commit()
        await self._session.refresh(row)
        project_name: str | None = None
        if row.project_id:
            from app.models.domain import Project

            project = await self._session.get(Project, row.project_id)
            project_name = project.name if project else None
        return self._to_workspace_list_item(row, project_name=project_name)
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

    async def get_service_graph(self, workspace_id: UUID, owner: User):
        """Return the workspace's inter-service connection graph (nodes/edges/mermaid)."""
        row = await self.get_workspace_for_owner(workspace_id, owner)
        return self._build_service_graph_response(row)

    async def update_service_connections(self, workspace_id: UUID, owner: User, connections):
        """Persist operator-configured connections and return the rebuilt graph."""
        row = await self.get_workspace_for_owner(workspace_id, owner)
        try:
            snapshot = json.loads(row.wizard_config_json) if row.wizard_config_json else {}
        except json.JSONDecodeError:
            snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot["service_connections"] = [c.model_dump() for c in connections]
        row.wizard_config_json = json.dumps(snapshot)
        await self._session.commit()
        return self._build_service_graph_response(row)

    def _build_service_graph_response(self, row: ProvisioningWorkspace):
        """Rebuild the connection graph from persisted comms + operator connections."""
        from app.schemas.repo_import import ServiceGraphResponse
        from app.services.comm_detector import ServiceComms
        from app.services.service_graph import (
            ExplicitConnection,
            build_service_graph,
            graph_to_mermaid,
        )

        try:
            raw = json.loads(row.wizard_config_json) if row.wizard_config_json else {}
        except json.JSONDecodeError:
            raw = {}
        raw = raw if isinstance(raw, dict) else {}

        comms = [
            ServiceComms.model_validate(c)
            for c in (raw.get("service_comms") or [])
            if isinstance(c, dict)
        ]
        # Linked repos (link flow) carry no detected comms - surface each as a service
        # node so it can be wired to databases/brokers in the connection editor.
        existing_services = {c.service for c in comms}
        for item in self._linked_repos_from_snapshot(raw):
            name = self._repo_service_name(item)
            if name and name not in existing_services:
                comms.append(ServiceComms(service=name, capabilities=[]))
                existing_services.add(name)
        explicit = [
            ExplicitConnection.model_validate(c)
            for c in (raw.get("service_connections") or [])
            if isinstance(c, dict)
        ]
        frameworks: dict[str, str] = {}
        detection = raw.get("detection") if isinstance(raw.get("detection"), dict) else {}
        for svc in (detection or {}).get("services", []):
            if isinstance(svc, dict) and svc.get("name"):
                frameworks[str(svc["name"])] = str(svc.get("framework") or "")

        # Enabled dependency kinds (databases, caches, brokers) surface as graph nodes.
        deps = raw.get("dependencies") if isinstance(raw.get("dependencies"), dict) else {}
        infra_kinds = [
            kind
            for kind, cfg in (deps or {}).items()
            if isinstance(cfg, dict) and cfg.get("enabled")
        ]

        graph = build_service_graph(
            comms,
            explicit_connections=explicit,
            frameworks=frameworks,
            infra_kinds=infra_kinds,
        )
        return ServiceGraphResponse(
            repos=list(raw.get("repos") or []),
            nodes=[n.model_dump() for n in graph.nodes],
            edges=[e.model_dump(mode="json") for e in graph.edges],
            mermaid=graph_to_mermaid(graph),
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
        project_name: str | None = None
        if row.project_id:
            from app.models.domain import Project

            project = await self._session.get(Project, row.project_id)
            project_name = project.name if project else None
        return IaCBundleSummary(
            workspace_id=str(row.id),
            engine=IaCEngine(row.engine),
            provider=CloudProvider(row.provider),
            root_dir=row.root_dir,
            files=files,
            artifact_mode=self.get_workspace_artifact_mode(row),
            runtime_mode=self.get_workspace_runtime_mode(row),
            name=row.name,
            status=row.status,
            created_at=row.created_at,
            starred=row.starred_at is not None,
            project_id=row.project_id,
            project_name=project_name,
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

    async def promote_workspace(
        self,
        source_workspace_id: UUID,
        payload: WorkspacePromoteRequest,
        *,
        owner: User,
        org_id: UUID,
    ) -> IaCBundleSummary:
        source = await self.get_workspace_for_owner(source_workspace_id, owner)
        config = await self.get_wizard_config(source_workspace_id, owner)
        target_suffix = payload.target_environment.value
        promoted_name = payload.promoted_name or self._promoted_name(config.name, target_suffix)

        credentials = CloudCredentials()
        if source.encrypted_credentials:
            try:
                credentials = CloudCredentials.model_validate_json(
                    decrypt_secret(source.encrypted_credentials)
                )
            except Exception:
                logger.warning(
                    "workspace_promotion_credentials_unreadable",
                    workspace_id=str(source_workspace_id),
                )

        promoted_request = ProvisioningWizardRequest(
            name=promoted_name,
            iac_engine=config.iac_engine,
            cloud=config.cloud,
            credentials=credentials,
            run_init=config.run_init if payload.run_init is None else payload.run_init,
            runtime_mode=config.runtime_mode,
            running_instance=config.running_instance,
            artifact_mode=config.artifact_mode,
            kubernetes_packaging=config.kubernetes_packaging,
            kubernetes_options=config.kubernetes_options,
            cost_optimization=config.cost_optimization,
            container_scaffold=config.container_scaffold,
            dependencies=config.dependencies,
            ansible=config.ansible,
            config_tool=config.config_tool,
            env_vars=config.env_vars,
            cloud_plugin=config.cloud_plugin,
        )
        return await self.generate_bundle(
            promoted_request,
            owner=owner,
            org_id=org_id,
            project_id=payload.project_id,
        )

    async def clone_workspace_for_cloud_promote(
        self,
        source_workspace_id: UUID,
        *,
        owner: User,
        org_id: UUID | None,
        target_provider: CloudProvider,
        credentials: CloudCredentials,
        workspace_name: str,
        primary_service: str | None = None,
        code_source: str | None = None,
        region: str | None = None,
        create_vpc: bool = False,
        create_subnets: bool = False,
        existing_vpc_id: str | None = None,
        existing_security_group_id: str | None = None,
        project_id: UUID | None = None,
        image_scan: object | None = None,
        cloud_plugin: CloudPluginTarget | None = None,
    ) -> UUID:
        """Copy a local workspace tree and re-target wizard config for cloud serverless."""
        from app.core.config import get_settings
        from app.models.domain import Organization
        from app.schemas.cloud import InstanceCodeSource
        from app.services.cloud_promote import build_cloud_promote_wizard_request
        from app.services.orgs import OrganizationService
        from app.services.plans import assert_can_create_workspace
        from app.services.projects import ProjectService

        source = await self.get_workspace_for_owner(source_workspace_id, owner)
        config = await self.get_wizard_config(source_workspace_id, owner)
        source_root = Path(source.root_dir)
        if not source_root.is_dir():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "workspace_source_missing",
                    "message": "Source workspace files are missing on disk",
                },
            )

        resolved_code: InstanceCodeSource | None = None
        if code_source:
            resolved_code = InstanceCodeSource(code_source)
        request = build_cloud_promote_wizard_request(
            config,
            workspace_name=workspace_name,
            provider=target_provider,
            credentials=credentials,
            primary_service=primary_service,
            code_source=resolved_code,
            region=region,
            create_vpc=create_vpc,
            create_subnets=create_subnets,
            existing_vpc_id=existing_vpc_id,
            existing_security_group_id=existing_security_group_id,
            image_scan=image_scan,
            cloud_plugin=cloud_plugin,
        )
        request = await self._with_account_credentials(request, owner)
        request = self._with_gcp_project_from_sa(request)

        new_id = uuid4()
        settings_root = Path(get_settings().iac_workspace_root).expanduser()
        settings_root.mkdir(parents=True, exist_ok=True)
        dest = settings_root / f"{workspace_name}-{new_id.hex[:8]}"
        if dest.exists():
            dest = settings_root / f"{workspace_name}-{new_id.hex[:8]}-cloud"
        shutil.copytree(source_root, dest, symlinks=False, ignore_dangling_symlinks=True)
        self._iac.regenerate(dest, request)

        orgs = OrganizationService(self._session)
        resolved_org_id = org_id
        org: Organization | None = None
        if resolved_org_id is None:
            org = await orgs.ensure_personal_org(owner)
            resolved_org_id = org.id
        if org is None:
            org = await self._session.get(Organization, resolved_org_id)
            if org is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "org_not_found", "message": "Organization not found"},
                )
        await assert_can_create_workspace(self._session, org)
        org_ctx = await orgs.resolve_context(user=owner, org_id=resolved_org_id)
        project = await ProjectService(self._session).resolve_project_for_workspace(
            org=org_ctx,
            project_id=project_id or source.project_id,
        )

        final_name = workspace_name
        for i in range(0, 10):
            candidate = workspace_name if i == 0 else f"{workspace_name}-{i}"[:128]
            clash = await self._session.execute(
                select(ProvisioningWorkspace).where(
                    ProvisioningWorkspace.org_id == resolved_org_id,
                    ProvisioningWorkspace.name == candidate,
                )
            )
            if clash.scalar_one_or_none() is None:
                final_name = candidate
                break

        encrypted = encrypt_secret(request.credentials.model_dump_json())
        row = ProvisioningWorkspace(
            id=new_id,
            owner_id=owner.id,
            org_id=resolved_org_id,
            project_id=project.id,
            name=final_name,
            engine=request.iac_engine.value,
            provider=target_provider.value,
            root_dir=str(dest),
            status="ready",
            encrypted_credentials=encrypted,
            wizard_config_json=self._wizard_config_json(request),
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "cloud_promote_workspace_cloned",
            source_workspace_id=str(source_workspace_id),
            workspace_id=str(new_id),
            provider=target_provider.value,
            root_dir=str(dest),
        )
        return new_id

    async def sync_workspace_after_cloud_deploy(
        self,
        workspace_id: UUID,
        *,
        running_instance: RunningInstanceConfig,
    ) -> None:
        """Refresh ansible + cloud IaC on disk after a successful cloud attach deploy."""
        row = await self.get_workspace(workspace_id)
        snapshot = self._load_wizard_snapshot(row)
        if snapshot is None:
            logger.warning(
                "cloud_deploy_workspace_sync_skipped",
                workspace_id=str(workspace_id),
                reason="no_wizard_snapshot",
            )
            return
        try:
            config = WorkspaceWizardConfig.model_validate(
                {**snapshot, "has_credentials": False}
            )
        except Exception:
            logger.warning(
                "cloud_deploy_workspace_sync_skipped",
                workspace_id=str(workspace_id),
                reason="invalid_wizard_snapshot",
            )
            return

        credentials = CloudCredentials()
        if row.encrypted_credentials:
            try:
                credentials = CloudCredentials.model_validate_json(
                    decrypt_secret(row.encrypted_credentials)
                )
            except Exception:
                logger.warning(
                    "cloud_deploy_workspace_sync_credentials_unreadable",
                    workspace_id=str(workspace_id),
                )

        config = config.model_copy(update={"running_instance": running_instance})
        request = self._request_from_wizard_config(config, credentials)
        request = self._with_gcp_project_from_sa(request)
        root = self._workspace_root(row)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "cloud_deploy_workspace_sync_path_failed",
                workspace_id=str(workspace_id),
                error=str(exc),
            )
            return

        files = self._iac.regenerate(root, request)
        row.wizard_config_json = self._wizard_config_json(request)
        row.status = "ready"
        logger.info(
            "cloud_deploy_workspace_synced",
            workspace_id=str(workspace_id),
            host=running_instance.host,
            file_count=len(files),
        )

    @staticmethod
    def _promoted_name(base_name: str, suffix: str) -> str:
        normalized = base_name.strip().lower()
        if normalized.endswith(f"-{suffix}"):
            return normalized
        return f"{normalized}-{suffix}"

    def estimate_workspace_cost(
        self,
        request: ProvisioningWizardRequest,
    ) -> ProvisioningCostEstimate:
        cloud = request.cloud
        if isinstance(cloud, LocalCloudConfig):
            return ProvisioningCostEstimate(
                provider=CloudProvider.LOCAL,
                hourly_usd=0.0,
                monthly_usd=0.0,
                assumptions=["Local provider uses operator-managed compute, no cloud bill estimate."],
            )

        if isinstance(cloud, GcpCloudConfig):
            return self._estimate_gcp(cloud.resources, request)
        if isinstance(cloud, AwsCloudConfig):
            return self._estimate_aws(cloud.resources, request)
        if isinstance(cloud, AzureCloudConfig):
            return self._estimate_azure(cloud.resources, request)
        if isinstance(cloud, CloudflareCloudConfig):
            return self._estimate_cloudflare(cloud.resources, request)
        return ProvisioningCostEstimate(
            provider=cloud.provider,
            hourly_usd=0.0,
            monthly_usd=0.0,
        )

    def _monthly_hours(self) -> Decimal:
        from app.core.config import get_settings

        value = getattr(get_settings(), "cost_hours_per_month", None)
        if isinstance(value, (int, float, Decimal)) and Decimal(value) > 0:
            return Decimal(str(value))
        return _DEFAULT_MONTH_HOURS

    def _finalize_cost(
        self,
        provider: CloudProvider,
        breakdown: list[tuple[str, str, Decimal, str | None]],
        assumptions: list[str],
    ) -> ProvisioningCostEstimate:
        month_hours = self._monthly_hours()
        line_items: list[CostEstimateLineItem] = []
        hourly = Decimal("0")
        for item_id, label, line_hourly, note in breakdown:
            clamped = max(line_hourly, Decimal("0"))
            line_items.append(
                CostEstimateLineItem(
                    id=item_id,
                    label=label,
                    hourly_usd=float(clamped.quantize(Decimal("0.0001"))),
                    monthly_usd=float((clamped * month_hours).quantize(Decimal("0.01"))),
                    note=note,
                )
            )
            hourly += clamped
        monthly = hourly * month_hours
        return ProvisioningCostEstimate(
            provider=provider,
            hourly_usd=float(hourly.quantize(Decimal("0.0001"))),
            monthly_usd=float(monthly.quantize(Decimal("0.01"))),
            breakdown=line_items,
            assumptions=assumptions,
        )

    def _compute_discount_factor(self, request: ProvisioningWizardRequest) -> Decimal:
        cost = request.cost_optimization
        if not cost.spot_scheduling.enabled:
            return Decimal("1")
        allocation = Decimal(str(cost.spot_scheduling.allocation_percent)) / Decimal("100")
        # Approximate blended discount: up to 60% savings on spot share.
        discount = allocation * Decimal("0.6")
        return max(Decimal("0.2"), Decimal("1") - discount)

    def _estimate_gcp(
        self,
        resources: GcpResources,
        request: ProvisioningWizardRequest,
    ) -> ProvisioningCostEstimate:
        discount = self._compute_discount_factor(request)
        compute_rate = _GCP_MACHINE_RATES.get(resources.machine_type, _DEFAULT_GCP_COMPUTE_HOURLY)
        breakdown: list[tuple[str, str, Decimal, str | None]] = []
        assumptions = ["Rates are directional estimates, check provider calculator before production."]

        if resources.gke or resources.cloud_run or resources.cloud_functions:
            breakdown.append(
                ("compute", f"Compute ({resources.machine_type})", compute_rate * discount, None)
            )
        if resources.cloud_sql:
            breakdown.append(("cloud_sql", "Cloud SQL", Decimal("0.11"), None))
        if resources.memorystore:
            mem_rate = Decimal("0.05") if resources.memorystore_engine.value == "redis" else Decimal("0.04")
            breakdown.append(("memorystore", "Memorystore", mem_rate, None))
        if resources.artifact_registry:
            breakdown.append(("artifact_registry", "Artifact Registry", Decimal("0.01"), None))
        if resources.bigquery:
            breakdown.append(("bigquery", "BigQuery baseline", Decimal("0.02"), "Storage and query volume varies"))
        if resources.pubsub:
            breakdown.append(("pubsub", "Pub/Sub baseline", Decimal("0.01"), "Traffic-dependent"))
        return self._finalize_cost(CloudProvider.GCP, breakdown, assumptions)

    def _estimate_aws(
        self,
        resources: AwsResources,
        request: ProvisioningWizardRequest,
    ) -> ProvisioningCostEstimate:
        discount = self._compute_discount_factor(request)
        compute_rate = _AWS_INSTANCE_RATES.get(resources.instance_type, _DEFAULT_AWS_COMPUTE_HOURLY)
        breakdown: list[tuple[str, str, Decimal, str | None]] = []
        assumptions = ["Rates are directional estimates, check provider calculator before production."]

        if resources.ec2 or resources.eks or resources.app_runner or resources.lambda_fn:
            breakdown.append(
                ("compute", f"Compute ({resources.instance_type})", compute_rate * discount, None)
            )
        if resources.rds:
            breakdown.append(("rds", "RDS", Decimal("0.12"), None))
        if resources.elasticache:
            cache_rate = Decimal("0.06") if resources.elasticache_engine.value == "redis" else Decimal("0.05")
            breakdown.append(("elasticache", "ElastiCache", cache_rate, None))
        if resources.alb:
            breakdown.append(("network", "NAT/ALB baseline", Decimal("0.05"), "Data transfer excluded"))
        if resources.ecr:
            breakdown.append(("ecr", "ECR baseline", Decimal("0.01"), None))
        return self._finalize_cost(CloudProvider.AWS, breakdown, assumptions)

    def _estimate_azure(
        self,
        resources: AzureResources,
        request: ProvisioningWizardRequest,
    ) -> ProvisioningCostEstimate:
        discount = self._compute_discount_factor(request)
        compute_rate = _AZURE_VM_RATES.get(resources.vm_size, _DEFAULT_AZURE_COMPUTE_HOURLY)
        breakdown: list[tuple[str, str, Decimal, str | None]] = []
        assumptions = ["Rates are directional estimates, check provider calculator before production."]

        if resources.aks or resources.container_apps or resources.app_service:
            breakdown.append(("compute", f"Compute ({resources.vm_size})", compute_rate * discount, None))
        if resources.cosmos_db:
            breakdown.append(("cosmos_db", "Cosmos DB", Decimal("0.12"), "Provisioned RU profile"))
        if resources.redis_cache:
            breakdown.append(("redis", "Azure Cache for Redis", Decimal("0.06"), None))
        if resources.log_analytics:
            breakdown.append(("logs", "Log Analytics baseline", Decimal("0.02"), "Ingestion-dependent"))
        return self._finalize_cost(CloudProvider.AZURE, breakdown, assumptions)

    def _estimate_cloudflare(
        self,
        resources: CloudflareResources,
        request: ProvisioningWizardRequest,
    ) -> ProvisioningCostEstimate:
        _ = request
        breakdown: list[tuple[str, str, Decimal, str | None]] = []
        assumptions = ["Cloudflare bills are often request-volume based, estimates are baseline only."]
        for key, enabled, label in (
            ("workers", resources.workers, "Workers"),
            ("r2", resources.r2, "R2"),
            ("pages", resources.pages, "Pages"),
            ("d1", resources.d1, "D1"),
            ("queues", resources.queues, "Queues"),
            ("kv", resources.kv, "KV"),
            ("tunnels", resources.tunnels, "Tunnel"),
        ):
            if enabled:
                breakdown.append((key, label, _DEFAULT_CLOUDFLARE_SERVICE_HOURLY, "Usage-dependent"))
        return self._finalize_cost(CloudProvider.CLOUDFLARE, breakdown, assumptions)

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

        # Local cluster ensure stays out of regenerate/update so IaC + container
        # scaffold writes return quickly; open_terminal / Celery still bring Kind up.

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
        merged = await self._fill_from_account_vault(
            merged,
            owner.id,
            provider=request.cloud.provider.value,
        )
        effective = request.model_copy(update={"credentials": merged})
        effective = self._with_gcp_project_from_sa(effective)
        files = self._iac.regenerate(workspace_path, effective)

        preserve: dict[str, object] | None = None
        if row.wizard_config_json:
            try:
                raw = json.loads(row.wizard_config_json)
            except json.JSONDecodeError:
                raw = None
            if isinstance(raw, dict):
                preserve = raw

        row.engine = effective.iac_engine.value
        row.provider = effective.cloud.provider.value
        row.encrypted_credentials = encrypt_secret(merged.model_dump_json())
        row.wizard_config_json = self._wizard_config_json(effective, preserve=preserve)
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
            runtime_mode=effective.runtime_mode,
            name=row.name,
            status=row.status,
            created_at=row.created_at,
            starred=row.starred_at is not None,
        )

    async def apply_cloud_plugin_to_workspace(
        self,
        workspace_id: UUID,
        *,
        owner: User,
        cloud_plugin: CloudPluginTarget,
    ) -> None:
        """Persist launch/promote plugin target onto the workspace (region + VM flags)."""
        from app.core.secrets import decrypt_secret
        from app.schemas.cloud import CloudCredentials, ProvisioningWizardRequest
        from app.services.cloud_plugin_defaults import apply_cloud_plugin_defaults

        row = await self.get_workspace_for_owner(workspace_id, owner)
        config = await self.get_wizard_config(workspace_id, owner)
        credentials = CloudCredentials()
        if row.encrypted_credentials:
            try:
                credentials = CloudCredentials.model_validate_json(
                    decrypt_secret(row.encrypted_credentials)
                )
            except Exception:
                logger.warning(
                    "cloud_plugin_credentials_unreadable",
                    workspace_id=str(workspace_id),
                )

        request = ProvisioningWizardRequest(
            name=config.name,
            iac_engine=config.iac_engine,
            cloud=config.cloud,
            credentials=credentials,
            run_init=config.run_init,
            runtime_mode=config.runtime_mode,
            running_instance=config.running_instance,
            artifact_mode=config.artifact_mode,
            kubernetes_packaging=config.kubernetes_packaging,
            kubernetes_options=config.kubernetes_options,
            cost_optimization=config.cost_optimization,
            container_scaffold=config.container_scaffold,
            dependencies=config.dependencies,
            ansible=config.ansible,
            config_tool=config.config_tool,
            env_vars=config.env_vars,
            cloud_plugin=config.cloud_plugin,
        )
        updated = apply_cloud_plugin_defaults(request, cloud_plugin)
        if updated.model_dump(mode="json") == request.model_dump(mode="json"):
            return
        await self.update_workspace(workspace_id, updated, owner=owner)
        logger.info(
            "cloud_plugin_applied_to_workspace",
            workspace_id=str(workspace_id),
            plugin=cloud_plugin.model_dump(mode="json"),
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
        filled = await self._fill_from_account_vault(
            request.credentials,
            owner.id,
            provider=request.cloud.provider.value,
        )
        if filled == request.credentials:
            return request
        return request.model_copy(update={"credentials": filled})

    async def _fill_from_account_vault(
        self,
        credentials: CloudCredentials,
        user_id: UUID,
        *,
        provider: str | None = None,
    ) -> CloudCredentials:
        """Merge account vault into workspace credentials.

        Service account / WIF keys are preferred. Connect OAuth and other vault
        fields only fill blanks. When ``provider`` is set, only that cloud's
        vault fields are merged so a GCP Connect token cannot poison AWS deploys.
        """
        from app.core.secrets import has_aws_auth, has_gcp_auth
        from app.services.user_credentials import (
            UserCloudCredentialsService,
            UserCloudCredentialsVaultError,
        )

        try:
            vault = await UserCloudCredentialsService(self._session).get_credentials(user_id)
        except UserCloudCredentialsVaultError:
            return credentials
        updates: dict[str, str | None] = {}
        data = credentials.model_dump()
        vault_data = vault.model_dump()
        provider_norm = (provider or "").strip().lower()

        def _key_allowed(key: str) -> bool:
            if not provider_norm or provider_norm == "local":
                return True
            if provider_norm == "gcp":
                return key.startswith("gcp_")
            if provider_norm == "aws":
                return key.startswith("aws_")
            if provider_norm == "azure":
                return key.startswith("azure_")
            if provider_norm == "cloudflare":
                return key.startswith("cloudflare_")
            return True

        if vault.gcp_project_id and _key_allowed("gcp_project_id"):
            updates["gcp_project_id"] = vault.gcp_project_id

        gcp_ok = has_gcp_auth(credentials)
        aws_ok = has_aws_auth(credentials)
        azure_ok = bool(
            credentials.azure_client_id
            and credentials.azure_client_secret
            and credentials.azure_tenant_id
            and credentials.azure_subscription_id
        ) or bool(credentials.azure_oauth_token_json)
        cf_ok = bool(credentials.cloudflare_api_token)

        for key, value in vault_data.items():
            if not value or not str(value).strip():
                continue
            if key in updates:
                continue
            if not _key_allowed(key):
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

    async def destroy_workspace(
        self, workspace_id: UUID, owner: User
    ) -> WorkspaceListItem:
        """Mark workspace deleting, cascade env teardowns, finalize in Celery.

        Returns immediately with status ``deleting`` so the UI can show a
        terminating state. Cloud IaC destroy and disk/row removal run after
        linked environments reach DESTROYED (see finalize_workspace_destroy).
        """
        row = await self.get_workspace_for_owner(workspace_id, owner)
        if row.status in {"deleting", "destroy_failed"}:
            # Re-enqueue finalize so a stuck ``deleting`` row (worker crash / timeout)
            # or a prior ``destroy_failed`` can complete without a DB surgery.
            from app.workers.tasks import enqueue_finalize_workspace_destroy

            enqueue_finalize_workspace_destroy(
                workspace_id=str(workspace_id),
                owner_id=str(owner.id),
            )
            logger.info(
                "provisioning_workspace_delete_requeued",
                workspace_id=str(workspace_id),
                status=row.status,
            )
            if row.status == "destroy_failed":
                row.status = "deleting"
                await self._session.commit()
                await self._session.refresh(row)
            return self._to_workspace_list_item(row)

        result = await self._session.execute(
            select(TerminalSessionRecord).where(
                TerminalSessionRecord.workspace_id == workspace_id,
                TerminalSessionRecord.status == "active",
            )
        )
        for record in result.scalars().all():
            await self._sandbox.kill(str(record.id))
            record.status = "destroyed"

        row.status = "deleting"
        cascaded_envs = await self._teardown_workspace_previews(workspace_id)
        await self._session.commit()
        await self._session.refresh(row)

        from app.workers.tasks import enqueue_finalize_workspace_destroy

        enqueue_finalize_workspace_destroy(
            workspace_id=str(workspace_id),
            owner_id=str(owner.id),
        )
        logger.info(
            "provisioning_workspace_delete_queued",
            workspace_id=str(workspace_id),
            cascaded_envs=cascaded_envs,
        )
        return self._to_workspace_list_item(row)

    async def finalize_workspace_destroy(
        self, workspace_id: UUID, owner: User
    ) -> None:
        """Complete workspace destroy after envs are gone (Celery / tests)."""
        row = await self.get_workspace_for_owner(workspace_id, owner)
        if row.status not in {"deleting", "destroy_failed"}:
            logger.info(
                "workspace_finalize_skipped",
                workspace_id=str(workspace_id),
                status=row.status,
            )
            return

        row.status = "deleting"
        await self._session.commit()

        # Snapshot scalars before wait/expire_all so finalize never lazy-loads
        # expired attributes in a sync helper path.
        was_local = row.provider == CloudProvider.LOCAL.value
        root_dir = row.root_dir
        engine = row.engine
        provider = row.provider
        org_id = row.org_id
        encrypted_credentials = row.encrypted_credentials

        await self._wait_workspace_environments_destroyed(workspace_id)
        await self._session.refresh(row)

        from app.core.config import get_settings

        settings = get_settings()
        if not was_local and settings.iac_destroy_on_workspace_delete:
            from app.services.iac_destroy import run_workspace_iac_destroy

            credentials: CloudCredentials | None = None
            if encrypted_credentials:
                try:
                    credentials = CloudCredentials.model_validate_json(
                        decrypt_secret(encrypted_credentials)
                    )
                except Exception:  # noqa: BLE001 - proceed without creds; destroy will skip/fail clearly
                    logger.warning(
                        "workspace_credentials_decrypt_failed",
                        workspace_id=str(workspace_id),
                    )
            destroy_result = await asyncio.to_thread(
                run_workspace_iac_destroy,
                root_dir=root_dir,
                engine=engine,
                credentials=credentials,
                org_id=str(org_id) if org_id else "default-org",
                workspace_id=str(row.id),
                settings=settings,
            )
            logger.info(
                "workspace_iac_destroy",
                workspace_id=str(workspace_id),
                status=destroy_result.status,
                detail=destroy_result.detail,
            )
            if destroy_result.status == "failed":
                from app.services.iac_destroy import workspace_cloud_infra_cleared

                # Env teardown often runs the same terraform destroy in parallel.
                # If state is already gone, treat as success so the row is not stuck.
                if workspace_cloud_infra_cleared(root_dir=root_dir, engine=engine):
                    logger.info(
                        "workspace_iac_destroy_race_recovered",
                        workspace_id=str(workspace_id),
                        detail=destroy_result.detail,
                    )
                else:
                    destroy_result = await asyncio.to_thread(
                        run_workspace_iac_destroy,
                        root_dir=root_dir,
                        engine=engine,
                        credentials=credentials,
                        org_id=str(org_id) if org_id else "default-org",
                        workspace_id=str(row.id),
                        settings=settings,
                    )
                    if (
                        destroy_result.status == "failed"
                        and not workspace_cloud_infra_cleared(
                            root_dir=root_dir, engine=engine
                        )
                    ):
                        row.status = "destroy_failed"
                        await self._session.commit()
                        logger.error(
                            "workspace_iac_destroy_failed_kept",
                            workspace_id=str(workspace_id),
                            detail=destroy_result.detail,
                            output=(destroy_result.output or "")[-1500:],
                        )
                        return
                    logger.info(
                        "workspace_iac_destroy_retry_ok",
                        workspace_id=str(workspace_id),
                        status=destroy_result.status,
                        detail=destroy_result.detail,
                    )

        from app.models.domain import Environment, EnvironmentStatus
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
            root_dir,
            workload_images=[
                env.workload_image for env in env_rows if env.workload_image
            ],
        )

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

        if not was_local and destroy_images and encrypted_credentials:
            try:
                from app.services.cloud_instance_compute import (
                    is_cloud_registry_image,
                    teardown_cloud_registry_images,
                )

                credentials = CloudCredentials.model_validate_json(
                    decrypt_secret(encrypted_credentials)
                )
                region = "us-central1"
                snapshot = self._load_wizard_snapshot(row)
                if snapshot:
                    try:
                        wiz = WorkspaceWizardConfig.model_validate(
                            {**snapshot, "has_credentials": False}
                        )
                        if wiz.running_instance.region:
                            region = wiz.running_instance.region
                    except Exception:
                        pass
                cloud_images = [
                    img for img in destroy_images if is_cloud_registry_image(img)
                ]
                if env_rows:
                    for env_row in env_rows:
                        env_images = list(cloud_images)
                        if env_row.workload_image and is_cloud_registry_image(
                            env_row.workload_image
                        ):
                            if env_row.workload_image not in env_images:
                                env_images.append(env_row.workload_image)
                        await asyncio.to_thread(
                            teardown_cloud_registry_images,
                            env_images,
                            cloud_provider=provider,
                            credentials=credentials,
                            region=region,
                            environment_id=str(env_row.id),
                        )
                elif cloud_images:
                    await asyncio.to_thread(
                        teardown_cloud_registry_images,
                        cloud_images,
                        cloud_provider=provider,
                        credentials=credentials,
                        region=region,
                        environment_id=None,
                    )
            except Exception:
                logger.exception(
                    "workspace_cloud_registry_cleanup_failed",
                    workspace_id=str(workspace_id),
                )

        live_envs = sum(
            1 for env in env_rows if env.status != EnvironmentStatus.DESTROYED
        )
        if not was_local and live_envs == 0:
            await self._maybe_teardown_shared_cloud_kubernetes(owner, row)

        self._iac.destroy_workspace(root_dir)
        await self._session.delete(row)
        await self._session.commit()
        logger.info("provisioning_workspace_destroyed", workspace_id=str(workspace_id))

        if was_local:
            await self._maybe_teardown_kind(owner)

    async def _wait_workspace_environments_destroyed(
        self,
        workspace_id: UUID,
        *,
        timeout_seconds: float = 90.0,
        poll_seconds: float = 1.0,
    ) -> None:
        """Block until linked environments are DESTROYED (or force after timeout).

        Keep this short: Celery soft_time_limit must still leave room for IaC
        destroy. Failed / never-provisioned envs are force-destroyed so workspace
        delete cannot sit on ``deleting`` for tens of minutes.
        """
        import time

        from app.models.domain import Environment, EnvironmentStatus
        from app.repositories.environment import EnvironmentRepository
        from app.workers.tasks import enqueue_teardown_environment

        deadline = time.monotonic() + timeout_seconds
        last_requeue = 0.0
        while True:
            result = await self._session.execute(
                select(Environment)
                .where(Environment.workspace_id == workspace_id)
                .execution_options(populate_existing=True)
            )
            remaining = [
                env
                for env in result.scalars().all()
                if env.status != EnvironmentStatus.DESTROYED
            ]
            if not remaining:
                return

            # Force-complete terminal failures quickly (nothing cloud-side to wait for).
            force_now = all(
                env.status == EnvironmentStatus.FAILED for env in remaining
            )
            if force_now or time.monotonic() >= deadline:
                env_repo = EnvironmentRepository(self._session)
                for env in remaining:
                    logger.warning(
                        "workspace_destroy_force_env_destroyed",
                        workspace_id=str(workspace_id),
                        environment_id=str(env.id),
                        status=env.status.value,
                        forced_early=force_now,
                    )
                    env.teardown_context_json = None
                    await env_repo.update_status(env, EnvironmentStatus.DESTROYED)
                await self._session.commit()
                return

            now = time.monotonic()
            if now - last_requeue >= 15.0:
                for env in remaining:
                    if env.status in {
                        EnvironmentStatus.TEARDOWN_PENDING,
                        EnvironmentStatus.FAILED,
                    }:
                        enqueue_teardown_environment(
                            environment_id=str(env.id),
                            correlation_id=f"workspace-destroy-wait:{workspace_id}",
                        )
                last_requeue = now
            await asyncio.sleep(poll_seconds)

    async def _teardown_workspace_previews(self, workspace_id: UUID) -> int:
        """Force teardown of all preview environments belonging to a workspace.

        Each non-DESTROYED environment is marked TEARDOWN_PENDING (which also
        cancels any in-flight provision) and its teardown task is enqueued -
        including environments already TEARDOWN_PENDING (re-queue after restart).

        Returns the number of environments cascaded.
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
            if env.status != EnvironmentStatus.DESTROYED
        ]
        if not active:
            return 0
        env_repo = EnvironmentRepository(self._session)
        from app.services.teardown_context import capture_environment_teardown_context

        for env in active:
            if env.status != EnvironmentStatus.TEARDOWN_PENDING:
                await capture_environment_teardown_context(self._session, env)
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
        return len(active)

    async def _maybe_teardown_shared_cloud_kubernetes(
        self, owner: User, workspace: ProvisioningWorkspace
    ) -> None:
        """Delete shared launchpad-previews when this was the last cloud workspace."""
        from app.models.domain import Environment, EnvironmentStatus
        from app.schemas.k8s import DeployMode
        from app.services.cloud_kubernetes import (
            is_cloud_kubernetes_provider,
            region_from_wizard,
            teardown_shared_preview_cluster,
        )

        provider = str(workspace.provider or "").strip().lower()
        if not is_cloud_kubernetes_provider(provider):
            return

        remaining_ws = await self._session.execute(
            select(ProvisioningWorkspace.id)
            .where(
                ProvisioningWorkspace.owner_id == owner.id,
                ProvisioningWorkspace.provider == provider,
                ProvisioningWorkspace.id != workspace.id,
            )
            .limit(1)
        )
        if remaining_ws.scalar_one_or_none() is not None:
            logger.info(
                "shared_cloud_k8s_retained",
                reason="other_cloud_workspaces",
                provider=provider,
            )
            return

        remaining_envs = await self._session.execute(
            select(Environment.id)
            .where(
                Environment.owner_id == owner.id,
                Environment.provider == provider,
                Environment.status != EnvironmentStatus.DESTROYED,
                Environment.deploy_mode.in_(
                    [DeployMode.MANIFEST.value, DeployMode.PREVIEW.value]
                ),
            )
            .limit(1)
        )
        if remaining_envs.scalar_one_or_none() is not None:
            logger.info(
                "shared_cloud_k8s_retained",
                reason="other_cloud_k8s_environments",
                provider=provider,
            )
            return

        credentials: CloudCredentials | None = None
        if workspace.encrypted_credentials:
            try:
                credentials = CloudCredentials.model_validate_json(
                    decrypt_secret(workspace.encrypted_credentials)
                )
            except Exception:
                credentials = None
        credentials = await self._fill_from_account_vault(
            credentials or CloudCredentials(),
            owner.id,
            provider=provider,
        )
        from app.core.secrets import has_aws_auth, has_gcp_auth

        if provider == CloudProvider.AWS.value and not has_aws_auth(credentials):
            logger.warning(
                "shared_cloud_k8s_teardown_skipped_no_credentials",
                provider=provider,
                workspace_id=str(workspace.id),
            )
            return
        if provider == CloudProvider.GCP.value and not has_gcp_auth(credentials):
            logger.warning(
                "shared_cloud_k8s_teardown_skipped_no_credentials",
                provider=provider,
                workspace_id=str(workspace.id),
            )
            return
        snapshot = self._load_wizard_snapshot(workspace)
        region = region_from_wizard(provider, snapshot, credentials=credentials)
        try:
            await asyncio.to_thread(
                teardown_shared_preview_cluster,
                provider=provider,
                credentials=credentials,
                region=region,
                environment_id=str(workspace.id),
                wait=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "shared_cloud_k8s_teardown_on_workspace_destroy_failed",
                workspace_id=str(workspace.id),
                provider=provider,
                error=str(exc)[:400],
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
        credentials = await self._fill_from_account_vault(
            credentials,
            owner.id,
            provider=workspace.provider,
        )

        workspace_path = self._iac.get_workspace(workspace.root_dir)

        # Bring up the local cluster when opening the sandbox (progress step 4),
        # not during IaC generation, so createWorkspace does not time out.
        if (workspace.provider or "").lower() == CloudProvider.LOCAL.value:
            config = await self.get_wizard_config(workspace_id, owner)
            if config.runtime_mode == WorkspaceRuntimeMode.KUBERNETES:
                cluster_name = None
                if isinstance(config.cloud, LocalCloudConfig):
                    cluster_name = config.cloud.resources.cluster_name
                try:
                    await ensure_kind_cluster(cluster_name=cluster_name)
                except RuntimeError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail={
                            "code": "kind_cluster_unavailable",
                            "message": str(exc),
                        },
                    ) from exc

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

    def _control_plane_url(self) -> str:
        from app.core.config import get_settings

        settings = get_settings()
        raw = (
            (settings.agent_control_plane_url or "").strip()
            or (settings.preview_public_base_url or "").strip()
            or "http://localhost:8000"
        )
        return raw.rstrip("/")

    def _parse_wizard_dict(self, row: ProvisioningWorkspace) -> dict[str, object]:
        try:
            raw = json.loads(row.wizard_config_json or "") if row.wizard_config_json else {}
        except json.JSONDecodeError:
            return {}
        return raw if isinstance(raw, dict) else {}

    def _linked_app_repo_from_snapshot(
        self, snapshot: dict[str, object]
    ) -> WorkspaceLinkedAppRepo | None:
        raw = snapshot.get("linked_app_repo")
        if not isinstance(raw, dict):
            return None
        try:
            return WorkspaceLinkedAppRepo.model_validate(raw)
        except Exception:
            return None

    async def get_linked_app_repo(
        self, workspace_id: UUID, owner: User
    ) -> WorkspaceLinkedAppRepoResponse:
        from app.core.config import get_settings

        row = await self.get_workspace_for_owner(workspace_id, owner)
        snapshot = self._parse_wizard_dict(row)
        linked = self._linked_app_repo_from_snapshot(snapshot)
        settings = get_settings()
        webhook_configured = bool((settings.webhook_secret or "").strip())
        message = (
            "Push to the tracked branch rebuilds active environments when the "
            "GitHub webhook is configured."
            if linked and linked.cd_mode == WorkspaceCdMode.WEBHOOK
            else (
                "GitHub Actions notifies Launchpad on push to the tracked branch."
                if linked
                else "No application repository linked yet."
            )
        )
        return WorkspaceLinkedAppRepoResponse(
            linked=linked,
            webhook_configured=webhook_configured,
            control_plane_url=self._control_plane_url(),
            message=message,
            workflow_path=linked.workflow_path if linked else None,
        )

    async def set_linked_app_repo(
        self,
        workspace_id: UUID,
        request: WorkspaceLinkedAppRepoRequest,
        *,
        owner: User,
    ) -> WorkspaceLinkedAppRepoResponse:
        from datetime import UTC, datetime

        from app.core.config import get_settings
        from app.models.domain import Environment, EnvironmentStatus
        from app.repositories.environment import ACTIVE_REBUILD_STATUSES

        row = await self.get_workspace_for_owner(workspace_id, owner)
        snapshot = self._parse_wizard_dict(row)
        settings = get_settings()
        webhook_configured = bool((settings.webhook_secret or "").strip())
        environments_updated = 0
        workflow_path: str | None = None

        if request.clear:
            previous = self._linked_app_repo_from_snapshot(snapshot)
            snapshot.pop("linked_app_repo", None)
            if previous is not None:
                if snapshot.get("git_repo_url") == previous.git_repo_url:
                    # Drop mirrored git fields unless this looks like a repo_import
                    # snapshot that should keep its own source metadata.
                    if snapshot.get("source") != "repo_import":
                        snapshot.pop("git_repo_url", None)
                        snapshot.pop("git_branch", None)
            row.wizard_config_json = json.dumps(snapshot)
            await self._session.commit()
            return WorkspaceLinkedAppRepoResponse(
                linked=None,
                webhook_configured=webhook_configured,
                control_plane_url=self._control_plane_url(),
                message="Application repository unlinked.",
                environments_updated=0,
            )

        assert request.installation_id is not None and request.full_name
        full_name = request.full_name
        git_repo_url = f"https://github.com/{full_name}.git"
        git_branch = request.git_branch
        cd_mode = request.cd_mode

        if cd_mode == WorkspaceCdMode.GITHUB_ACTIONS:
            cd_secret = (settings.webhook_secret or "").strip() or None
            if not cd_secret:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "webhook_secret_required",
                        "message": (
                            "CD mode github_actions requires WEBHOOK_SECRET on the API "
                            "so Launchpad can set LAUNCHPAD_CD_SECRET on the repo."
                        ),
                    },
                )
            try:
                workflow_path = await asyncio.to_thread(
                    self._github.ensure_app_cd_workflow,
                    installation_id=request.installation_id,
                    full_name=full_name,
                    branch=git_branch,
                    control_plane_url=self._control_plane_url(),
                    cd_secret=cd_secret,
                    workspace_id=str(workspace_id),
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "github_app_cd_failed",
                        "message": str(exc),
                    },
                ) from exc

        linked = WorkspaceLinkedAppRepo(
            installation_id=request.installation_id,
            full_name=full_name,
            git_repo_url=git_repo_url,
            git_branch=git_branch,
            cd_mode=cd_mode,
            workflow_path=workflow_path,
            updated_at=datetime.now(UTC),
        )
        snapshot["linked_app_repo"] = linked.model_dump(mode="json")
        snapshot["git_repo_url"] = git_repo_url
        snapshot["git_branch"] = git_branch
        row.wizard_config_json = json.dumps(snapshot)

        env_rows = (
            await self._session.execute(
                select(Environment).where(
                    Environment.workspace_id == workspace_id,
                    Environment.status.in_(
                        (*ACTIVE_REBUILD_STATUSES, EnvironmentStatus.PAUSED)
                    ),
                )
            )
        ).scalars().all()
        for env in env_rows:
            env.git_repo_url = git_repo_url
            env.git_branch = git_branch
            environments_updated += 1

        await self._session.commit()
        logger.info(
            "workspace_linked_app_repo_saved",
            workspace_id=str(workspace_id),
            full_name=full_name,
            git_branch=git_branch,
            cd_mode=cd_mode.value,
            environments_updated=environments_updated,
            workflow_path=workflow_path,
        )

        if cd_mode == WorkspaceCdMode.WEBHOOK:
            message = (
                "Linked with webhook CD (default). Point GitHub at "
                f"{self._control_plane_url()}/api/v1/webhooks/github "
                "using WEBHOOK_SECRET. Push to the tracked branch rebuilds "
                "matching environments."
            )
            if not webhook_configured:
                message += " WEBHOOK_SECRET is not set on the API yet."
        else:
            message = (
                f"Linked with GitHub Actions CD. Workflow `{workflow_path}` "
                "notifies Launchpad on push to the tracked branch."
            )

        return WorkspaceLinkedAppRepoResponse(
            linked=linked,
            webhook_configured=webhook_configured,
            control_plane_url=self._control_plane_url(),
            message=message,
            workflow_path=workflow_path,
            environments_updated=environments_updated,
        )

    async def set_workspace_git_source(
        self,
        workspace_id: UUID,
        request: WorkspaceGitSourceRequest,
        *,
        owner: User,
    ) -> WorkspaceGitSourceResponse:
        """Persist git_repo_url/git_branch for webhook rebuild matching (e.g. GitLab)."""
        from app.models.domain import Environment

        row = await self.get_workspace_for_owner(workspace_id, owner)
        snapshot = self._parse_wizard_dict(row)
        environments_updated = 0

        if request.clear:
            snapshot.pop("git_repo_url", None)
            snapshot.pop("git_branch", None)
            row.wizard_config_json = json.dumps(snapshot)
            await self._session.commit()
            return WorkspaceGitSourceResponse(
                git_repo_url=None,
                git_branch="main",
                message="Git source cleared.",
                environments_updated=0,
            )

        assert request.git_repo_url
        git_repo_url = request.git_repo_url
        git_branch = request.git_branch
        snapshot["git_repo_url"] = git_repo_url
        snapshot["git_branch"] = git_branch
        row.wizard_config_json = json.dumps(snapshot)

        env_rows = (
            await self._session.execute(
                select(Environment).where(Environment.workspace_id == workspace_id)
            )
        ).scalars().all()
        for env in env_rows:
            env.git_repo_url = git_repo_url
            env.git_branch = git_branch
            environments_updated += 1

        await self._session.commit()
        logger.info(
            "workspace_git_source_saved",
            workspace_id=str(workspace_id),
            git_branch=git_branch,
            environments_updated=environments_updated,
        )
        return WorkspaceGitSourceResponse(
            git_repo_url=git_repo_url,
            git_branch=git_branch,
            message=(
                f"Tracking {git_repo_url}@{git_branch}. "
                f"Updated {environments_updated} environment(s)."
            ),
            environments_updated=environments_updated,
        )

    # Tokens (whole words in the repo leaf) that mark a repo as the frontend/UI.
    _FRONTEND_TOKENS = frozenset({
        "frontend", "front", "web", "webapp", "ui", "client", "www", "site", "dashboard",
    })

    @classmethod
    def _is_frontend_repo(cls, item: WorkspaceLinkedRepoItem) -> bool:
        """Heuristic: is this linked repo the frontend? (by repo name tokens)."""
        import re

        text = (item.full_name or item.git_repo_url or "").lower()
        leaf = text.rstrip("/").split("/")[-1].removesuffix(".git")
        tokens = {t for t in re.split(r"[^a-z0-9]+", leaf) if t}
        return bool(tokens & cls._FRONTEND_TOKENS)

    @classmethod
    def _choose_primary_repo_index(cls, repos: list[WorkspaceLinkedRepoItem]) -> int:
        """Explicit primary wins; else the frontend repo; else the first."""
        for i, repo in enumerate(repos):
            if getattr(repo, "primary", False):
                return i
        for i, repo in enumerate(repos):
            if cls._is_frontend_repo(repo):
                return i
        return 0

    @staticmethod
    def _repo_service_name(item: WorkspaceLinkedRepoItem) -> str:
        """Service-node name for a linked repo (the repo leaf, K8s-safe)."""
        import re

        base = (item.full_name or "").strip()
        leaf = base.split("/")[-1] if base else ""
        if not leaf:
            url = (item.git_repo_url or "").strip().rstrip("/")
            leaf = url.split("/")[-1]
            if leaf.endswith(".git"):
                leaf = leaf[:-4]
        return re.sub(r"[^a-z0-9-]+", "-", leaf.lower()).strip("-")[:63]

    def _linked_repos_from_snapshot(
        self, snapshot: dict[str, object]
    ) -> list[WorkspaceLinkedRepoItem]:
        """Read the linked-repos list, falling back to a legacy single link."""
        raw = snapshot.get("linked_repos")
        items: list[WorkspaceLinkedRepoItem] = []
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                try:
                    items.append(WorkspaceLinkedRepoItem.model_validate(entry))
                except Exception:  # noqa: BLE001, S112 - tolerate malformed persisted data
                    continue
        if items:
            return items
        # Backward compat: synthesize a 1-item list from a legacy single link so the
        # UI shows an existing GitHub App / git source as the first linked repo.
        legacy = self._linked_app_repo_from_snapshot(snapshot)
        if legacy is not None:
            return [
                WorkspaceLinkedRepoItem(
                    kind=WorkspaceRepoKind.GITHUB,
                    git_repo_url=legacy.git_repo_url,
                    git_branch=legacy.git_branch,
                    full_name=legacy.full_name,
                    installation_id=legacy.installation_id,
                    cd_mode=legacy.cd_mode,
                    workflow_path=legacy.workflow_path,
                    primary=True,
                )
            ]
        url = snapshot.get("git_repo_url")
        if isinstance(url, str) and url.strip():
            branch = snapshot.get("git_branch")
            return [
                WorkspaceLinkedRepoItem(
                    kind=WorkspaceRepoKind.GITLAB,
                    git_repo_url=url.strip(),
                    git_branch=str(branch).strip() if isinstance(branch, str) and branch.strip() else "main",
                    primary=True,
                )
            ]
        return []

    async def get_workspace_linked_repos(
        self, workspace_id: UUID, owner: User
    ) -> WorkspaceLinkedReposResponse:
        from app.core.config import get_settings

        row = await self.get_workspace_for_owner(workspace_id, owner)
        snapshot = self._parse_wizard_dict(row)
        repos = self._linked_repos_from_snapshot(snapshot)
        settings = get_settings()
        primary = repos[0] if repos else None
        return WorkspaceLinkedReposResponse(
            repos=repos,
            primary_git_repo_url=primary.git_repo_url if primary else None,
            primary_git_branch=primary.git_branch if primary else "main",
            webhook_configured=bool((settings.webhook_secret or "").strip()),
            control_plane_url=self._control_plane_url(),
            message=(
                f"{len(repos)} repository(ies) linked."
                if repos
                else "No repositories linked yet."
            ),
        )

    async def set_workspace_linked_repos(
        self,
        workspace_id: UUID,
        request: WorkspaceLinkedReposRequest,
        *,
        owner: User,
    ) -> WorkspaceLinkedReposResponse:
        """Replace the workspace's linked-repo list (primary = first item).

        Multi-repo aware and backward compatible: the primary drives the legacy
        git_repo_url / linked_app_repo fields and environment mirroring, so existing
        single-repo consumers and webhook matching keep working. For github repos in
        github_actions CD mode, a per-repo workflow is installed (all scoped to this
        workspace), so a push to ANY linked repo rebuilds the workspace.
        """
        from datetime import UTC, datetime

        from app.core.config import get_settings
        from app.models.domain import Environment, EnvironmentStatus
        from app.repositories.environment import ACTIVE_REBUILD_STATUSES

        row = await self.get_workspace_for_owner(workspace_id, owner)
        snapshot = self._parse_wizard_dict(row)
        settings = get_settings()
        webhook_configured = bool((settings.webhook_secret or "").strip())

        if not request.repos:
            snapshot.pop("linked_repos", None)
            snapshot.pop("linked_app_repo", None)
            if snapshot.get("source") != "repo_import":
                snapshot.pop("git_repo_url", None)
                snapshot.pop("git_branch", None)
            row.wizard_config_json = json.dumps(snapshot)
            await self._session.commit()
            return WorkspaceLinkedReposResponse(
                repos=[],
                webhook_configured=webhook_configured,
                control_plane_url=self._control_plane_url(),
                message="All linked repositories cleared.",
            )

        stored: list[WorkspaceLinkedRepoItem] = []
        for item in request.repos:
            workflow_path = item.workflow_path
            if (
                item.kind == WorkspaceRepoKind.GITHUB
                and item.cd_mode == WorkspaceCdMode.GITHUB_ACTIONS
                and item.installation_id
                and item.full_name
            ):
                cd_secret = (settings.webhook_secret or "").strip() or None
                if not cd_secret:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "webhook_secret_required",
                            "message": (
                                "CD mode github_actions requires WEBHOOK_SECRET on the API "
                                "so Launchpad can set LAUNCHPAD_CD_SECRET on each repo."
                            ),
                        },
                    )
                try:
                    workflow_path = await asyncio.to_thread(
                        self._github.ensure_app_cd_workflow,
                        installation_id=item.installation_id,
                        full_name=item.full_name,
                        branch=item.git_branch,
                        control_plane_url=self._control_plane_url(),
                        cd_secret=cd_secret,
                        workspace_id=str(workspace_id),
                    )
                except ValueError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={"code": "github_app_cd_failed", "message": str(exc)},
                    ) from exc
            stored.append(item.model_copy(update={"workflow_path": workflow_path}))

        # Choose the primary repo (the one that deploys): an explicit user selection
        # wins; otherwise default to the frontend-looking repo; otherwise the first.
        # Reorder so the primary is index 0 and mark exactly one item primary.
        primary_idx = self._choose_primary_repo_index(stored)
        if primary_idx > 0:
            stored.insert(0, stored.pop(primary_idx))
        stored = [item.model_copy(update={"primary": i == 0}) for i, item in enumerate(stored)]

        snapshot["linked_repos"] = [i.model_dump(mode="json") for i in stored]

        # Primary drives the legacy single-repo fields (backward compatible).
        primary = stored[0]
        snapshot["git_repo_url"] = primary.git_repo_url
        snapshot["git_branch"] = primary.git_branch
        if primary.kind == WorkspaceRepoKind.GITHUB and primary.installation_id and primary.full_name:
            snapshot["linked_app_repo"] = WorkspaceLinkedAppRepo(
                installation_id=primary.installation_id,
                full_name=primary.full_name,
                git_repo_url=primary.git_repo_url,
                git_branch=primary.git_branch,
                cd_mode=primary.cd_mode,
                workflow_path=primary.workflow_path,
                updated_at=datetime.now(UTC),
            ).model_dump(mode="json")
        else:
            snapshot.pop("linked_app_repo", None)
        row.wizard_config_json = json.dumps(snapshot)

        environments_updated = 0
        env_rows = (
            await self._session.execute(
                select(Environment).where(
                    Environment.workspace_id == workspace_id,
                    Environment.status.in_(
                        (*ACTIVE_REBUILD_STATUSES, EnvironmentStatus.PAUSED)
                    ),
                )
            )
        ).scalars().all()
        for env in env_rows:
            env.git_repo_url = primary.git_repo_url
            env.git_branch = primary.git_branch
            environments_updated += 1

        await self._session.commit()
        logger.info(
            "workspace_linked_repos_saved",
            workspace_id=str(workspace_id),
            repo_count=len(stored),
            environments_updated=environments_updated,
        )
        return WorkspaceLinkedReposResponse(
            repos=stored,
            primary_git_repo_url=primary.git_repo_url,
            primary_git_branch=primary.git_branch,
            webhook_configured=webhook_configured,
            control_plane_url=self._control_plane_url(),
            message=f"Linked {len(stored)} repository(ies). Primary: {primary.git_repo_url}",
            environments_updated=environments_updated,
        )

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
    def _wizard_config_json(
        request: ProvisioningWizardRequest,
        *,
        preserve: dict[str, object] | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "name": request.name,
            "iac_engine": request.iac_engine.value,
            "cloud": request.cloud.model_dump(mode="json"),
            "run_init": request.run_init,
            "runtime_mode": request.runtime_mode.value,
            "running_instance": request.running_instance.model_dump(mode="json"),
            "artifact_mode": request.artifact_mode.value,
            "kubernetes_packaging": request.kubernetes_packaging.value,
            "kubernetes_options": request.kubernetes_options.model_dump(mode="json"),
            "cost_optimization": request.cost_optimization.model_dump(mode="json"),
            "container_scaffold": request.container_scaffold.model_dump(mode="json"),
            "dependencies": request.dependencies.model_dump(mode="json"),
            "ansible": request.ansible.model_dump(mode="json"),
            "config_tool": request.config_tool.value,
            "env_vars": [e.model_dump(mode="json") for e in request.env_vars],
        }
        if request.cloud_plugin is not None:
            payload["cloud_plugin"] = request.cloud_plugin.model_dump(mode="json")
        if preserve:
            for key in (
                "git_repo_url",
                "git_branch",
                "linked_app_repo",
                "linked_repos",
                "source",
                "import_id",
                "detection",
                "preview_service",
                "commit_sha",
                # Multi-repo + service-graph state must survive a plain workspace
                # update (which carries no repo/graph fields), or the Services graph
                # loses its nodes/edges.
                "repos",
                "service_comms",
                "service_connections",
                "service_graph",
                "service_graph_mermaid",
                "cloud_plugin",
            ):
                if key in preserve and key not in payload:
                    payload[key] = preserve[key]
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
                    from app.services.runtime_mode import coerce_wizard_snapshot

                    return coerce_wizard_snapshot(raw)
        root = Path(row.root_dir)
        if root.is_dir():
            snapshot = self._iac.read_wizard_snapshot(root)
            if snapshot is not None:
                from app.services.runtime_mode import coerce_wizard_snapshot

                return coerce_wizard_snapshot(snapshot)
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
            runtime_mode=config.runtime_mode,
            running_instance=config.running_instance,
            artifact_mode=config.artifact_mode,
            kubernetes_packaging=config.kubernetes_packaging,
            kubernetes_options=config.kubernetes_options,
            cost_optimization=config.cost_optimization,
            container_scaffold=config.container_scaffold,
            dependencies=config.dependencies,
            ansible=config.ansible,
            config_tool=config.config_tool,
            env_vars=config.env_vars,
            cloud_plugin=config.cloud_plugin,
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
                artifact_mode=self.get_workspace_artifact_mode(row),
                runtime_mode=self.get_workspace_runtime_mode(row),
                name=row.name,
                status=row.status,
                created_at=row.created_at,
                starred=row.starred_at is not None,
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
        if (
            isinstance(request.cloud, LocalCloudConfig)
            and request.runtime_mode == WorkspaceRuntimeMode.KUBERNETES
        ):
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
            runtime_mode=request.runtime_mode,
            name=row.name,
            status=row.status,
            created_at=row.created_at,
            starred=row.starred_at is not None,
        )

    def get_workspace_runtime_mode(
        self,
        workspace: ProvisioningWorkspace,
    ) -> WorkspaceRuntimeMode:
        snapshot = self._load_wizard_snapshot(workspace)
        if snapshot and snapshot.get("runtime_mode"):
            try:
                return WorkspaceRuntimeMode(str(snapshot["runtime_mode"]))
            except ValueError:
                logger.warning(
                    "workspace_runtime_mode_invalid",
                    workspace_id=str(workspace.id),
                    runtime_mode=snapshot.get("runtime_mode"),
                )
        return WorkspaceRuntimeMode.KUBERNETES

    def get_workspace_kubernetes_packaging(
        self,
        workspace: ProvisioningWorkspace,
    ) -> KubernetesPackaging | None:
        snapshot = self._load_wizard_snapshot(workspace)
        if snapshot is not None and "kubernetes_packaging" in snapshot:
            raw = str(snapshot.get("kubernetes_packaging") or "none")
            try:
                packaging = KubernetesPackaging(raw)
            except ValueError:
                logger.warning(
                    "workspace_packaging_invalid",
                    workspace_id=str(workspace.id),
                    packaging=raw,
                )
            else:
                if packaging == KubernetesPackaging.NONE:
                    return None
                return packaging

        if self.get_workspace_runtime_mode(workspace) != WorkspaceRuntimeMode.KUBERNETES:
            return None

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

        if self.get_workspace_runtime_mode(workspace) != WorkspaceRuntimeMode.KUBERNETES:
            return WorkspaceArtifactsMode.IAC_ONLY

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

    async def _gitlab_credentials(self, owner: User) -> tuple[str, str, str]:
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
            token = await auth.ensure_fresh_token(row)
            return row.base_url, token, row.token_type or "pat"
        except GitLabAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GitLabAuthError("Stored GitLab token could not be decrypted") from exc

    async def list_gitlab_projects(self, *, owner: User, search: str | None = None):
        from app.schemas.cloud import GitlabProjectItem
        from app.services.gitlab_service import (
            GitLabAuthError,
            GitLabProvisioningService,
            http_error_from_gitlab,
        )

        base_url, token, token_type = await self._gitlab_credentials(owner)
        try:
            rows = await asyncio.to_thread(
                GitLabProvisioningService(self._iac).list_projects,
                base_url=base_url,
                token=token,
                search=search,
                token_type=token_type,
            )
        except GitLabAuthError as exc:
            raise http_error_from_gitlab(exc) from exc
        return [GitlabProjectItem.model_validate(item) for item in rows]

    async def list_gitlab_branches(
        self,
        *,
        owner: User,
        project_path: str,
    ):
        from app.schemas.cloud import GitBranchItem, GitBranchListResponse
        from app.services.gitlab_service import (
            GitLabAuthError,
            GitLabProvisioningService,
            http_error_from_gitlab,
        )

        base_url, token, token_type = await self._gitlab_credentials(owner)
        try:
            rows = await asyncio.to_thread(
                GitLabProvisioningService(self._iac).list_branches,
                base_url=base_url,
                token=token,
                project_path=project_path,
                token_type=token_type,
            )
        except GitLabAuthError as exc:
            raise http_error_from_gitlab(exc) from exc
        default_branch = next(
            (str(item["name"]) for item in rows if item.get("is_default")),
            None,
        )
        return GitBranchListResponse(
            branches=[GitBranchItem.model_validate(item) for item in rows],
            default_branch=default_branch,
        )

    async def create_gitlab_repo(self, request, *, owner: User):
        from app.schemas.cloud import GitlabRepoResult
        from app.services.gitlab_service import (
            GitLabAuthError,
            GitLabProvisioningService,
            http_error_from_gitlab,
        )

        base_url, token, token_type = await self._gitlab_credentials(owner)
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
                token_type=token_type,
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
        base_url, token, token_type = await self._gitlab_credentials(owner)
        try:
            result = await asyncio.to_thread(
                GitLabProvisioningService(self._iac).push_workspace_files,
                base_url=base_url,
                token=token,
                project_path=request.project_path,
                root_dir=workspace.root_dir,
                commit_message=request.commit_message,
                token_type=token_type,
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

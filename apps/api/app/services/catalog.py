"""Golden path service catalog - list templates and create services."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.domain import CatalogService, ProvisioningWorkspace, User
from app.schemas.catalog import (
    CatalogServiceCreate,
    CatalogServiceRead,
    CatalogServiceUpdate,
    GoldenPathTemplateRead,
    ServiceScorecard,
)
from app.schemas.cloud import (
    CloudCredentials,
    ContainerScaffoldConfig,
    DataStoreDependency,
    GitHubRepoRequest,
    IaCEngine,
    KubernetesPackaging,
    LocalCloudConfig,
    ProvisioningWizardRequest,
    WorkloadDependenciesConfig,
    WorkspaceArtifactsMode,
)
from app.services.golden_path_templates import (
    get_golden_path_template,
    list_golden_path_templates,
)
from app.services.iac_generator import IaCGenerator
from app.services.orgs import OrganizationService
from app.services.provisioning import ProvisioningService
from app.core.secrets import encrypt_secret
from app.services.service_scorecard import compute_workspace_scorecard

logger = get_logger(__name__)


class CatalogServiceManager:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._iac = IaCGenerator()

    def list_templates(self) -> list[GoldenPathTemplateRead]:
        return [
            GoldenPathTemplateRead(
                id=t.id,
                version=t.version,
                title=t.title,
                description=t.description,
                icon=t.icon,
                stack=t.stack,
                frameworks=list(t.frameworks),
                docker_images=list(t.docker_images),
                default_tier=t.default_tier,  # type: ignore[arg-type]
                default_slo=t.default_slo,
                listen_port=t.listen_port,
                tags=list(t.tags),
                includes_dockerfile=t.includes_dockerfile,
                includes_k8s=t.includes_k8s,
                includes_cicd=t.includes_cicd,
                includes_iac=t.includes_iac,
                enable_postgres=t.enable_postgres,
                enable_redis=t.enable_redis,
            )
            for t in list_golden_path_templates()
        ]

    async def list_services(
        self,
        *,
        owner: User,
        org_id: UUID | None,
    ) -> list[CatalogServiceRead]:
        stmt = select(CatalogService).where(CatalogService.owner_id == owner.id)
        if org_id is not None:
            stmt = select(CatalogService).where(
                (CatalogService.org_id == org_id) | (CatalogService.owner_id == owner.id)
            )
        stmt = stmt.order_by(CatalogService.created_at.desc())
        result = await self._session.execute(stmt)
        return [self._to_read(row) for row in result.scalars().all()]

    async def get_service(
        self,
        service_id: UUID,
        *,
        owner: User,
        org_id: UUID | None,
    ) -> CatalogServiceRead:
        row = await self._get_row(service_id)
        if row is None or not self._can_view(row, owner=owner, org_id=org_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "service_not_found", "message": "Catalog service not found"},
            )
        return self._to_read(row)

    async def create_service(
        self,
        payload: CatalogServiceCreate,
        *,
        owner: User,
        org_id: UUID | None,
    ) -> CatalogServiceRead:
        try:
            template = get_golden_path_template(payload.template_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "template_not_found", "message": str(exc)},
            ) from exc

        existing = await self._session.execute(
            select(CatalogService).where(
                CatalogService.owner_id == owner.id,
                CatalogService.name == payload.name,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "service_exists",
                    "message": f"Service '{payload.name}' already exists",
                },
            )

        artifact_mode = (
            WorkspaceArtifactsMode.BOTH
            if template.includes_iac
            else WorkspaceArtifactsMode.MANIFEST_ONLY
        )
        wizard = ProvisioningWizardRequest(
            name=payload.name,
            iac_engine=IaCEngine.TERRAFORM,
            cloud=LocalCloudConfig(),
            credentials=CloudCredentials(),
            run_init=False,
            artifact_mode=artifact_mode,
            kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
            container_scaffold=ContainerScaffoldConfig(
                enabled=template.includes_dockerfile,
                generate_dockerfile=True,
                generate_docker_compose=True,
                stack=template.stack,
                frameworks=list(template.frameworks),
                app_name=payload.name,
                listen_port=template.listen_port,
            ),
            dependencies=WorkloadDependenciesConfig(
                postgres=DataStoreDependency(enabled=template.enable_postgres),
                redis=DataStoreDependency(enabled=template.enable_redis),
            ),
        )

        # Scaffold workspace files without requiring a live kind cluster.
        bundle = self._iac.generate(wizard)
        personal = await OrganizationService(self._session).ensure_personal_org(owner)
        resolved_org = org_id or personal.id
        encrypted = encrypt_secret(wizard.credentials.model_dump_json())
        workspace = ProvisioningWorkspace(
            id=UUID(bundle.workspace_id),
            owner_id=owner.id,
            org_id=resolved_org,
            name=payload.name,
            engine=bundle.engine.value,
            provider=bundle.provider.value,
            root_dir=bundle.root_dir,
            status="ready",
            encrypted_credentials=encrypted,
            starred_at=datetime.now(UTC),
        )
        self._session.add(workspace)
        await self._session.flush()

        self._write_service_descriptor(
            root_dir=bundle.root_dir,
            payload=payload,
            template_id=template.id,
            template_version=template.version,
        )

        if template.includes_cicd:
            self._ensure_cicd_scaffold(
                bundle.root_dir,
                payload.name,
                vcs_provider=payload.vcs_provider,
            )

        scorecard = compute_workspace_scorecard(bundle.root_dir)
        if payload.enforce_scorecard_gate and not scorecard.passed:
            failed_items = [item.title for item in scorecard.items if not item.passed]
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "scorecard_gate_failed",
                    "message": (
                        f"Service compliance score ({scorecard.score}/100) failed hard gate requirement "
                        f"(min {scorecard.gate}). Failed checks: {', '.join(failed_items)}"
                    ),
                    "scorecard": scorecard.model_dump(),
                },
            )

        repository_url: str | None = None
        if payload.vcs_provider == "github" and payload.create_github_repo:
            if payload.github_installation_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "github_installation_required",
                        "message": "github_installation_id is required when create_github_repo is true",
                    },
                )
            provisioning = ProvisioningService(self._session)
            gh_result = await provisioning.create_github_repo(
                GitHubRepoRequest(
                    name=payload.name,
                    description=payload.description or f"Golden path service {payload.name}",
                    private=payload.github_private,
                    installation_id=payload.github_installation_id,
                    organization=payload.github_organization,
                    workspace_id=str(workspace.id),
                    set_cloud_secrets=False,
                    include_workflow=True,
                    include_dockerfiles=True,
                ),
                owner=owner,
            )
            repository_url = gh_result.html_url
        elif payload.vcs_provider == "gitlab":
            from app.services.gitlab_service import (
                GitLabAuthError,
                GitLabAuthService,
                GitLabProvisioningService,
            )

            auth_svc = GitLabAuthService(self._session)
            conn = await auth_svc.get_connection(owner.id)
            if conn is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "gitlab_connection_required",
                        "message": "GitLab connection required. Please connect GitLab account first.",
                    },
                )
            token = auth_svc.decrypt_token(conn)
            gl_prov = GitLabProvisioningService()
            try:
                gl_result = gl_prov.create_or_open_project(
                    base_url=conn.base_url,
                    token=token,
                    name=payload.gitlab_project_name or payload.name,
                    description=payload.description or f"Golden path service {payload.name}",
                    private=payload.gitlab_private,
                    root_dir=bundle.root_dir,
                    include_ci=True,
                )
                repository_url = gl_result.get("web_url")
            except GitLabAuthError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "gitlab_provision_failed", "message": str(exc)},
                ) from exc

        row = CatalogService(
            owner_id=owner.id,
            org_id=resolved_org,
            workspace_id=workspace.id,
            name=payload.name,
            description=payload.description,
            service_owner=payload.owner or owner.email,
            tier=payload.tier,
            slo_target=payload.slo_target or template.default_slo,
            runbook_url=payload.runbook_url,
            on_call=payload.on_call,
            template_id=template.id,
            template_version=template.version,
            repository_url=repository_url,
            compliance_score=scorecard.score,
            scorecard_json=scorecard.model_dump_json(),
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)

        initial_preview_id: UUID | None = None
        initial_preview_url: str | None = None
        if payload.trigger_initial_preview:
            try:
                from app.services.environment import EnvironmentService
                from app.schemas.environment import PreviewLaunchRequest, PreviewProvider

                env_svc = EnvironmentService(self._session)
                launch_req = PreviewLaunchRequest(
                    name=f"preview-{payload.name}"[:64],
                    workspace_id=workspace.id,
                    provider=PreviewProvider.LOCAL,
                    ttl_hours=8,
                )
                env_read = await env_svc.launch_preview(
                    launch_req,
                    owner=owner,
                    correlation_id=f"catalog-preview-{row.id}",
                    org_id=resolved_org,
                )
                initial_preview_id = env_read.id
                initial_preview_url = env_read.preview_url
            except Exception as exc:
                logger.warning(
                    "catalog_initial_preview_launch_failed",
                    service_id=str(row.id),
                    error=str(exc),
                )

        logger.info(
            "catalog_service_created",
            service_id=str(row.id),
            template_id=template.id,
            workspace_id=str(workspace.id),
            score=scorecard.score,
        )
        read_obj = self._to_read(row)
        read_obj.initial_preview_id = initial_preview_id
        read_obj.initial_preview_url = initial_preview_url
        return read_obj

    async def update_service(
        self,
        service_id: UUID,
        payload: CatalogServiceUpdate,
        *,
        owner: User,
        org_id: UUID | None,
    ) -> CatalogServiceRead:
        row = await self._get_row(service_id)
        if row is None or not self._can_view(row, owner=owner, org_id=org_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "service_not_found", "message": "Catalog service not found"},
            )
        if row.owner_id != owner.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Only the service creator can update it"},
            )

        data = payload.model_dump(exclude_unset=True)
        if "description" in data and data["description"] is not None:
            row.description = data["description"]
        if "owner" in data and data["owner"] is not None:
            row.service_owner = data["owner"]
        if "tier" in data and data["tier"] is not None:
            row.tier = data["tier"]
        if "slo_target" in data and data["slo_target"] is not None:
            row.slo_target = data["slo_target"]
        if "runbook_url" in data:
            row.runbook_url = data["runbook_url"]
        if "on_call" in data:
            row.on_call = data["on_call"]

        await self._session.commit()
        await self._session.refresh(row)
        logger.info("catalog_service_updated", service_id=str(row.id))
        return self._to_read(row)

    async def delete_service(
        self,
        service_id: UUID,
        *,
        owner: User,
        org_id: UUID | None,
    ) -> None:
        row = await self._get_row(service_id)
        if row is None or not self._can_view(row, owner=owner, org_id=org_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "service_not_found", "message": "Catalog service not found"},
            )
        if row.owner_id != owner.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "Only the service creator can delete it"},
            )
        await self._session.delete(row)
        await self._session.commit()
        logger.info("catalog_service_deleted", service_id=str(service_id))

    async def delete_services_for_workspace(self, workspace_id: UUID) -> int:
        """Remove catalog entries linked to a workspace (used on workspace destroy)."""
        result = await self._session.execute(
            select(CatalogService).where(CatalogService.workspace_id == workspace_id)
        )
        rows = list(result.scalars().all())
        for row in rows:
            await self._session.delete(row)
        if rows:
            await self._session.flush()
            logger.info(
                "catalog_services_deleted_for_workspace",
                workspace_id=str(workspace_id),
                count=len(rows),
            )
        return len(rows)

    def _write_service_descriptor(
        self,
        *,
        root_dir: str,
        payload: CatalogServiceCreate,
        template_id: str,
        template_version: str,
    ) -> None:
        path = Path(root_dir) / "service.yaml"
        content = (
            "apiVersion: launchpad.io/v1\n"
            "kind: ServiceDescriptor\n"
            "metadata:\n"
            f"  name: {payload.name}\n"
            f"  description: \"{(payload.description or '').replace(chr(34), '')}\"\n"
            "  labels:\n"
            f"    launchpad.io/template: {template_id}\n"
            f"    launchpad.io/template-version: \"{template_version}\"\n"
            "spec:\n"
            f"  owner: {payload.owner}\n"
            f"  tier: {payload.tier}\n"
            f"  slo: \"{payload.slo_target}\"\n"
        )
        if payload.runbook_url:
            content += f"  runbook: {payload.runbook_url}\n"
        if payload.on_call:
            content += f"  onCall: {payload.on_call}\n"
        content += (
            "  health:\n"
            "    path: /healthz\n"
            "    port: 8080\n"
            "infrastructure:\n"
            "  components:\n"
            f"    - name: {payload.name}\n"
            "      type: api\n"
            f"      dockerfile: dockers/Dockerfile.{payload.name}\n"
        )
        path.write_text(content, encoding="utf-8")

    def _ensure_cicd_scaffold(
        self,
        root_dir: str,
        app_name: str,
        *,
        vcs_provider: str = "github",
    ) -> None:
        """Write a minimal CI workflow if the IaC generator did not emit one."""
        root = Path(root_dir)
        if vcs_provider == "gitlab":
            gitlab_ci = root / ".gitlab-ci.yml"
            if gitlab_ci.is_file() or list(root.glob("ci/gitlab/**/*.yml")):
                return
            # Stronger golden-path CI: SAST + build + Trivy (matches scorecard).
            content = (
                f"# Golden path GitLab CI for {app_name}\n"
                "stages:\n"
                "  - test\n"
                "  - build\n"
                "  - scan\n"
                "sast:\n"
                "  stage: test\n"
                "  image: returntocorp/semgrep:1.97.0\n"
                "  script:\n"
                "    - semgrep scan --config p/ci --error .\n"
                "build:\n"
                "  stage: build\n"
                "  image: docker:27\n"
                "  services: [docker:27-dind]\n"
                "  script:\n"
                "    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA .\n"
                "container-security-scan:\n"
                "  stage: scan\n"
                "  image: aquasec/trivy:0.58.1\n"
                "  script:\n"
                "    - trivy image --exit-code 0 $CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA\n"
                "  allow_failure: true\n"
            )
            gitlab_ci.write_text(content, encoding="utf-8")
            return

        existing = list(root.glob("ci/**/*.yml")) + list(root.glob(".github/workflows/*.yml"))
        if existing or (root / ".gitlab-ci.yml").is_file():
            return
        workflow_dir = root / "ci" / "github" / "workflows"
        workflow_dir.mkdir(parents=True, exist_ok=True)
        workflow = (
            f"# Golden path CI for {app_name}\n"
            "name: Build, Scan & Deploy\n"
            "on:\n"
            "  push:\n"
            "    branches: [\"main\"]\n"
            "jobs:\n"
            "  sast-code-scan:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Semgrep\n"
            "        run: docker run --rm -v \"$PWD:/src\" -w /src returntocorp/semgrep:1.97.0 "
            "semgrep scan --config p/ci --error .\n"
            "  build-image:\n"
            "    needs: sast-code-scan\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Build\n"
            "        run: docker build -t app .\n"
            "  container-security-scan:\n"
            "    needs: build-image\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Trivy\n"
            "        run: docker run --rm aquasec/trivy:0.58.1 image --exit-code 0 app\n"
        )
        (workflow_dir / "deploy.yml").write_text(workflow, encoding="utf-8")

    async def _get_row(self, service_id: UUID) -> CatalogService | None:
        result = await self._session.execute(
            select(CatalogService).where(CatalogService.id == service_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _can_view(row: CatalogService, *, owner: User, org_id: UUID | None) -> bool:
        if row.owner_id == owner.id:
            return True
        if org_id is not None and row.org_id == org_id:
            return True
        return False

    def _to_read(self, row: CatalogService) -> CatalogServiceRead:
        try:
            scorecard = ServiceScorecard.model_validate_json(row.scorecard_json)
        except Exception:  # noqa: BLE001
            scorecard = ServiceScorecard(score=row.compliance_score, gate=70, passed=row.compliance_score >= 70, items=[])
        return CatalogServiceRead(
            id=row.id,
            name=row.name,
            description=row.description,
            owner=row.service_owner,
            tier=row.tier,  # type: ignore[arg-type]
            slo_target=row.slo_target,
            runbook_url=row.runbook_url,
            on_call=row.on_call,
            template_id=row.template_id,
            template_version=row.template_version,
            repository_url=row.repository_url,
            workspace_id=row.workspace_id,
            compliance_score=row.compliance_score,
            scorecard=scorecard,
            org_id=row.org_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

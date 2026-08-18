"""Orchestrate clone → detect → generate → persist Launchpad workspace."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.secrets import encrypt_secret
from app.models.domain import ProvisioningWorkspace, User
from app.schemas.cloud import CloudCredentials, CloudProvider, IaCEngine, WorkspaceRuntimeMode
from app.schemas.repo_import import (
    RepoImportCreateRequest,
    RepoImportSaveRequest,
    RepoImportSaveResult,
    RepoImportSessionRead,
    RepoRef,
    ServiceOverride,
)
from app.services.git_importer import GitImporterError, GitImporterService
from app.services.multi_repo_import import RepoDetection, assemble_multi_repo
from pkg.detector import ProjectDetectorEngine
from pkg.detector.models import DetectedService, DetectionResult
from pkg.generator.workspace import WorkspaceGenerator

logger = get_logger(__name__)

_DETECTION_FILE = ".launchpad/detection.json"
_MULTI_REPO_FILE = ".launchpad/multi_repo.json"
# Kept at the workspace container root by GitImporterService.clone; must survive the
# multi-repo relocation of the primary clone into apps/<name>/ (see git_importer).
_IMPORT_META_FILE = ".launchpad-import.json"
# Multi-repo layout: every imported repo lives under this dir so generated infra at
# the container root never nests inside a source repo.
_APPS_DIR = "apps"


def _repo_slug_from_url(url: str) -> str:
    import re

    tail = url.rstrip("/").rsplit("/", 1)[-1]
    tail = tail[:-4] if tail.endswith(".git") else tail
    return re.sub(r"[^a-z0-9-]", "-", tail.lower()).strip("-")[:63] or "repo"


class RepoImportService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._importer = GitImporterService(self._settings)
        self._detector = ProjectDetectorEngine()
        self._generator = WorkspaceGenerator()

    async def start_import(
        self,
        request: RepoImportCreateRequest,
        *,
        owner: User,
    ) -> RepoImportSessionRead:
        refs = request.effective_repos()
        is_multi = len(refs) > 1
        used_names: set[str] = set()
        detections: list[RepoDetection] = []
        datastore_kinds: set[str] = set()
        repo_urls: list[str] = []
        primary = None
        primary_detection: DetectionResult | None = None

        try:
            for index, ref in enumerate(refs):
                token = self._resolve_token(
                    request.use_github_app_token,
                    installation_id=ref.github_installation_id or request.github_installation_id,
                )
                cloned = self._importer.clone(
                    repo_url=ref.git_repo_url, branch=ref.git_branch, token=token
                )
                repo_urls.append(cloned.repo_url)
                name = self._unique_repo_name(ref, cloned.repo_url, used_names)

                if index == 0:
                    primary = cloned
                    if is_multi:
                        # Multi-repo: relocate the primary clone into apps/<name>/ so the
                        # container root holds only generated infra + .launchpad metadata,
                        # never a repo's source tree.
                        root = cloned.root_dir / _APPS_DIR / name
                        self._relocate_into(cloned.root_dir, root)
                        mount = f"{_APPS_DIR}/{name}"
                    else:
                        # Single-repo import: repo stays at the workspace root (unchanged).
                        root = cloned.root_dir
                        mount = ""
                else:
                    # Secondary repos live under apps/<name>/ alongside the primary.
                    root = primary.root_dir / _APPS_DIR / name
                    root.parent.mkdir(parents=True, exist_ok=True)
                    if root.exists():
                        shutil.rmtree(root, ignore_errors=True)
                    shutil.copytree(
                        cloned.root_dir, root, symlinks=False, ignore_dangling_symlinks=True
                    )
                    self._importer.cleanup(cloned.import_id)
                    mount = f"{_APPS_DIR}/{name}"

                det = self._detector.detect(root)
                datastore_kinds.update(det.datastores)
                if index == 0:
                    primary_detection = det
                # Name services after the repo (not the detector's launch-web/launch-server
                # convention). A single-service repo becomes just <repo>; monorepos become
                # <repo>-<service>.
                repo_services = self._apply_repo_naming(name, det.services)
                detections.append(
                    RepoDetection(
                        name=name, root_dir=root, services=repo_services, mount_prefix=mount
                    )
                )
        except GitImporterError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "repo_import_clone_failed", "message": str(exc)},
            ) from exc

        assert primary is not None and primary_detection is not None
        assembly = assemble_multi_repo(detections)
        merged_detection = primary_detection.model_copy(
            update={"services": assembly.services, "datastores": sorted(datastore_kinds)}
        )
        # Cache the MERGED detection so save re-uses it (never re-detects the tree,
        # which now contains the secondary repos under repos/).
        self._write_detection(primary.root_dir, merged_detection)

        multi_repo = len(refs) > 1
        if multi_repo:
            self._write_multi_repo(primary.root_dir, repo_urls, assembly)

        meta = self._importer.read_meta(primary.import_id)
        created_raw = meta.get("created_at")
        created_at = None
        if isinstance(created_raw, str):
            try:
                created_at = datetime.fromisoformat(created_raw)
            except ValueError:
                created_at = datetime.now(UTC)

        logger.info(
            "repo_import_detected",
            import_id=primary.import_id,
            repos=len(refs),
            layout=merged_detection.layout.value,
            services=len(assembly.services),
            edges=len(assembly.graph.edges),
            owner_id=str(owner.id),
        )
        return RepoImportSessionRead(
            import_id=primary.import_id,
            git_repo_url=primary.repo_url,
            git_branch=primary.branch,
            commit_sha=primary.commit_sha,
            layout=merged_detection.layout,
            detection=merged_detection,
            services=assembly.services,
            created_at=created_at,
            datastore_suggestions=self._datastore_suggestions(merged_detection.datastores),
            repos=repo_urls,
            service_graph=assembly.graph.model_dump(),
            mermaid=assembly.mermaid,
        )

    async def get_import(self, import_id: str, *, owner: User) -> RepoImportSessionRead:
        del owner  # ownership is path-isolation for now; import ids are unguessable UUIDs
        try:
            root = self._importer.get_root(import_id)
            meta = self._importer.read_meta(import_id)
        except GitImporterError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "repo_import_not_found", "message": str(exc)},
            ) from exc

        detection = self._read_detection(root)
        if detection is None:
            detection = self._detector.detect(root)
            self._write_detection(root, detection)

        created_at = None
        created_raw = meta.get("created_at")
        if isinstance(created_raw, str):
            try:
                created_at = datetime.fromisoformat(created_raw)
            except ValueError:
                pass

        multi_repo = self._read_multi_repo(root) or {}
        return RepoImportSessionRead(
            import_id=import_id,
            git_repo_url=str(meta.get("repo_url") or ""),
            git_branch=str(meta.get("branch") or "main"),
            commit_sha=str(meta.get("commit_sha") or ""),
            layout=detection.layout,
            detection=detection,
            services=detection.services,
            created_at=created_at,
            datastore_suggestions=self._datastore_suggestions(detection.datastores),
            repos=multi_repo.get("repos", []),
            service_graph=multi_repo.get("service_graph"),
            mermaid=multi_repo.get("mermaid"),
        )

    async def save_as_workspace(
        self,
        import_id: str,
        request: RepoImportSaveRequest,
        *,
        owner: User,
        org_id: UUID | None = None,
    ) -> RepoImportSaveResult:
        try:
            import_root = self._importer.get_root(import_id)
            meta = self._importer.read_meta(import_id)
        except GitImporterError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "repo_import_not_found", "message": str(exc)},
            ) from exc

        detection = self._read_detection(import_root) or self._detector.detect(import_root)
        services = self._apply_overrides(detection.services, request.services)
        if not any(s.enabled for s in services):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "no_services_enabled",
                    "message": "Enable at least one detected service before saving",
                },
            )

        workspace_id = uuid.uuid4()
        durable = self._allocate_durable_dir(request.name)
        # Move clone into durable workspace root (keeps source tree + generated files).
        if durable.exists():
            shutil.rmtree(durable, ignore_errors=True)
        shutil.copytree(import_root, durable, symlinks=False, ignore_dangling_symlinks=True)

        adjusted = detection.model_copy(update={"services": services})
        runtime_mode = WorkspaceRuntimeMode(request.runtime_mode)

        # Derive message-broker + inter-service connection wiring from the detected
        # communication graph (multi-repo import). Empty for single plain repos.
        multi_repo_saved = self._read_multi_repo(import_root) or {}
        broker_kinds, connection_env = self._connection_wiring(multi_repo_saved, services)

        deps = self._build_dependencies(
            detection.datastores,
            request.datastores,
            workspace_name=request.name,
            broker_kinds=broker_kinds,
        )
        env_map = self._build_env_map(
            detection,
            request.env_vars,
            deps,
            workspace_name=request.name,
            connection_env=connection_env,
        )
        # External datastore URLs without a matching .env.example key still land in .env
        generated = self._generator.generate(
            durable,
            adjusted,
            workspace_name=request.name,
            services=services,
            runtime_mode=runtime_mode.value,
            dependencies=deps,
            env_vars=env_map,
        )

        if request.enable_iac and runtime_mode != WorkspaceRuntimeMode.KUBERNETES:
            from app.services.local_runtime_iac import write_local_runtime_iac

            iac_files = write_local_runtime_iac(
                durable,
                name=request.name,
                engine=IaCEngine(request.iac_engine),
                runtime_mode=runtime_mode,
            )
            generated.files.extend(iac_files)

        if request.enable_cicd:
            cicd_files = self._write_cicd_stub(
                durable,
                platform=request.cicd_platform,
                name=request.name,
            )
            generated.files.extend(cicd_files)

        running_instance_cfg = None
        if runtime_mode == WorkspaceRuntimeMode.RUNNING_INSTANCE:
            from app.schemas.cloud import (
                InstanceProcessStrategy,
                InstanceReverseProxy,
                RunningInstanceConfig,
                RunningInstanceKind,
            )
            from app.services.instance_process_scaffold import write_instance_process_scaffold

            running_instance_cfg = RunningInstanceConfig(
                kind=RunningInstanceKind.LOCAL_MACHINE,
                listen_port=8088,
                process_strategy=InstanceProcessStrategy(request.process_strategy),
                reverse_proxy=InstanceReverseProxy(request.reverse_proxy),
            )
            generated.files.extend(
                write_instance_process_scaffold(
                    durable,
                    name=request.name,
                    running_instance=running_instance_cfg,
                )
            )

        cluster_ready = False
        wants_cluster = (
            request.ensure_local_cluster
            and runtime_mode == WorkspaceRuntimeMode.KUBERNETES
        )
        if wants_cluster:
            try:
                from app.services.kind_cluster import ensure_kind_cluster

                await ensure_kind_cluster()
                cluster_ready = True
            except Exception as exc:
                logger.warning(
                    "repo_import_cluster_ensure_failed",
                    import_id=import_id,
                    error=str(exc),
                )

        from app.models.domain import Organization
        from app.services.orgs import OrganizationService
        from app.services.plans import assert_can_create_workspace
        from app.services.projects import ProjectService

        orgs = OrganizationService(self._session)
        personal = await orgs.ensure_personal_org(owner)
        resolved_org_id = org_id or personal.id
        org = await self._session.get(Organization, resolved_org_id)
        if org is not None:
            await assert_can_create_workspace(self._session, org)
        org_ctx = await orgs.resolve_context(user=owner, org_id=resolved_org_id)
        project = await ProjectService(self._session).resolve_project_for_workspace(
            org=org_ctx,
            project_id=request.project_id,
        )
        artifact_mode = (
            "manifest_only"
            if runtime_mode == WorkspaceRuntimeMode.KUBERNETES
            else ("iac_only" if request.enable_iac else "iac_only")
        )
        packaging = (
            "raw_manifests"
            if runtime_mode == WorkspaceRuntimeMode.KUBERNETES
            else "none"
        )
        wizard_snapshot = {
            "source": "repo_import",
            "git_repo_url": meta.get("repo_url"),
            "git_branch": meta.get("branch"),
            "commit_sha": meta.get("commit_sha"),
            "import_id": import_id,
            "detection": adjusted.model_dump(),
            "preview_service": generated.preview_service,
            "name": request.name,
            "iac_engine": request.iac_engine,
            "provider": CloudProvider.LOCAL.value,
            "cloud": {
                "provider": CloudProvider.LOCAL.value,
                "resources": {"cluster_name": "launchpad", "context": "k3d-launchpad"},
            },
            "runtime_mode": runtime_mode.value,
            "running_instance": (
                running_instance_cfg.model_dump()
                if running_instance_cfg is not None
                else {
                    "kind": "local_machine",
                    "listen_port": 8088,
                    "process_strategy": "docker",
                    "reverse_proxy": "none",
                }
            ),
            "artifact_mode": artifact_mode,
            "kubernetes_packaging": packaging,
            "run_init": False,
            "dependencies": deps.model_dump(),
            "env_vars_configured": sorted(env_map.keys()),
        }
        # Multi-repo: persist the repo list + inter-service connection graph so the
        # workspace can render it and later wire the connections.
        multi_repo = self._read_multi_repo(import_root)
        if multi_repo:
            wizard_snapshot["repos"] = multi_repo.get("repos", [])
            wizard_snapshot["service_graph"] = multi_repo.get("service_graph")
            wizard_snapshot["service_graph_mermaid"] = multi_repo.get("mermaid")
            wizard_snapshot["service_comms"] = multi_repo.get("service_comms", [])
            wizard_snapshot["service_connections"] = multi_repo.get("service_connections", [])
        row = ProvisioningWorkspace(
            id=workspace_id,
            owner_id=owner.id,
            org_id=resolved_org_id,
            project_id=project.id,
            name=request.name,
            engine=request.iac_engine,
            provider=CloudProvider.LOCAL.value,
            root_dir=str(durable),
            status="ready",
            encrypted_credentials=encrypt_secret(CloudCredentials().model_dump_json()),
            wizard_config_json=json.dumps(wizard_snapshot),
        )
        self._session.add(row)
        await self._session.commit()

        # Temp import dir can go away after durable copy.
        self._importer.cleanup(import_id)

        logger.info(
            "repo_import_saved_workspace",
            import_id=import_id,
            workspace_id=str(workspace_id),
            name=request.name,
            files=len(generated.files),
        )
        if runtime_mode == WorkspaceRuntimeMode.KUBERNETES:
            message = (
                "Workspace saved. Open Launch and select this workspace to deploy the preview."
                if cluster_ready
                else (
                    "Workspace saved. Local cluster was not ready; "
                    "Launch will start it on deploy."
                )
            )
        elif runtime_mode == WorkspaceRuntimeMode.DOCKER_COMPOSE:
            message = (
                "Workspace saved as Docker Compose. Open Launch and select this workspace "
                "to run docker compose."
            )
        else:
            strategy = request.process_strategy
            proxy = request.reverse_proxy
            message = (
                "Workspace saved as running instance "
                f"(process={strategy}, proxy={proxy}). "
                "Docker strategy deploys via Launch attach; "
                "PM2/systemd/nginx scaffolds are under infra/instance/."
            )
        return RepoImportSaveResult(
            workspace_id=workspace_id,
            name=request.name,
            root_dir=str(durable),
            files=generated.files,
            preview_service=generated.preview_service,
            cluster_ready=cluster_ready,
            message=message,
        )

    @staticmethod
    def _write_cicd_stub(workspace_dir: Path, *, platform: str, name: str) -> list[str]:
        """Minimal GitHub Actions / GitLab CI stub for imported workspaces."""
        written: list[str] = []
        if platform == "gitlab":
            path = workspace_dir / ".gitlab-ci.yml"
            path.write_text(
                f"# Launchpad CI stub for {name}\n"
                "stages:\n"
                "  - build\n"
                "  - test\n\n"
                "build:\n"
                "  stage: build\n"
                "  image: docker:27\n"
                "  services:\n"
                "    - docker:27-dind\n"
                "  script:\n"
                "    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA .\n",
                encoding="utf-8",
            )
            written.append(".gitlab-ci.yml")
            return written

        path = workspace_dir / ".github" / "workflows" / "launchpad-ci.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Launchpad CI stub for {name}\n"
            "name: launchpad-ci\n"
            "on:\n"
            "  push:\n"
            "    branches: [main, master]\n"
            "  pull_request:\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Build container\n"
            "        run: docker build -t launchpad/${{ github.repository }}:${{ github.sha }} .\n",
            encoding="utf-8",
        )
        written.append(".github/workflows/launchpad-ci.yml")
        return written

    async def discard(self, import_id: str, *, owner: User) -> None:
        del owner
        self._importer.cleanup(import_id)

    def needs_rehydrate(self, workspace: ProvisioningWorkspace) -> bool:
        """True when an imported workspace lost its source tree (e.g. /tmp wipe)."""
        snapshot = self._repo_import_snapshot(workspace)
        if snapshot is None:
            return False
        from app.services.manifest_deploy import (
            workspace_has_application_source,
            workspace_is_nginx_scaffold_only,
        )

        root = Path(workspace.root_dir)
        if not root.is_dir():
            return True
        if not workspace_has_application_source(root):
            return True
        return workspace_is_nginx_scaffold_only(
            root,
            default_image=self._settings.default_workload_image,
        )

    def rehydrate_workspace(self, workspace: ProvisioningWorkspace) -> Path:
        """Re-clone a repo_import workspace into the durable IaC root and regenerate manifests."""
        snapshot = self._repo_import_snapshot(workspace)
        if snapshot is None:
            raise ValueError("Workspace is not a repository import; cannot rehydrate")

        repo_url = str(snapshot.get("git_repo_url") or "").strip()
        branch = str(snapshot.get("git_branch") or "main").strip() or "main"
        if not repo_url:
            raise ValueError("Repository import snapshot is missing git_repo_url")

        detection_raw = snapshot.get("detection")
        if not isinstance(detection_raw, dict):
            raise ValueError("Repository import snapshot is missing detection metadata")
        detection = DetectionResult.model_validate(detection_raw)
        services = [s for s in detection.services if s.enabled]
        if not services:
            raise ValueError("Repository import snapshot has no enabled services")

        token = self._resolve_token(True)
        try:
            cloned = self._importer.clone(
                repo_url=repo_url,
                branch=branch,
                token=token,
            )
        except GitImporterError as exc:
            raise RuntimeError(f"Failed to re-clone imported repository: {exc}") from exc

        durable = self._allocate_durable_dir(workspace.name)
        if durable.exists():
            shutil.rmtree(durable, ignore_errors=True)
        try:
            shutil.copytree(
                cloned.root_dir,
                durable,
                symlinks=False,
                ignore_dangling_symlinks=True,
            )
        finally:
            self._importer.cleanup(cloned.import_id)

        generated = self._generator.generate(
            durable,
            detection,
            workspace_name=workspace.name,
            services=services,
            runtime_mode=str(snapshot.get("runtime_mode") or "kubernetes"),
        )
        self._write_detection(durable, detection)

        old_root = Path(workspace.root_dir)
        workspace.root_dir = str(durable)
        logger.info(
            "repo_import_workspace_rehydrated",
            workspace_id=str(workspace.id),
            from_dir=str(old_root),
            to_dir=str(durable),
            files=len(generated.files),
            preview_service=generated.preview_service,
        )
        return durable

    @staticmethod
    def _repo_import_snapshot(workspace: ProvisioningWorkspace) -> dict[str, object] | None:
        raw = workspace.wizard_config_json
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("source") != "repo_import":
            return None
        return payload

    @staticmethod
    def _datastore_suggestions(kinds: list[str]) -> dict[str, dict[str, str]]:
        from pkg.detector.env_example import suggested_datastore_urls

        out: dict[str, dict[str, str]] = {}
        for kind in kinds:
            urls = suggested_datastore_urls(kind)
            if urls.get("in_cluster") or urls.get("external"):
                out[kind] = urls
        return out

    @staticmethod
    def _connection_wiring(
        multi_repo: dict,
        services: list[DetectedService],
    ) -> tuple[set[str], dict[str, str]]:
        """From persisted comms/connections, derive broker kinds + connection env.

        Returns (broker_kinds, connection_env). Empty for single-repo imports that
        carry no communication graph.
        """
        raw_comms = multi_repo.get("service_comms") or []
        if not raw_comms:
            return set(), {}
        from app.services.comm_detector import CommKind, ServiceComms
        from app.services.service_connection_env import build_connection_env
        from app.services.service_graph import ExplicitConnection

        comms: list[ServiceComms] = []
        for item in raw_comms:
            try:
                comms.append(ServiceComms.model_validate(item))
            except Exception:  # noqa: BLE001 - tolerate malformed persisted data
                continue

        connections: list[ExplicitConnection] = []
        for item in multi_repo.get("service_connections") or []:
            try:
                connections.append(ExplicitConnection.model_validate(item))
            except Exception:  # noqa: BLE001
                continue

        broker_kinds: set[str] = set()
        for comm in comms:
            kinds = comm.kinds()
            if CommKind.KAFKA in kinds:
                broker_kinds.add("kafka")
            if CommKind.RABBITMQ in kinds:
                broker_kinds.add("rabbitmq")

        service_ports = {
            s.name: s.port for s in services if getattr(s, "port", None)
        }
        connection_env = build_connection_env(
            comms, connections, service_ports=service_ports
        )
        return broker_kinds, connection_env

    @staticmethod
    def _build_dependencies(
        detected: list[str],
        overrides: list[object],
        *,
        workspace_name: str,
        broker_kinds: set[str] | None = None,
    ):
        from app.schemas.cloud import (
            DataStoreDependency,
            DependencyPlacement,
            MessageBrokerDependency,
            WorkloadDependenciesConfig,
        )
        from app.schemas.repo_import import DatastoreImportConfig
        from pkg.detector.env_example import suggested_datastore_urls

        del workspace_name  # reserved for future per-app naming
        by_kind: dict[str, DatastoreImportConfig] = {}
        for raw in overrides:
            if isinstance(raw, DatastoreImportConfig):
                by_kind[raw.kind] = raw
            elif isinstance(raw, dict):
                cfg = DatastoreImportConfig.model_validate(raw)
                by_kind[cfg.kind] = cfg

        def _dep(kind: str) -> DataStoreDependency:
            cfg = by_kind.get(kind)
            if cfg is None:
                if kind not in detected:
                    return DataStoreDependency(enabled=False)
                # Default: in-cluster for kubernetes8s-friendly local preview
                return DataStoreDependency(
                    enabled=True,
                    placement=DependencyPlacement.IN_CLUSTER,
                )
            if cfg.placement == "skip":
                return DataStoreDependency(enabled=False)
            if cfg.placement == "external":
                url = (cfg.connection_url or "").strip()
                if not url:
                    url = suggested_datastore_urls(kind).get("external") or ""
                return DataStoreDependency(
                    enabled=True,
                    placement=DependencyPlacement.EXTERNAL,
                    connection_url=url or None,
                )
            return DataStoreDependency(
                enabled=True,
                placement=DependencyPlacement.IN_CLUSTER,
            )

        # Auto-provision (in-cluster) any message broker the detector saw services
        # using, unless the user explicitly overrode datastore placement to skip.
        brokers = broker_kinds or set()

        def _broker(kind: str) -> MessageBrokerDependency:
            cfg = by_kind.get(kind)
            if cfg is not None and cfg.placement == "skip":
                return MessageBrokerDependency(enabled=False)
            if cfg is not None and cfg.placement == "external":
                return MessageBrokerDependency(
                    enabled=True,
                    placement=DependencyPlacement.EXTERNAL,
                    connection_url=(cfg.connection_url or "").strip() or None,
                )
            if kind in brokers or cfg is not None:
                return MessageBrokerDependency(
                    enabled=True,
                    placement=DependencyPlacement.IN_CLUSTER,
                )
            return MessageBrokerDependency(enabled=False)

        return WorkloadDependenciesConfig(
            postgres=_dep("postgres"),
            mysql=_dep("mysql"),
            mariadb=_dep("mariadb"),
            mongodb=_dep("mongodb"),
            redis=_dep("redis"),
            kafka=_broker("kafka"),
            rabbitmq=_broker("rabbitmq"),
        )

    @staticmethod
    def _build_env_map(
        detection: DetectionResult,
        overrides: list[object],
        deps,
        *,
        workspace_name: str,
        connection_env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        from app.schemas.cloud import DependencyPlacement
        from app.schemas.repo_import import EnvVarOverride
        from app.services.workload_dependencies import dependency_secret_string_data
        from pkg.detector.env_example import suggested_datastore_urls

        env_map: dict[str, str] = {}
        for item in detection.env_example:
            if item.suggested_value:
                env_map[item.key] = item.suggested_value

        # Datastore placement URLs override .env.example placeholders.
        secret_bits = dependency_secret_string_data(deps, name=workspace_name)
        for key, value in secret_bits.items():
            if value:
                env_map[key] = value

        # Ensure external URL placeholders land even if secrets skipped empty
        for kind, attr in (
            ("postgres", "postgres"),
            ("mysql", "mysql"),
            ("mariadb", "mariadb"),
            ("mongodb", "mongodb"),
            ("redis", "redis"),
        ):
            dep = getattr(deps, attr)
            if not dep.enabled or dep.placement != DependencyPlacement.EXTERNAL:
                continue
            url = (dep.connection_url or "").strip() or suggested_datastore_urls(
                kind, app_name=workspace_name
            ).get("external", "")
            if not url:
                continue
            if kind == "redis":
                env_map["REDIS_URL"] = url
            elif kind == "mongodb":
                env_map["MONGODB_URI"] = url
            elif kind == "mysql":
                env_map["MYSQL_URL"] = url
                env_map["DATABASE_URL"] = url
            elif kind == "mariadb":
                env_map["MARIADB_URL"] = url
                env_map["DATABASE_URL"] = url
            else:
                env_map["DATABASE_URL"] = url

        # Graph-derived inter-service connection targets (gRPC/HTTP). Namespaced by
        # target service, so safe in the shared .env; placed before user overrides.
        for key, value in (connection_env or {}).items():
            if key and value:
                env_map.setdefault(key, value)

        # Explicit user overrides always win.
        for raw in overrides:
            if isinstance(raw, EnvVarOverride):
                key, value = raw.key, raw.value
            elif isinstance(raw, dict):
                key = str(raw.get("key") or "").strip()
                value = str(raw.get("value") or "")
            else:
                continue
            if not key:
                continue
            env_map[key] = value

        return env_map

    def _allocate_durable_dir(self, name: str) -> Path:
        root = Path(self._settings.iac_workspace_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        candidate = root / name
        if not candidate.exists():
            return candidate
        return root / f"{name}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _apply_overrides(
        services: list[DetectedService],
        overrides: list[ServiceOverride],
    ) -> list[DetectedService]:
        by_id = {o.id: o for o in overrides}
        out: list[DetectedService] = []
        preview_forced = any(o.is_preview_target for o in overrides)
        for svc in services:
            ov = by_id.get(svc.id)
            if ov is None:
                out.append(svc.model_copy(update={"is_preview_target": False}) if preview_forced else svc)
                continue
            updates: dict[str, object] = {
                "enabled": ov.enabled,
                "is_preview_target": ov.is_preview_target,
            }
            if ov.port is not None:
                updates["port"] = ov.port
            if ov.name:
                updates["name"] = ov.name
            out.append(svc.model_copy(update=updates))
        if preview_forced and not any(s.is_preview_target and s.enabled for s in out):
            for i, s in enumerate(out):
                if s.enabled:
                    out[i] = s.model_copy(update={"is_preview_target": True})
                    break
        elif not any(s.is_preview_target for s in out):
            for i, s in enumerate(out):
                if s.enabled:
                    out[i] = s.model_copy(update={"is_preview_target": True})
                    break
        return out

    @staticmethod
    def _write_detection(root: Path, detection: DetectionResult) -> None:
        path = root / _DETECTION_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(detection.model_dump_json(indent=2), encoding="utf-8")

    @staticmethod
    def _read_detection(root: Path) -> DetectionResult | None:
        path = root / _DETECTION_FILE
        if not path.is_file():
            return None
        try:
            return DetectionResult.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _unique_repo_name(ref: RepoRef, repo_url: str, used: set[str]) -> str:
        base = (ref.name or "").strip().lower() or _repo_slug_from_url(repo_url)
        base = re.sub(r"[^a-z0-9-]", "-", base).strip("-")[:63] or "repo"
        name = base
        n = 2
        while name in used:
            name = f"{base}-{n}"[:63]
            n += 1
        used.add(name)
        return name

    @staticmethod
    def _apply_repo_naming(
        repo_name: str, services: list[DetectedService]
    ) -> list[DetectedService]:
        """Rename detected services after the repo instead of launch-web/launch-server.

        - A repo with a single service is named exactly after the repo (``orders``).
        - A monorepo's services keep a repo prefix for uniqueness (``orders-web``),
          with the detector's ``launch-`` prefix stripped.

        Names stay K8s-safe (slugged) and unique within the repo. Service ``path``,
        ports, roles, and preview-target flags are preserved.
        """
        slug = re.sub(r"[^a-z0-9-]+", "-", (repo_name or "").lower()).strip("-") or "app"
        single = len(services) == 1
        out: list[DetectedService] = []
        used: set[str] = set()
        for svc in services:
            if single:
                name = slug
            else:
                leaf = re.sub(r"^launch-", "", svc.name)
                leaf = re.sub(r"[^a-z0-9-]+", "-", leaf.lower()).strip("-") or "svc"
                name = leaf if leaf.startswith(slug) else f"{slug}-{leaf}"
            name = name[:63]
            base = name
            n = 2
            while name in used:
                name = f"{base}-{n}"[:63]
                n += 1
            used.add(name)
            if name != svc.name:
                out.append(svc.model_copy(update={"name": name, "id": name}))
            else:
                out.append(svc)
        return out

    @staticmethod
    def _relocate_into(container: Path, dest: Path) -> None:
        """Move a clone's files from ``container`` into ``dest`` (e.g. apps/<name>).

        The import meta file and the apps dir itself stay at the container root so
        ``read_meta`` keeps working and generated infra lands beside apps/, not inside
        a source repo.
        """
        dest.mkdir(parents=True, exist_ok=True)
        keep = {_IMPORT_META_FILE, _APPS_DIR}
        for entry in list(container.iterdir()):
            if entry.name in keep:
                continue
            shutil.move(str(entry), str(dest / entry.name))

    @staticmethod
    def _write_multi_repo(root: Path, repos: list[str], assembly: object) -> None:
        path = root / _MULTI_REPO_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "repos": repos,
            "service_graph": assembly.graph.model_dump(),  # type: ignore[attr-defined]
            "mermaid": assembly.mermaid,  # type: ignore[attr-defined]
            # Persist per-service comms so operator connection edits can rebuild the
            # graph without re-cloning/re-detecting.
            "service_comms": [c.model_dump() for c in assembly.comms],  # type: ignore[attr-defined]
            "service_connections": [],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _read_multi_repo(root: Path) -> dict | None:
        path = root / _MULTI_REPO_FILE
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def _resolve_token(
        self,
        use_github_app: bool,
        *,
        installation_id: int | None = None,
    ) -> str | None:
        from app.services.github_app import resolve_git_clone_token

        return resolve_git_clone_token(
            settings=self._settings,
            installation_id=installation_id,
            allow_github_app=use_github_app,
        )

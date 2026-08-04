"""Orchestrate clone → detect → generate → persist Launchpad workspace."""

from __future__ import annotations

import json
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
from app.schemas.cloud import CloudCredentials, CloudProvider, IaCEngine
from app.schemas.repo_import import (
    RepoImportCreateRequest,
    RepoImportSaveRequest,
    RepoImportSaveResult,
    RepoImportSessionRead,
    ServiceOverride,
)
from app.services.git_importer import GitImporterError, GitImporterService
from pkg.detector import ProjectDetectorEngine
from pkg.detector.models import DetectedService, DetectionResult
from pkg.generator.workspace import WorkspaceGenerator

logger = get_logger(__name__)

_DETECTION_FILE = ".launchpad/detection.json"


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
        token = self._resolve_token(
            request.use_github_app_token,
            installation_id=request.github_installation_id,
        )
        try:
            cloned = self._importer.clone(
                repo_url=request.git_repo_url,
                branch=request.git_branch,
                token=token,
            )
        except GitImporterError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "repo_import_clone_failed", "message": str(exc)},
            ) from exc

        detection = self._detector.detect(cloned.root_dir)
        self._write_detection(cloned.root_dir, detection)
        meta = self._importer.read_meta(cloned.import_id)
        created_raw = meta.get("created_at")
        created_at = None
        if isinstance(created_raw, str):
            try:
                created_at = datetime.fromisoformat(created_raw)
            except ValueError:
                created_at = datetime.now(UTC)

        logger.info(
            "repo_import_detected",
            import_id=cloned.import_id,
            layout=detection.layout.value,
            services=len(detection.services),
            owner_id=str(owner.id),
        )
        return RepoImportSessionRead(
            import_id=cloned.import_id,
            git_repo_url=cloned.repo_url,
            git_branch=cloned.branch,
            commit_sha=cloned.commit_sha,
            layout=detection.layout,
            detection=detection,
            services=detection.services,
            created_at=created_at,
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

        return RepoImportSessionRead(
            import_id=import_id,
            git_repo_url=str(meta.get("repo_url") or ""),
            git_branch=str(meta.get("branch") or "main"),
            commit_sha=str(meta.get("commit_sha") or ""),
            layout=detection.layout,
            detection=detection,
            services=detection.services,
            created_at=created_at,
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
        generated = self._generator.generate(
            durable,
            adjusted,
            workspace_name=request.name,
            services=services,
        )

        cluster_ready = False
        if request.ensure_local_cluster:
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

        from app.services.orgs import OrganizationService

        personal = await OrganizationService(self._session).ensure_personal_org(owner)
        wizard_snapshot = {
            "source": "repo_import",
            "git_repo_url": meta.get("repo_url"),
            "git_branch": meta.get("branch"),
            "commit_sha": meta.get("commit_sha"),
            "import_id": import_id,
            "detection": adjusted.model_dump(),
            "preview_service": generated.preview_service,
            "iac_engine": IaCEngine.TERRAFORM.value,
            "provider": CloudProvider.LOCAL.value,
        }
        row = ProvisioningWorkspace(
            id=workspace_id,
            owner_id=owner.id,
            org_id=org_id or personal.id,
            name=request.name,
            engine=IaCEngine.TERRAFORM.value,
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
        return RepoImportSaveResult(
            workspace_id=workspace_id,
            name=request.name,
            root_dir=str(durable),
            files=generated.files,
            preview_service=generated.preview_service,
            cluster_ready=cluster_ready,
            message=(
                "Workspace saved. Open Launch and select this workspace to deploy the preview."
                if cluster_ready
                else "Workspace saved. Local cluster was not ready; Launch will start it on deploy."
            ),
        )

    async def discard(self, import_id: str, *, owner: User) -> None:
        del owner
        self._importer.cleanup(import_id)

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

    def _resolve_token(
        self,
        use_github_app: bool,
        *,
        installation_id: int | None = None,
    ) -> str | None:
        if self._settings.github_pat:
            return self._settings.github_pat.strip() or None
        if not use_github_app:
            return None
        try:
            from app.services.github_app import (
                get_installation_access_token,
                is_github_app_configured,
            )

            if not is_github_app_configured(self._settings):
                return None
            return get_installation_access_token(
                installation_id=installation_id,
                settings=self._settings,
            )
        except Exception as exc:
            logger.warning("repo_import_token_unavailable", error=str(exc))
            return None

"""Clone public/private git repositories into isolated import directories."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, sanitize_log_message
from app.services.preview_build import PreviewBuildError, clone_git_repository

logger = get_logger(__name__)

_IMPORT_META = ".launchpad-import.json"


@dataclass(frozen=True, slots=True)
class ImportCloneResult:
    import_id: str
    root_dir: Path
    commit_sha: str
    repo_url: str
    branch: str


class GitImporterError(RuntimeError):
    """Repository import / clone failed."""


class GitImporterService:
    """Securely clone repos into ``/tmp/launchpad/imports/{importId}`` (configurable)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def imports_root(self) -> Path:
        configured = (getattr(self._settings, "repo_import_root", None) or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return Path("/tmp/launchpad/imports")

    def clone(
        self,
        *,
        repo_url: str,
        branch: str = "main",
        token: str | None = None,
        import_id: str | None = None,
    ) -> ImportCloneResult:
        cleaned_url = self._validate_repo_url(repo_url)
        branch_clean = (branch or "main").strip() or "main"
        iid = import_id or str(uuid.uuid4())
        dest = self.imports_root / iid
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            commit_sha = clone_git_repository(
                repo_url=cleaned_url,
                branch=branch_clean,
                commit_sha=None,
                token=token,
                dest=dest,
            )
        except PreviewBuildError as exc:
            shutil.rmtree(dest, ignore_errors=True)
            raise GitImporterError(str(exc)) from exc
        except Exception as exc:
            shutil.rmtree(dest, ignore_errors=True)
            detail = sanitize_log_message(str(exc))
            raise GitImporterError(f"git clone failed: {detail[:800]}") from exc

        meta = {
            "import_id": iid,
            "repo_url": cleaned_url,
            "branch": branch_clean,
            "commit_sha": commit_sha,
            "created_at": datetime.now(UTC).isoformat(),
            "root_dir": str(dest),
        }
        (dest / _IMPORT_META).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        logger.info(
            "repo_import_cloned",
            import_id=iid,
            branch=branch_clean,
            commit_sha=commit_sha[:12],
        )
        return ImportCloneResult(
            import_id=iid,
            root_dir=dest,
            commit_sha=commit_sha,
            repo_url=cleaned_url,
            branch=branch_clean,
        )

    def get_root(self, import_id: str) -> Path:
        dest = self.imports_root / self._safe_id(import_id)
        if not dest.is_dir():
            raise GitImporterError(f"Import '{import_id}' not found or expired")
        return dest

    def read_meta(self, import_id: str) -> dict[str, object]:
        root = self.get_root(import_id)
        meta_path = root / _IMPORT_META
        if not meta_path.is_file():
            return {"import_id": import_id, "root_dir": str(root)}
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GitImporterError("Corrupt import metadata") from exc
        return data if isinstance(data, dict) else {"import_id": import_id}

    def cleanup(self, import_id: str) -> bool:
        dest = self.imports_root / self._safe_id(import_id)
        if not dest.exists():
            return False
        shutil.rmtree(dest, ignore_errors=True)
        logger.info("repo_import_cleaned", import_id=import_id)
        return True

    @staticmethod
    def _safe_id(import_id: str) -> str:
        cleaned = import_id.strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", cleaned):
            raise GitImporterError("Invalid import id")
        return cleaned

    @staticmethod
    def _validate_repo_url(repo_url: str) -> str:
        cleaned = repo_url.strip()
        if not cleaned:
            raise GitImporterError("git_repo_url is required")
        lower = cleaned.lower()
        if not (
            lower.startswith("https://")
            or lower.startswith("http://")
            or lower.startswith("git@")
            or lower.startswith("ssh://")
        ):
            raise GitImporterError("git_repo_url must be an http(s), git@, or ssh URL")
        if any(ch in cleaned for ch in (" ", "\n", "\r", "\t", ";", "|", "&", "`")):
            raise GitImporterError("git_repo_url contains invalid characters")
        return cleaned

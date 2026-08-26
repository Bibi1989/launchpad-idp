"""Storage + lifecycle for user/org-registered declarative cloud plugins.

Persists plugin *manifests* (metadata + runner spec) per organization and exposes them:
* projected into the provider catalog (so they appear in the UI picker),
* materialized as :class:`ManifestPlugin` runners for provisioning.

The IaC bundle for a plugin lives under a per-org directory on disk; the manifest only
holds a pointer to it. No executable code is stored in the database.
"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
import zipfile
from pathlib import Path
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.domain import PluginManifestStore
from app.plugins.manifest import (
    ManifestError,
    ManifestPlugin,
    PluginManifest,
    load_manifest,
    manifest_to_catalog_entry,
)

logger = get_logger(__name__)

# Guards against zip-bombs / runaway bundles.
_MAX_BUNDLE_BYTES = 50 * 1024 * 1024  # 50 MB uncompressed
_MAX_BUNDLE_FILES = 5000


class BundleError(ValueError):
    """An uploaded plugin bundle is unsafe, malformed, or too large."""


def _check_caps(total_bytes: int, file_count: int) -> None:
    if total_bytes > _MAX_BUNDLE_BYTES:
        raise BundleError(f"bundle exceeds {_MAX_BUNDLE_BYTES // (1024 * 1024)} MB uncompressed")
    if file_count > _MAX_BUNDLE_FILES:
        raise BundleError(f"bundle has too many files (> {_MAX_BUNDLE_FILES})")


def _extract_tar(data: bytes, target: Path) -> int:
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            members = tar.getmembers()
            _check_caps(sum(m.size for m in members if m.isreg()),
                        sum(1 for m in members if m.isreg()))
            # filter="data" (3.12+) rejects path traversal, absolute paths, and special files.
            tar.extractall(target, filter="data")
            return sum(1 for m in members if m.isreg())
    except tarfile.TarError as exc:
        raise BundleError(f"invalid tar archive: {exc}") from exc


def _extract_zip(data: bytes, target: Path) -> int:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            _check_caps(sum(i.file_size for i in infos), sum(1 for i in infos if not i.is_dir()))
            for info in infos:
                name = info.filename
                resolved = (target / name).resolve()
                if resolved != target and target not in resolved.parents:
                    raise BundleError(f"zip entry '{name}' escapes the bundle root")
            zf.extractall(target)
            return sum(1 for i in infos if not i.is_dir())
    except zipfile.BadZipFile as exc:
        raise BundleError(f"invalid zip archive: {exc}") from exc


def _plugins_root() -> Path:
    settings = get_settings()
    base = getattr(settings, "iac_workspace_root", None) or str(Path.home() / ".launchpad" / "workspaces")
    return Path(base).parent / "plugins"


class UserPluginService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def bundle_dir(self, org_id: UUID, plugin_id: str) -> Path:
        """Per-org, per-plugin directory where the plugin's IaC bundle lives."""
        return _plugins_root() / str(org_id) / plugin_id

    def user_bundle_dir(self, user_id: UUID, plugin_id: str) -> Path:
        """Per-user directory for personal plugins."""
        return _plugins_root() / "users" / str(user_id) / plugin_id

    def _bundle_for(self, row: PluginManifestStore, plugin_id: str) -> Path:
        if row.owner_user_id is not None:
            return self.user_bundle_dir(row.owner_user_id, plugin_id)
        if row.org_id is not None:
            return self.bundle_dir(row.org_id, plugin_id)
        return _plugins_root() / plugin_id

    async def _row(self, org_id: UUID, plugin_id: str) -> PluginManifestStore | None:
        result = await self._session.execute(
            select(PluginManifestStore).where(
                PluginManifestStore.org_id == org_id,
                PluginManifestStore.plugin_id == plugin_id,
            )
        )
        return result.scalar_one_or_none()

    async def _row_user(self, user_id: UUID, plugin_id: str) -> PluginManifestStore | None:
        result = await self._session.execute(
            select(PluginManifestStore).where(
                PluginManifestStore.owner_user_id == user_id,
                PluginManifestStore.plugin_id == plugin_id,
            )
        )
        return result.scalar_one_or_none()

    async def _accessible_row(
        self,
        plugin_id: str,
        *,
        org_id: UUID | None,
        user_id: UUID | None,
    ) -> PluginManifestStore | None:
        """Prefer the caller's personal plugin, then org, then a public plugin."""
        if user_id is not None:
            own = await self._row_user(user_id, plugin_id)
            if own is not None:
                return own
        if org_id is not None:
            org_row = await self._row(org_id, plugin_id)
            if org_row is not None:
                return org_row
        result = await self._session.execute(
            select(PluginManifestStore).where(
                PluginManifestStore.plugin_id == plugin_id,
                PluginManifestStore.visibility == "public",
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _owner_kind(row: PluginManifestStore) -> str:
        return "user" if row.owner_user_id is not None else "organization"

    @staticmethod
    def _can_edit(row: PluginManifestStore, *, org_id: UUID | None, user_id: UUID | None) -> bool:
        if row.owner_user_id is not None:
            return user_id is not None and row.owner_user_id == user_id
        return org_id is not None and row.org_id == org_id

    def _entry(self, manifest: PluginManifest, row: PluginManifestStore, *, can_edit: bool) -> dict:
        entry = manifest_to_catalog_entry(manifest)
        entry["owner"] = self._owner_kind(row)
        entry["visibility"] = row.visibility or "private"
        entry["can_edit"] = can_edit
        return entry

    async def upsert(
        self,
        org_id: UUID | None,
        manifest: PluginManifest,
        *,
        owner_user_id: UUID | None = None,
        visibility: str = "private",
    ) -> PluginManifest:
        """Create or replace a plugin manifest for an org or user. Ensures the bundle dir exists."""
        vis = visibility if visibility in ("private", "public") else "private"
        if owner_user_id is not None:
            org_id = None
            bundle = self.user_bundle_dir(owner_user_id, manifest.id)
            row = await self._row_user(owner_user_id, manifest.id)
        else:
            if org_id is None:
                raise ValueError("org_id or owner_user_id is required")
            bundle = self.bundle_dir(org_id, manifest.id)
            row = await self._row(org_id, manifest.id)
        bundle.mkdir(parents=True, exist_ok=True)
        payload = manifest.model_dump_json()
        if row is None:
            self._session.add(
                PluginManifestStore(
                    org_id=org_id,
                    owner_user_id=owner_user_id,
                    plugin_id=manifest.id,
                    manifest_json=payload,
                    bundle_path=str(bundle),
                    visibility=vis,
                )
            )
        else:
            row.manifest_json = payload
            row.bundle_path = str(bundle)
            row.visibility = vis
        await self._session.commit()
        return manifest

    async def get_raw(self, org_id: UUID, plugin_id: str) -> dict | None:
        """Return the stored manifest JSON for edit/preview, or None if missing."""
        row = await self._row(org_id, plugin_id)
        if row is None:
            return None
        try:
            data = json.loads(row.manifest_json)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    async def get_for_caller(
        self,
        plugin_id: str,
        *,
        org_id: UUID | None,
        user_id: UUID | None,
    ) -> dict | None:
        row = await self._accessible_row(plugin_id, org_id=org_id, user_id=user_id)
        if row is None:
            return None
        try:
            data = json.loads(row.manifest_json)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return {
            "manifest": data,
            "owner": self._owner_kind(row),
            "visibility": row.visibility or "private",
            "can_edit": self._can_edit(row, org_id=org_id, user_id=user_id),
        }

    async def delete(self, org_id: UUID, plugin_id: str, *, user_id: UUID | None = None) -> bool:
        row = None
        if user_id is not None:
            row = await self._row_user(user_id, plugin_id)
        if row is None:
            row = await self._row(org_id, plugin_id)
        if row is None:
            return False
        if not self._can_edit(row, org_id=org_id, user_id=user_id):
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def list_manifests(self, org_id: UUID) -> list[tuple[PluginManifest, Path]]:
        """Return (manifest, bundle_root) for every plugin registered by an org."""
        result = await self._session.execute(
            select(PluginManifestStore).where(PluginManifestStore.org_id == org_id)
        )
        out: list[tuple[PluginManifest, Path]] = []
        for row in result.scalars().all():
            try:
                manifest = load_manifest(json.loads(row.manifest_json))
            except (ManifestError, json.JSONDecodeError) as exc:
                logger.warning("plugin_manifest_unreadable", plugin_id=row.plugin_id, error=str(exc)[:200])
                continue
            out.append((manifest, Path(row.bundle_path or self.bundle_dir(org_id, row.plugin_id))))
        return out

    async def catalog_entries(self, org_id: UUID, user_id: UUID | None = None) -> list[dict]:
        """Org + personal + public plugins, projected into the provider-catalog shape."""
        clauses = [PluginManifestStore.org_id == org_id]
        if user_id is not None:
            clauses.append(PluginManifestStore.owner_user_id == user_id)
        clauses.append(PluginManifestStore.visibility == "public")
        result = await self._session.execute(select(PluginManifestStore).where(or_(*clauses)))
        ranked: dict[str, tuple[int, dict]] = {}
        for row in result.scalars().all():
            try:
                manifest = load_manifest(json.loads(row.manifest_json))
            except (ManifestError, json.JSONDecodeError) as exc:
                logger.warning("plugin_manifest_unreadable", plugin_id=row.plugin_id, error=str(exc)[:200])
                continue
            can_edit = self._can_edit(row, org_id=org_id, user_id=user_id)
            entry = self._entry(manifest, row, can_edit=can_edit)
            # Prefer personal, then org, then public when plugin ids collide.
            rank = 0 if row.owner_user_id == user_id else 1 if row.org_id == org_id else 2
            previous = ranked.get(manifest.id)
            if previous is None or rank < previous[0]:
                ranked[manifest.id] = (rank, entry)
        return [item[1] for item in ranked.values()]

    async def build_plugin(
        self,
        org_id: UUID,
        plugin_id: str,
        *,
        user_id: UUID | None = None,
    ) -> ManifestPlugin | None:
        """Materialize a runnable ManifestPlugin (manifest + its bundle root)."""
        row = await self._accessible_row(plugin_id, org_id=org_id, user_id=user_id)
        if row is None:
            return None
        manifest = load_manifest(json.loads(row.manifest_json))
        bundle_root = Path(row.bundle_path or self._bundle_for(row, plugin_id))
        return ManifestPlugin(manifest, bundle_root=bundle_root)

    async def store_bundle(
        self,
        org_id: UUID,
        plugin_id: str,
        *,
        filename: str,
        data: bytes,
        user_id: UUID | None = None,
    ) -> int:
        """Extract an uploaded IaC bundle (tar.gz / tgz / tar / zip) into the plugin's dir.

        Replaces any previous bundle. Returns the number of files extracted. Raises
        :class:`BundleError` on unsafe or oversized archives, and KeyError if the plugin
        is not registered for this org.
        """
        row = await self._accessible_row(plugin_id, org_id=org_id, user_id=user_id)
        if row is None or not self._can_edit(row, org_id=org_id, user_id=user_id):
            raise KeyError(f"no plugin '{plugin_id}' for this org")

        target = self._bundle_for(row, plugin_id)
        # Replace the bundle atomically-ish: clear then re-create.
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        lower = filename.lower()
        if lower.endswith(".zip"):
            count = _extract_zip(data, target)
        elif lower.endswith((".tar.gz", ".tgz", ".tar")):
            count = _extract_tar(data, target)
        else:
            # Best-effort sniff: try tar first, then zip.
            try:
                count = _extract_tar(data, target)
            except BundleError:
                count = _extract_zip(data, target)

        row.bundle_path = str(target)
        await self._session.commit()
        logger.info("plugin_bundle_stored", plugin_id=plugin_id, files=count)
        return count

"""Catalog + credential API for the plugin-based multi-cloud provisioning engine.

Additive router:
* GET  /cloud-providers                     - provider catalog (fields/regions/tiers)
* GET  /cloud-providers/{id}                 - one provider's catalog
* GET  /cloud-providers/{id}/tools           - provisioning + config tools for that cloud
* GET  /provisioning-tools                   - full tool catalog (all clouds)
* GET  /cloud-providers/{id}/credentials     - which credential fields are configured
* PUT  /cloud-providers/{id}/credentials     - save credentials (encrypted at rest)
* DELETE /cloud-providers/{id}/credentials   - clear credentials for a provider
* POST /cloud-providers/{id}/validate        - validate credentials against the provider
* POST /plugins/validate                     - dry-run PluginManifest validation (alias: /cloud-providers/plugins/validate)
* POST /plugins/generate                     - natural language -> PluginManifest
* POST /plugins/generate-schemas             - cloud service -> credentialsSchema + deploymentConfigSchema
* POST /plugins/register                     - persist a PluginManifest (alias: /cloud-providers/plugins)
* GET  /plugins/{id}                         - stored manifest JSON for edit
* DELETE /plugins/{id}                       - remove a registered plugin

Secret values are never returned. Actual provisioning continues through the existing flow.
"""

from __future__ import annotations

import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.deps.org import CurrentOrg
from app.plugins.manifest import (
    load_manifest,
    manifest_field_errors,
    manifest_to_catalog_entry,
)
from app.providers.base import ProvisionSpec, RuntimeTarget
from app.providers.provider_services import services_for
from app.providers.provisioning_scaffold import render_provisioning_files
from app.providers.registry import build_catalog, catalog_for, get_provider
from app.providers.service_plugins import adapter_id_for, catalog_overlay_for, expand_service_plugins, merge_catalog
from app.providers.tools import build_tools_catalog, tools_for_cloud
from app.services.plugin_ai import PluginAiService
from app.services.provider_credentials import ProviderCredentialsVault
from app.services.provisioning import ProvisioningService
from app.services.user_plugins import BundleError, UserPluginService

router = APIRouter(tags=["cloud-providers"])


class ProviderCredentialsUpdate(BaseModel):
    credentials: dict[str, str] = Field(default_factory=dict)


class ProviderValidateRequest(BaseModel):
    # If omitted, validate whatever is already stored for the caller.
    credentials: dict[str, str] | None = None


class ProviderValidateResponse(BaseModel):
    valid: bool
    message: str | None = None


class ProvisioningSpecInput(BaseModel):
    """Minimal, UI-friendly description of what to provision (all optional)."""

    name: str | None = None
    image: str | None = None
    app_port: int = 8080
    region: str | None = None
    tier: str | None = None
    runtime_target: str = "docker_host"
    env_vars: dict[str, str] = Field(default_factory=dict)
    ssh_public_key: str | None = None

    def to_spec(self, environment_id: str) -> ProvisionSpec:
        try:
            target = RuntimeTarget(self.runtime_target)
        except ValueError:
            target = RuntimeTarget.DOCKER_HOST
        return ProvisionSpec(
            environment_id=environment_id,
            runtime_target=target,
            name=self.name,
            image=self.image,
            app_port=self.app_port,
            region=self.region,
            tier=self.tier,
            env_vars=self.env_vars,
            ssh_public_key=self.ssh_public_key,
        )


class ProvisioningPreviewRequest(BaseModel):
    tool: str = "scripting"
    spec: ProvisioningSpecInput = Field(default_factory=ProvisioningSpecInput)


class ScaffoldFileOut(BaseModel):
    path: str
    content: str


class ProvisioningScaffoldRequest(BaseModel):
    workspace_id: UUID
    tool: str = "scripting"
    spec: ProvisioningSpecInput = Field(default_factory=ProvisioningSpecInput)


def get_vault(session: AsyncSession = Depends(get_db_session)) -> ProviderCredentialsVault:
    return ProviderCredentialsVault(session)


def get_provisioning_service(session: AsyncSession = Depends(get_db_session)) -> ProvisioningService:
    return ProvisioningService(session)


def get_user_plugins(session: AsyncSession = Depends(get_db_session)) -> UserPluginService:
    return UserPluginService(session)


class PluginManifestUpsert(BaseModel):
    # The full declarative manifest (validated server-side via load_manifest).
    manifest: dict
    owner: Literal["user", "organization"] = "organization"
    visibility: Literal["private", "public"] = "private"


# --- catalog ---------------------------------------------------------------------------


@router.get("/cloud-providers")
async def list_cloud_providers(
    user: CurrentUser,
    org: CurrentOrg,
    plugins: UserPluginService = Depends(get_user_plugins),
) -> list[dict]:
    """Built-in service plugins + this user's personal, org, and public manifest plugins."""
    return merge_catalog(
        expand_service_plugins(build_catalog()),
        await plugins.catalog_entries(org.org_id, user_id=user.id),
    )


@router.get("/provisioning-tools")
async def list_provisioning_tools(user: CurrentUser) -> list[dict]:
    return build_tools_catalog()


# --- user/org declarative plugins (must precede /{provider_id}) ------------------------


@router.get("/cloud-providers/plugins")
async def list_user_plugins(
    user: CurrentUser,
    org: CurrentOrg,
    plugins: UserPluginService = Depends(get_user_plugins),
) -> list[dict]:
    return await plugins.catalog_entries(org.org_id, user_id=user.id)


def _invalid_manifest_detail(errors: list[dict[str, str]]) -> dict[str, object]:
    """HTTPException detail the global handler maps to ``error.details.errors``."""
    summary = "; ".join(f"{item['loc']}: {item['msg']}" for item in errors) or "invalid plugin manifest"
    return {"code": "invalid_manifest", "message": summary, "errors": errors}


@router.post("/plugins/validate")
@router.post("/cloud-providers/plugins/validate")
async def validate_user_plugin(
    payload: PluginManifestUpsert,
    user: CurrentUser,
) -> dict:
    """Dry-run: validate a manifest against the schema without persisting.

    Returns ``{valid, errors: [{loc, msg}], manifest}``. ``credentialsSchema`` and
    ``deploymentConfigSchema`` are checked as valid JSON Schema documents; missing or
    malformed fixed fields are reported with their exact locations.
    """
    errors = manifest_field_errors(payload.manifest)
    if errors:
        return {"valid": False, "errors": errors, "manifest": None}
    manifest = load_manifest(payload.manifest)
    return {"valid": True, "errors": [], "manifest": manifest_to_catalog_entry(manifest)}


class PluginGenerateRequest(BaseModel):
    prompt: str = Field(min_length=8, max_length=4000)


class PluginGenerateSchemasRequest(BaseModel):
    parent_cloud: str | None = Field(default=None, max_length=64)
    service_type: str | None = Field(default=None, max_length=32)
    plugin_id: str | None = Field(default=None, max_length=64)
    label: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=2000)
    prompt: str = Field(default="", max_length=2000)


@router.post("/plugins/generate")
@router.post("/cloud-providers/plugins/generate")
async def generate_user_plugin(payload: PluginGenerateRequest, user: CurrentUser) -> dict:
    """Turn a natural-language description into a PluginManifest (Gemini, else heuristic)."""
    _ = user
    service = PluginAiService()
    try:
        manifest, source = await asyncio.to_thread(service.generate, payload.prompt)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "plugin_generate_invalid", "message": str(exc)},
        ) from exc
    return {"manifest": manifest, "source": source, "gemini_configured": service.gemini_configured}


@router.post("/plugins/generate-schemas")
@router.post("/cloud-providers/plugins/generate-schemas")
async def generate_plugin_schemas(payload: PluginGenerateSchemasRequest, user: CurrentUser) -> dict:
    """Draft credentialsSchema + deploymentConfigSchema from the selected cloud service."""
    _ = user
    service = PluginAiService()
    try:
        creds, deploy, source = await asyncio.to_thread(
            service.generate_schemas,
            parent_cloud=payload.parent_cloud or "",
            service_type=payload.service_type or "",
            plugin_id=payload.plugin_id or "",
            label=payload.label or "",
            category=payload.category or "",
            description=payload.description or "",
            prompt=payload.prompt or "",
        )
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "plugin_generate_invalid", "message": str(exc)},
        ) from exc
    return {
        "credentialsSchema": creds,
        "deploymentConfigSchema": deploy,
        "source": source,
        "gemini_configured": service.gemini_configured,
    }


@router.post("/plugins/register", status_code=status.HTTP_201_CREATED)
@router.post("/cloud-providers/plugins", status_code=status.HTTP_201_CREATED)
async def register_user_plugin(
    payload: PluginManifestUpsert,
    user: CurrentUser,
    org: CurrentOrg,
    plugins: UserPluginService = Depends(get_user_plugins),
) -> dict:
    """Register (or replace) a declarative cloud plugin for this user or org."""
    errors = manifest_field_errors(payload.manifest)
    if errors:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_invalid_manifest_detail(errors),
        )
    manifest = load_manifest(payload.manifest)
    if payload.owner == "user":
        await plugins.upsert(None, manifest, owner_user_id=user.id, visibility=payload.visibility)
    else:
        await plugins.upsert(org.org_id, manifest, visibility=payload.visibility)
    entry = manifest_to_catalog_entry(manifest)
    entry["owner"] = payload.owner
    entry["visibility"] = payload.visibility
    entry["can_edit"] = True
    return entry


@router.get("/plugins/{plugin_id}")
@router.get("/cloud-providers/plugins/{plugin_id}")
async def get_user_plugin(
    plugin_id: str,
    user: CurrentUser,
    org: CurrentOrg,
    plugins: UserPluginService = Depends(get_user_plugins),
) -> dict:
    """Return the stored manifest JSON so the registration modal can edit it."""
    found = await plugins.get_for_caller(plugin_id, org_id=org.org_id, user_id=user.id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no plugin '{plugin_id}'")
    return found


@router.delete("/plugins/{plugin_id}", status_code=status.HTTP_200_OK)
@router.delete("/cloud-providers/plugins/{plugin_id}", status_code=status.HTTP_200_OK)
async def delete_user_plugin(
    plugin_id: str,
    user: CurrentUser,
    org: CurrentOrg,
    plugins: UserPluginService = Depends(get_user_plugins),
) -> dict:
    removed = await plugins.delete(org.org_id, plugin_id, user_id=user.id)
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no plugin '{plugin_id}'")
    return {"deleted": plugin_id}


@router.post("/cloud-providers/plugins/{plugin_id}/bundle", status_code=status.HTTP_200_OK)
async def upload_plugin_bundle(
    plugin_id: str,
    user: CurrentUser,
    org: CurrentOrg,
    file: UploadFile = File(...),
    plugins: UserPluginService = Depends(get_user_plugins),
) -> dict:
    """Upload the IaC bundle (tar.gz / zip) for a registered plugin, extracted safely."""
    data = await file.read()
    try:
        count = await plugins.store_bundle(
            org.org_id,
            plugin_id,
            filename=file.filename or "bundle.tar.gz",
            data=data,
            user_id=user.id,
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except BundleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"plugin_id": plugin_id, "files": count}


@router.get("/cloud-providers/{provider_id}")
async def get_cloud_provider(provider_id: str, user: CurrentUser) -> dict:
    adapter_id = adapter_id_for(provider_id)
    if get_provider(adapter_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown provider '{provider_id}'")
    data = catalog_overlay_for(provider_id, catalog_for(adapter_id))
    if not data.get("services"):
        data["services"] = services_for(adapter_id)
    return data


@router.get("/cloud-providers/{provider_id}/services")
async def get_cloud_provider_services(provider_id: str, user: CurrentUser) -> list[dict]:
    """Services this cloud offers, grouped by runtime (kubernetes / docker / vm / paas)."""
    adapter = _require_known(provider_id)
    overlay = catalog_overlay_for(provider_id, catalog_for(adapter.id))
    services = overlay.get("services")
    if isinstance(services, list) and services:
        return services
    return services_for(adapter.id)


@router.get("/cloud-providers/{provider_id}/tools")
async def get_cloud_provider_tools(provider_id: str, user: CurrentUser) -> list[dict]:
    adapter = _require_known(provider_id)
    return tools_for_cloud(adapter.id)


@router.post("/cloud-providers/{provider_id}/provisioning-preview")
async def preview_provisioning(
    provider_id: str,
    payload: ProvisioningPreviewRequest,
    user: CurrentUser,
) -> list[ScaffoldFileOut]:
    """Render (without writing) the files the selected tool would scaffold."""
    adapter = _require_known(provider_id)
    files = render_provisioning_files(
        payload.tool, adapter.id, payload.spec.to_spec(f"preview-{provider_id}")
    )
    return [ScaffoldFileOut(path=f.path, content=f.content) for f in files]


@router.post("/cloud-providers/{provider_id}/scaffold")
async def scaffold_provisioning_to_workspace(
    provider_id: str,
    payload: ProvisioningScaffoldRequest,
    user: CurrentUser,
    provisioning: ProvisioningService = Depends(get_provisioning_service),
) -> list[ScaffoldFileOut]:
    """Write the selected tool's provisioning files into the workspace directory."""
    adapter = _require_known(provider_id)
    files = render_provisioning_files(
        payload.tool, adapter.id, payload.spec.to_spec(str(payload.workspace_id))
    )
    written: list[ScaffoldFileOut] = []
    for f in files:
        result = await provisioning.write_workspace_file(
            payload.workspace_id, user, relative_path=f.path, content=f.content
        )
        written.append(ScaffoldFileOut(path=result.path, content=f.content))
    return written


# --- credentials -----------------------------------------------------------------------


@router.get("/cloud-providers/{provider_id}/credentials")
async def get_provider_credentials_status(
    provider_id: str,
    user: CurrentUser,
    vault: ProviderCredentialsVault = Depends(get_vault),
) -> dict[str, list[str]]:
    adapter = _require_known(provider_id)
    all_status = await vault.status(user.id)
    fields = all_status.get(adapter.id, []) or all_status.get(provider_id, [])
    return {provider_id: fields}


@router.put("/cloud-providers/{provider_id}/credentials")
async def upsert_provider_credentials(
    provider_id: str,
    payload: ProviderCredentialsUpdate,
    user: CurrentUser,
    vault: ProviderCredentialsVault = Depends(get_vault),
) -> dict[str, list[str]]:
    adapter = _require_known(provider_id)
    return await vault.upsert_provider(user.id, adapter.id, payload.credentials)


@router.delete("/cloud-providers/{provider_id}/credentials")
async def delete_provider_credentials(
    provider_id: str,
    user: CurrentUser,
    vault: ProviderCredentialsVault = Depends(get_vault),
) -> dict[str, list[str]]:
    adapter = _require_known(provider_id)
    return await vault.delete_provider(user.id, adapter.id)


@router.post("/cloud-providers/{provider_id}/validate")
async def validate_provider_credentials(
    provider_id: str,
    payload: ProviderValidateRequest,
    user: CurrentUser,
    vault: ProviderCredentialsVault = Depends(get_vault),
) -> ProviderValidateResponse:
    adapter = _require_known(provider_id)
    credentials = payload.credentials
    if credentials is None:
        credentials = await vault.get_for_provider(user.id, adapter.id)
    if not credentials:
        return ProviderValidateResponse(valid=False, message="No credentials provided or stored.")
    try:
        # Adapters are synchronous (network calls); run off the event loop.
        valid = await asyncio.to_thread(adapter.validate_credentials, credentials)
    except Exception as exc:  # noqa: BLE001 - surface as invalid, not a 500
        return ProviderValidateResponse(valid=False, message=str(exc)[:300])
    return ProviderValidateResponse(valid=bool(valid), message=None if valid else "Credentials rejected.")


def _require_known(provider_id: str):
    adapter = get_provider(adapter_id_for(provider_id))
    if adapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown provider '{provider_id}'")
    return adapter

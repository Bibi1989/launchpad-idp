"""Declarative manifest plugins - how users add a new cloud without writing code.

A user (or operator) registers a cloud by supplying DATA, not code: a manifest that
describes the cloud (credential fields, regions, sizes) and points at an IaC bundle
(Terraform dir / Pulumi program / Ansible playbook) plus which runner executes it.

At provision time, :class:`ManifestPlugin` builds the matching runner plugin (the
Terraform/Pulumi/Ansible adapters in this package), maps the user's credentials + request
into runner inputs, and delegates. Nothing in the manifest is executed as code - only the
existing IaC tools run - so accepting manifests from users is safe.

Manifest example (YAML)::

    id: my-hetzner
    label: My Hetzner (Terraform)
    runtime_targets: [vm]
    credential_fields:
      - {name: hcloud_token, label: Hetzner Token, secret: true, required: true}
    regions:
      - {value: nbg1, label: Nuremberg}
    tiers:
      - {id: cx22, label: "CX22 2vCPU/4GB", vcpus: 2, memory_mb: 4096}
    runner:
      type: terraform
      working_dir: hetzner        # relative to the bundle root
      var_mapping:
        hcloud_token: "${credentials.hcloud_token}"
        region: "${spec.region}"
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.logging import get_logger

from .ansible_config_plugin import AnsibleConfigPlugin
from .aws_terraform_plugin import AwsTerraformPlugin
from .base import CloudServicePlugin, PluginResult, PluginStatus
from .gcp_pulumi_plugin import GcpPulumiPlugin

logger = get_logger(__name__)

# --- manifest schema ------------------------------------------------------------------


# Base for every manifest sub-model: accept both snake_case and camelCase, keep extras.
_LENIENT = ConfigDict(populate_by_name=True, extra="allow")

# Runner engines. IaC engines have a real execution path; the code engines
# (node/python/binary/cli/docker) are accepted by the schema and reserved for the
# sandboxed agent-side executor (see docs/plugin-isolation.md).
RunnerType = Literal[
    "terraform", "opentofu", "pulumi", "ansible",  # IaC (executable today)
    "node", "python", "binary", "cli", "docker", "script",  # code plugins (reserved)
]
_IAC_ENGINES = {"terraform", "opentofu", "pulumi", "ansible"}


def _validate_json_schema(value: dict[str, Any]) -> dict[str, Any]:
    """Ensure a dict is a syntactically valid JSON Schema (Draft 7). Empty is allowed."""
    if not value:
        return value
    from jsonschema import Draft7Validator
    from jsonschema.exceptions import SchemaError

    try:
        Draft7Validator.check_schema(value)
    except SchemaError as exc:
        raise ValueError(f"not a valid JSON Schema: {exc.message}") from exc
    return value


class ManifestCredentialField(BaseModel):
    model_config = _LENIENT
    name: str
    label: str = Field(validation_alias=AliasChoices("label", "displayName", "name", "title"))
    secret: bool = True
    required: bool = True
    help: str | None = None
    placeholder: str | None = None


class ManifestRegion(BaseModel):
    model_config = _LENIENT
    value: str = Field(validation_alias=AliasChoices("value", "id", "slug"))
    label: str = Field(validation_alias=AliasChoices("label", "displayName", "name"))


class ManifestTier(BaseModel):
    model_config = _LENIENT
    id: str = Field(validation_alias=AliasChoices("id", "value", "slug"))
    label: str = Field(validation_alias=AliasChoices("label", "displayName", "name"))
    vcpus: int | None = None
    memory_mb: int | None = None
    monthly_usd: float | None = None


class RunnerConfig(BaseModel):
    """How a plugin is executed. Naming is flexible: ``type`` / ``engine`` / ``runtime``.

    - IaC engines (terraform/opentofu/pulumi/ansible) run the bundle in place today.
    - Code engines (node/python/binary/cli/docker) are accepted for forward-compat and
      run through the sandboxed executor once enabled.
    """

    model_config = _LENIENT

    # `type` is the discriminator; `engine` / `runtime` are accepted aliases.
    type: RunnerType = Field(
        default="node",
        validation_alias=AliasChoices("type", "engine", "runtime", "kind"),
    )
    # IaC runner locations (relative to the bundle root).
    working_dir: str | None = Field(default=None, validation_alias=AliasChoices("working_dir", "workingDir", "dir", "bundle_path", "bundlePath"))
    project_dir: str | None = Field(default=None, validation_alias=AliasChoices("project_dir", "projectDir"))
    stack_name: str = Field(default="dev", validation_alias=AliasChoices("stack_name", "stackName", "stack"))
    playbook_path: str | None = Field(default=None, validation_alias=AliasChoices("playbook_path", "playbookPath", "playbook"))
    # Code/CLI/docker runner hints (reserved).
    entry: str | None = Field(default=None, validation_alias=AliasChoices("entry", "entrypoint", "main"))
    command: str | None = None
    image: str | None = None
    args: list[str] = Field(default_factory=list)
    # runner-input-key -> template ("${credentials.X}" / "${spec.Y}" / literal).
    var_mapping: dict[str, str] = Field(default_factory=dict, validation_alias=AliasChoices("var_mapping", "varMapping", "vars"))


# Backward-compatible alias for the historical name.
RunnerSpec = RunnerConfig


class PluginRunners(BaseModel):
    """Optional split runners: infrastructure provision vs post-create config."""

    model_config = _LENIENT

    provision: RunnerConfig | None = None
    config: RunnerConfig | None = None


class PluginDefaults(BaseModel):
    """Wizard defaults when this plugin is chosen as a deploy target."""

    model_config = _LENIENT

    iac_engine: str | None = Field(
        default=None,
        validation_alias=AliasChoices("iac_engine", "iacEngine"),
        serialization_alias="iacEngine",
    )
    config_tool: str | None = Field(
        default=None,
        validation_alias=AliasChoices("config_tool", "configTool"),
        serialization_alias="configTool",
    )


class PluginManifest(BaseModel):
    model_config = _LENIENT

    id: str
    label: str = Field(validation_alias=AliasChoices("label", "displayName", "name", "title"))
    version: str = Field(default="1.0.0")
    category: str | None = Field(default=None)
    description: str | None = Field(default=None, validation_alias=AliasChoices("description", "summary"))
    icon: str | None = Field(default=None)
    runtime_targets: list[str] = Field(
        default_factory=lambda: ["vm"],
        validation_alias=AliasChoices("runtime_targets", "runtimeTargets", "targets"),
    )
    docs_url: str | None = Field(default=None, validation_alias=AliasChoices("docs_url", "docsUrl", "documentation"))
    homepage: str | None = Field(default=None, validation_alias=AliasChoices("homepage", "home_page", "url"))
    license: str | None = Field(default=None, validation_alias=AliasChoices("license", "licence"))
    author: str | None = None
    keywords: list[str] = Field(default_factory=list)
    parent_cloud: str | None = Field(
        default=None,
        validation_alias=AliasChoices("parent_cloud", "parentCloud"),
        serialization_alias="parentCloud",
    )
    credential_fields: list[ManifestCredentialField] = Field(
        default_factory=list,
        validation_alias=AliasChoices("credential_fields", "credentialFields"),
    )
    regions: list[ManifestRegion] = Field(default_factory=list)
    tiers: list[ManifestTier] = Field(default_factory=list)
    runner: RunnerConfig = Field(
        default_factory=RunnerConfig,
        validation_alias=AliasChoices("runner", "runtime"),
    )
    runners: PluginRunners | None = Field(
        default=None,
        validation_alias=AliasChoices("runners"),
    )
    defaults: PluginDefaults | None = Field(
        default=None,
        validation_alias=AliasChoices("defaults"),
    )
    # Capabilities: a flat list of strings OR a structured object
    # ({serviceType, supportsTtl, supportsCustomDns, ...}). Both are accepted.
    capabilities: list[str] | dict[str, Any] = Field(default_factory=list)
    hooks: dict[str, Any] = Field(default_factory=dict)
    credentials_schema: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("credentials_schema", "credentialsSchema")
    )
    deployment_config_schema: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("deployment_config_schema", "deploymentConfigSchema"),
    )

    # credentialsSchema / deploymentConfigSchema must be valid JSON Schema documents.
    _check_schemas = field_validator("credentials_schema", "deployment_config_schema")(
        staticmethod(_validate_json_schema)
    )

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str) -> str:
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", value.strip()):
            raise ValueError("must be a semantic version, e.g. 1.0.0")
        return value.strip()

    @model_validator(mode="after")
    def _sync_legacy_runner(self) -> PluginManifest:
        """Keep ``runner`` aligned with ``runners.provision`` for older executors."""
        if self.runners and self.runners.provision is not None:
            object.__setattr__(self, "runner", self.runners.provision)
        elif self.runners is None and self.runner.type != "node":
            object.__setattr__(
                self,
                "runners",
                PluginRunners(provision=self.runner),
            )
        return self

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        """Fill an ``id`` from the display name/label when only those are given."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if not d.get("id"):
            label_like = d.get("label") or d.get("displayName") or d.get("name") or d.get("title")
            if label_like:
                d["id"] = _slugify(str(label_like))
        return d


class ManifestError(ValueError):
    """A manifest is invalid or references an unsafe/missing bundle path."""


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "plugin"


# --- variable resolution --------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\$\{(credentials|spec)\.([A-Za-z0-9_]+)\}")


def resolve_inputs(
    var_mapping: Mapping[str, str],
    *,
    credentials: Mapping[str, Any] | None,
    spec: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Resolve ${credentials.X} / ${spec.Y} placeholders (and literals) into runner inputs."""
    creds = credentials or {}
    spc = spec or {}

    def replace(match: re.Match[str]) -> str:
        source, key = match.group(1), match.group(2)
        value = (creds if source == "credentials" else spc).get(key)
        return "" if value is None else str(value)

    resolved: dict[str, str] = {}
    for key, template in var_mapping.items():
        resolved[key] = _PLACEHOLDER.sub(replace, str(template))
    return resolved


# --- the plugin -----------------------------------------------------------------------


class ManifestPlugin(CloudServicePlugin):
    """A cloud plugin defined by a manifest, executed by a Terraform/Pulumi/Ansible runner."""

    def __init__(self, manifest: PluginManifest, *, bundle_root: str | Path) -> None:
        self.manifest = manifest
        self.id = manifest.id
        self.bundle_root = Path(bundle_root).resolve()

    def provision(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        return self._delegate("provision", inputs, phase="provision")

    def configure(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        if self.manifest.runners is None or self.manifest.runners.config is None:
            return PluginResult(
                PluginStatus.SUCCESS,
                "No config runner defined; instance uses LaunchConfig / first-boot metadata.",
            )
        return self._delegate("provision", inputs, phase="config")

    def destroy(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        return self._delegate("destroy", inputs, phase="provision")

    def get_status(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        return self._delegate("get_status", inputs, phase="provision")

    # --- internals ---
    def _runner_for(self, phase: str) -> RunnerConfig:
        runners = self.manifest.runners
        if phase == "config" and runners and runners.config is not None:
            return runners.config
        if runners and runners.provision is not None:
            return runners.provision
        return self.manifest.runner

    def _delegate(
        self,
        action: str,
        inputs: Mapping[str, Any] | None,
        *,
        phase: str = "provision",
    ) -> PluginResult:
        payload = inputs or {}
        credentials = payload.get("credentials")
        spec = payload.get("spec")
        runner_cfg = self._runner_for(phase)
        try:
            runner = self._build_runner(runner_cfg)
            runner_inputs = resolve_inputs(
                runner_cfg.var_mapping, credentials=credentials, spec=spec
            )
        except ManifestError as exc:
            return PluginResult(PluginStatus.FAILED, str(exc))
        method = getattr(runner, action, None)
        if method is None:
            return PluginResult(PluginStatus.FAILED, f"runner does not support action '{action}'")
        return method(runner_inputs)

    def _safe_path(self, relative: str | None) -> Path:
        """Resolve a bundle-relative path, refusing to escape the bundle root."""
        target = (self.bundle_root / (relative or "")).resolve()
        if target != self.bundle_root and self.bundle_root not in target.parents:
            raise ManifestError(f"runner path '{relative}' escapes the bundle root")
        return target

    def _restricted_env(self) -> dict[str, str]:
        """Minimal environment for untrusted user IaC: no control-plane secrets.

        Only PATH (to locate the tool + downloaded providers) and a bundle-local HOME
        (for tool caches) are exposed. All provider auth is passed explicitly as mapped
        vars, never via inherited environment. This is defense-in-depth alongside the
        path-traversal guard - see docs/plugin-isolation.md for the full model.
        """
        import os

        home = self.bundle_root / ".home"
        home.mkdir(parents=True, exist_ok=True)
        return {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(home),
        }

    def _build_runner(self, runner: RunnerConfig | None = None) -> CloudServicePlugin:
        runner = runner or self.manifest.runner
        env = self._restricted_env()
        if runner.type in ("terraform", "opentofu"):
            return AwsTerraformPlugin(
                self._safe_path(runner.working_dir), engine=runner.type, base_env=env
            )
        if runner.type == "pulumi":
            return GcpPulumiPlugin(self._safe_path(runner.project_dir), stack_name=runner.stack_name)
        if runner.type == "ansible":
            if not runner.playbook_path:
                raise ManifestError("ansible runner requires playbook_path")
            return AnsibleConfigPlugin(self._safe_path(runner.playbook_path), base_env=env)
        if runner.type not in _IAC_ENGINES:
            # node/python/binary/cli/docker validate fine, but only run once the sandboxed
            # agent-side executor is enabled (see docs/plugin-isolation.md).
            raise ManifestError(
                f"runner type '{runner.type}' is not executable yet; use an IaC engine "
                f"({', '.join(sorted(_IAC_ENGINES))}) or wait for the sandboxed executor"
            )
        raise ManifestError(f"unsupported runner type '{runner.type}'")


# --- loading + catalog projection -----------------------------------------------------


def _format_validation_error(exc: ValidationError) -> str:
    """Turn a pydantic ValidationError into a short, human-readable summary."""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


def manifest_field_errors(data: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return field-level errors ([] when valid) as ``[{loc, msg}]`` for the UI to map."""
    try:
        PluginManifest.model_validate(dict(data))
        return []
    except ValidationError as exc:
        return [
            {"loc": ".".join(str(p) for p in err.get("loc", ())) or "(root)", "msg": err.get("msg", "invalid")}
            for err in exc.errors()
        ]
    except Exception as exc:  # noqa: BLE001
        return [{"loc": "(root)", "msg": str(exc)}]


def load_manifest(data: Mapping[str, Any]) -> PluginManifest:
    """Validate a manifest dict into a PluginManifest (raises ManifestError on bad data).

    The raised message names the exact fields at fault (e.g. ``label: Field required``)
    rather than a raw pydantic dump.
    """
    try:
        return PluginManifest.model_validate(dict(data))
    except ValidationError as exc:
        raise ManifestError(f"invalid plugin manifest: {_format_validation_error(exc)}") from exc
    except Exception as exc:
        raise ManifestError(f"invalid plugin manifest: {exc}") from exc


def load_manifests_from_dir(directory: str | Path) -> list[tuple[PluginManifest, Path]]:
    """Load every ``*.yaml`` / ``*.yml`` / ``*.json`` manifest in ``directory``.

    Returns (manifest, bundle_root) pairs, where the bundle root is the manifest's own
    parent directory. A single bad manifest is logged (with its path + the exact
    validation issue) and skipped - it never aborts loading the rest or crashes boot.
    """
    import json

    import yaml

    root = Path(directory)
    out: list[tuple[PluginManifest, Path]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in (".yaml", ".yml", ".json"):
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
            out.append((load_manifest(data), path.parent))
        except (ManifestError, json.JSONDecodeError, yaml.YAMLError, OSError) as exc:
            logger.warning("plugin_manifest_load_failed", path=str(path), error=str(exc)[:400])
            continue
    return out


def _capability_labels(capabilities: list[str] | dict[str, Any]) -> list[str]:
    """Normalize capabilities (list or {serviceType, supportsX}) into display strings."""
    if isinstance(capabilities, dict):
        labels = [str(v) for k, v in capabilities.items() if k == "serviceType" and v]
        labels += [k for k, v in capabilities.items() if k != "serviceType" and v is True]
        return labels
    return list(capabilities)


def manifest_to_catalog_entry(manifest: PluginManifest) -> dict[str, Any]:
    """Project a manifest into the same catalog shape the built-in providers use."""
    entry: dict[str, Any] = {
        "id": manifest.id,
        "label": manifest.label,
        "version": manifest.version,
        "category": manifest.category,
        "description": manifest.description,
        "icon": manifest.icon,
        "docs_url": manifest.docs_url,
        "runtime_targets": list(manifest.runtime_targets),
        "credential_fields": [
            f.model_dump(include={"name", "label", "secret", "required", "help", "placeholder"})
            for f in manifest.credential_fields
        ],
        "regions": [r.model_dump(include={"value", "label"}) for r in manifest.regions],
        "tiers": [t.model_dump(include={"id", "label", "vcpus", "memory_mb", "monthly_usd"}) for t in manifest.tiers],
        "capabilities": _capability_labels(manifest.capabilities),
        "source": "manifest",
        "parent_cloud": manifest.parent_cloud,
        "homepage": manifest.homepage,
        "license": manifest.license,
        "author": manifest.author,
        "keywords": list(manifest.keywords),
    }
    if manifest.defaults is not None:
        entry["defaults"] = manifest.defaults.model_dump(by_alias=True, exclude_none=True)
    if manifest.runners is not None:
        entry["runners"] = manifest.runners.model_dump(by_alias=True, exclude_none=True)
    return entry


__all__ = [
    "ManifestCredentialField",
    "ManifestError",
    "ManifestPlugin",
    "ManifestRegion",
    "ManifestTier",
    "PluginDefaults",
    "PluginManifest",
    "PluginRunners",
    "RunnerConfig",
    "RunnerSpec",
    "load_manifest",
    "load_manifests_from_dir",
    "manifest_field_errors",
    "manifest_to_catalog_entry",
    "resolve_inputs",
]

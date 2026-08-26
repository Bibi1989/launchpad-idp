"""Generate a PluginManifest from a natural-language prompt.

Uses Gemini when configured; otherwise a keyword heuristic so register-plugin
still works without an API key.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.plugins.manifest import PluginManifest, load_manifest, manifest_field_errors
from app.services.plugin_ai_schemas import schemas_for_cloud_service

logger = get_logger(__name__)

PLUGIN_MANIFEST_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "label", "version", "category", "description", "runner", "capabilities"],
    "properties": {
        "id": {"type": "string", "description": "kebab-case plugin id"},
        "label": {"type": "string"},
        "version": {"type": "string", "description": "semver, e.g. 1.0.0"},
        "category": {
            "type": "string",
            "enum": ["cloud-provider", "ingress", "notification", "database", "config"],
        },
        "description": {"type": "string"},
        "icon": {"type": "string", "description": "Material Symbols glyph name"},
        "docsUrl": {"type": "string"},
        "homepage": {"type": "string"},
        "license": {"type": "string"},
        "author": {"type": "string"},
        "parentCloud": {
            "type": "string",
            "enum": ["gcp", "aws", "azure", "cloudflare", "hetzner", "digitalocean", ""],
        },
        "runner": {
            "type": "object",
            "required": ["type"],
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["terraform", "opentofu", "pulumi", "ansible", "node", "python", "script"],
                },
                "bundlePath": {"type": "string"},
                "entry": {"type": "string"},
            },
        },
        "capabilities": {
            "type": "object",
            "required": ["serviceType"],
            "properties": {
                "serviceType": {"type": "string", "enum": ["vm", "container", "kubernetes", "paas"]},
                "supportsTtl": {"type": "boolean"},
                "supportsCustomDns": {"type": "boolean"},
                "supportsEphemeralDb": {"type": "boolean"},
            },
        },
        "credentialsSchema": {"type": "object"},
        "deploymentConfigSchema": {"type": "object"},
    },
}

_SYSTEM = """You generate Launchpad cloud plugin manifests (JSON only).
A plugin is one deploy target (GKE, EKS, a DigitalOcean droplet, Cloudflare tunnel),
not a whole cloud account. Account keys live in Settings; set parentCloud to gcp, aws,
azure, or cloudflare when the plugin should reuse those keys.
Use kebab-case ids, semver 1.0.0, Material Symbols icon names, and JSON Schema draft-07
for credentialsSchema and deploymentConfigSchema.
credentialsSchema: for gcp/aws/azure/cloudflare use optional override fields (never
required) so Settings keys apply unless overridden. For other clouds include the native
API token. deploymentConfigSchema must match the concrete service (cluster, registry,
VPC, imageSource external vs native registry, secret backend, VM size, ports).
Keep required credential fields minimal. Do not invent secrets."""

PLUGIN_SCHEMAS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["credentialsSchema", "deploymentConfigSchema"],
    "properties": {
        "credentialsSchema": {"type": "object"},
        "deploymentConfigSchema": {"type": "object"},
    },
}

_SCHEMAS_SYSTEM = """You generate JSON Schema draft-07 for a Launchpad cloud plugin.
Return only credentialsSchema and deploymentConfigSchema as objects.
Account keys for gcp, aws, azure, and cloudflare live in Settings: credentialsSchema
must be optional override fields (writeOnly), never required.
For other clouds include the native API token or key fields.
deploymentConfigSchema must match the concrete service (GKE, EKS, Cloud Run, droplet,
Workers, ...): region or zone, cluster or VM size, imageSource (build_registry vs
external hub), secret backend (cloud secret manager vs native Kubernetes Secrets),
registry, VPC/subnets, ports. additionalProperties must be false. Do not invent secrets."""


class PluginAiService:
    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def gemini_configured(self) -> bool:
        return bool(self._settings.gemini_api_key)

    def generate(self, prompt: str) -> tuple[dict[str, Any], str]:
        """Return (manifest dict, source). Raises ValueError if the result is invalid."""
        text = (prompt or "").strip()
        if len(text) < 8:
            raise ValueError("Describe the plugin in a bit more detail (at least 8 characters).")
        source = "heuristic"
        raw: dict[str, Any]
        if self.gemini_configured:
            try:
                raw = self._generate_with_gemini(text)
                source = "gemini"
            except Exception:
                logger.exception("plugin_ai_gemini_failed")
                raw = self._heuristic_manifest(text)
        else:
            raw = self._heuristic_manifest(text)
        errors = manifest_field_errors(raw)
        if errors:
            raise ValueError("; ".join(f"{item['loc']}: {item['msg']}" for item in errors))
        manifest: PluginManifest = load_manifest(raw)
        return json.loads(manifest.model_dump_json(by_alias=True)), source

    def generate_schemas(
        self,
        *,
        parent_cloud: str = "",
        service_type: str = "",
        plugin_id: str = "",
        label: str = "",
        category: str = "",
        description: str = "",
        prompt: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Return (credentialsSchema, deploymentConfigSchema, source)."""
        context = {
            "parentCloud": (parent_cloud or "").strip(),
            "serviceType": (service_type or "").strip(),
            "pluginId": (plugin_id or "").strip(),
            "label": (label or "").strip(),
            "category": (category or "").strip(),
            "description": (description or "").strip(),
            "notes": (prompt or "").strip(),
        }
        if not any(context[key] for key in ("parentCloud", "serviceType", "pluginId", "label", "notes")):
            raise ValueError("Set a parent cloud, service type, plugin id, or a short description first.")
        source = "heuristic"
        creds: dict[str, Any]
        deploy: dict[str, Any]
        if self.gemini_configured:
            try:
                parsed = self._generate_schemas_with_gemini(context)
                creds = _as_object(parsed.get("credentialsSchema"))
                deploy = _as_object(parsed.get("deploymentConfigSchema"))
                if creds and deploy:
                    source = "gemini"
                else:
                    creds, deploy = schemas_for_cloud_service(
                        parent_cloud=context["parentCloud"],
                        service_type=context["serviceType"],
                        plugin_id=context["pluginId"],
                        label=context["label"],
                        category=context["category"],
                        prompt=context["notes"] or context["description"],
                    )
            except Exception:
                logger.exception("plugin_ai_schemas_gemini_failed")
                creds, deploy = schemas_for_cloud_service(
                    parent_cloud=context["parentCloud"],
                    service_type=context["serviceType"],
                    plugin_id=context["pluginId"],
                    label=context["label"],
                    category=context["category"],
                    prompt=context["notes"] or context["description"],
                )
        else:
            creds, deploy = schemas_for_cloud_service(
                parent_cloud=context["parentCloud"],
                service_type=context["serviceType"],
                plugin_id=context["pluginId"],
                label=context["label"],
                category=context["category"],
                prompt=context["notes"] or context["description"],
            )
        return creds, deploy, source

    def _generate_with_gemini(self, prompt: str) -> dict[str, Any]:
        from google import genai
        from google.genai import types

        api_key = self._settings.gemini_api_key
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self._settings.gemini_model,
            contents=f"Create a Launchpad cloud plugin for:\n\n{prompt}",
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                temperature=0.2,
                response_mime_type="application/json",
                response_json_schema=PLUGIN_MANIFEST_JSON_SCHEMA,
            ),
        )
        raw_text = (response.text or "").strip()
        if not raw_text:
            raise RuntimeError("Gemini returned an empty response")
        parsed: Any = json.loads(raw_text)
        if not isinstance(parsed, dict):
            raise RuntimeError("Gemini did not return a JSON object")
        return parsed

    def _generate_schemas_with_gemini(self, context: dict[str, str]) -> dict[str, Any]:
        from google import genai
        from google.genai import types

        api_key = self._settings.gemini_api_key
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self._settings.gemini_model,
            contents=(
                "Generate credentialsSchema and deploymentConfigSchema for this Launchpad plugin:\n\n"
                + json.dumps(context, indent=2)
            ),
            config=types.GenerateContentConfig(
                system_instruction=_SCHEMAS_SYSTEM,
                temperature=0.2,
                response_mime_type="application/json",
                response_json_schema=PLUGIN_SCHEMAS_JSON_SCHEMA,
            ),
        )
        raw_text = (response.text or "").strip()
        if not raw_text:
            raise RuntimeError("Gemini returned an empty response")
        parsed: Any = json.loads(raw_text)
        if not isinstance(parsed, dict):
            raise RuntimeError("Gemini did not return a JSON object")
        return parsed

    def _heuristic_manifest(self, prompt: str) -> dict[str, Any]:
        text = prompt.lower()
        parent = ""
        service_type = "vm"
        icon = "cloud"
        runner_type = "terraform"
        label = prompt.strip()[:64]
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "cloud-plugin"

        if any(token in text for token in ("gke", "gcp", "google cloud", "cloud run", "compute engine")):
            parent = "gcp"
            icon = "cloud_sync"
            if "gke" in text or "kubernetes" in text:
                service_type = "kubernetes"
                icon = "hub"
                slug = "gcp-gke"
                label = "Google GKE"
            elif "cloud run" in text:
                service_type = "container"
                icon = "directions_run"
                slug = "gcp-cloud-run"
                label = "Cloud Run"
            else:
                slug = "gcp-compute"
                label = "Compute Engine"
        elif any(token in text for token in ("eks", "ecs", "aws", "amazon", "lambda", "ec2")):
            parent = "aws"
            icon = "cloud_upload"
            if "eks" in text or "kubernetes" in text:
                service_type = "kubernetes"
                icon = "hub"
                slug = "aws-eks"
                label = "Amazon EKS"
            elif "ecs" in text or "fargate" in text:
                service_type = "container"
                icon = "sailing"
                slug = "aws-ecs"
                label = "Amazon ECS"
            else:
                slug = "aws-ec2"
                label = "Amazon EC2"
        elif any(token in text for token in ("aks", "azure", "container apps")):
            parent = "azure"
            icon = "cloud_queue"
            service_type = "kubernetes" if "aks" in text or "kubernetes" in text else "container"
            slug = "azure-aks" if service_type == "kubernetes" else "azure-container-apps"
            label = "Azure AKS" if service_type == "kubernetes" else "Azure Container Apps"
            icon = "hub" if service_type == "kubernetes" else "view_in_ar"
        elif any(token in text for token in ("cloudflare", "workers", "tunnel")):
            parent = "cloudflare"
            icon = "cyclone"
            service_type = "paas"
            runner_type = "script"
            slug = "cloudflare-workers" if "worker" in text else "cloudflare-tunnel"
            label = "Cloudflare Workers" if "worker" in text else "Cloudflare Tunnel"
        elif "digitalocean" in text or "droplet" in text:
            parent = "digitalocean"
            icon = "water_drop"
            slug = "digitalocean-droplet"
            label = "DigitalOcean Droplets"
        elif "hetzner" in text:
            parent = "hetzner"
            icon = "dns"
            slug = "hetzner-server"
            label = "Hetzner Cloud Server"

        creds, deploy = schemas_for_cloud_service(
            parent_cloud=parent,
            service_type=service_type,
            plugin_id=slug,
            label=label,
            prompt=prompt,
        )

        return {
            "id": slug[:64],
            "label": label,
            "version": "1.0.0",
            "category": "cloud-provider",
            "description": prompt.strip()[:240],
            "icon": icon,
            **({"parentCloud": parent} if parent else {}),
            "runner": {"type": runner_type, "bundlePath": parent or slug},
            "capabilities": {
                "serviceType": service_type,
                "supportsTtl": True,
                "supportsCustomDns": True,
                "supportsEphemeralDb": False,
            },
            "credentialsSchema": creds,
            "deploymentConfigSchema": deploy,
        }


def _as_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}

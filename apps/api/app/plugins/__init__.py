"""Deployment-runner plugin/adapter layer.

Thin adapters that WRAP existing IaC/config tools (Terraform CLI, Pulumi Automation API,
Ansible CLI) as their internal drivers. No infrastructure code is generated or modified
here - the plugins only execute the existing ``.tf`` / Pulumi program / ``playbook.yml``.

Public surface:
    CloudServicePlugin   - interface (provision / destroy / get_status)
    AwsTerraformPlugin   - runs existing Terraform in its directory
    GcpPulumiPlugin      - runs existing Pulumi program via the Automation API
    AnsibleConfigPlugin  - runs existing playbook.yml against a host IP
    PluginRegistry       - register + look up runners
"""

from __future__ import annotations

from .ansible_config_plugin import AnsibleConfigPlugin
from .aws_terraform_plugin import AwsTerraformPlugin
from .base import CloudServicePlugin, PluginResult, PluginStatus
from .gcp_pulumi_plugin import GcpPulumiPlugin
from .manifest import (
    ManifestError,
    ManifestPlugin,
    PluginManifest,
    RunnerConfig,
    RunnerSpec,
    load_manifest,
    load_manifests_from_dir,
    manifest_field_errors,
    manifest_to_catalog_entry,
    resolve_inputs,
)
from .registry import PluginRegistry, default_registry

__all__ = [
    "AnsibleConfigPlugin",
    "AwsTerraformPlugin",
    "CloudServicePlugin",
    "GcpPulumiPlugin",
    "ManifestError",
    "ManifestPlugin",
    "PluginManifest",
    "PluginRegistry",
    "PluginResult",
    "PluginStatus",
    "RunnerSpec",
    "default_registry",
    "load_manifest",
    "load_manifests_from_dir",
    "manifest_field_errors",
    "manifest_to_catalog_entry",
    "resolve_inputs",
]

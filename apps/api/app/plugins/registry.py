"""Registry for deployment-runner plugins.

Register a runner under a string key and look it up cleanly at call time. Plugins are
registered as instances because each one is bound to a specific IaC location (a Terraform
directory, a Pulumi project, or a playbook path).

Example::

    registry = PluginRegistry()
    registry.register("aws", AwsTerraformPlugin("infra/terraform/aws"))
    registry.register("gcp", GcpPulumiPlugin("infra/pulumi", stack_name="prod"))
    registry.register("configure", AnsibleConfigPlugin("infra/ansible/playbook.yml"))

    registry.require("aws").provision()
    registry.require("configure").provision({"host": "203.0.113.10"})
"""

from __future__ import annotations

from app.core.logging import get_logger

from .base import CloudServicePlugin

logger = get_logger(__name__)


class PluginRegistry:
    """Holds deployment runners keyed by a stable string."""

    def __init__(self) -> None:
        self._plugins: dict[str, CloudServicePlugin] = {}

    def register(self, key: str, plugin: CloudServicePlugin, *, override: bool = False) -> CloudServicePlugin:
        key = (key or "").strip()
        if not key:
            raise ValueError("plugin key must be a non-empty string")
        if key in self._plugins and not override:
            logger.debug("plugin_already_registered", key=key)
            return self._plugins[key]
        self._plugins[key] = plugin
        logger.info("plugin_registered", key=key, plugin=type(plugin).__name__)
        return plugin

    def get(self, key: str) -> CloudServicePlugin | None:
        return self._plugins.get((key or "").strip())

    def require(self, key: str) -> CloudServicePlugin:
        plugin = self.get(key)
        if plugin is None:
            raise KeyError(f"no plugin registered under '{key}'")
        return plugin

    def unregister(self, key: str) -> None:
        self._plugins.pop((key or "").strip(), None)

    def keys(self) -> list[str]:
        return list(self._plugins.keys())

    def items(self) -> list[tuple[str, CloudServicePlugin]]:
        return list(self._plugins.items())

    def __contains__(self, key: str) -> bool:
        return (key or "").strip() in self._plugins

    def __len__(self) -> int:
        return len(self._plugins)


# A process-wide default registry for callers that want a shared instance.
default_registry = PluginRegistry()

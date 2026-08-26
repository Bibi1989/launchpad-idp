"""GCP runner: wraps the existing Pulumi program via the Pulumi Automation API.

This plugin does NOT define any Pulumi resources - it drives the Pulumi program that
already exists in ``project_dir`` (its ``Pulumi.yaml`` + program) through the Automation
API. PATH/backend/passphrase are taken from the existing ``iac_cli.ensure_pulumi_env``
helper, and the ``pulumi`` CLI is resolved/bootstrapped via ``resolve_pulumi_bin``.

The Pulumi Automation API (``pulumi`` Python package) is an optional dependency; if it is
not installed the plugin returns a clear, non-fatal result instead of raising.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.services.iac_cli import IaCCliError, ensure_pulumi_env, resolve_pulumi_bin

from .base import CloudServicePlugin, PluginResult, PluginStatus

logger = get_logger(__name__)


class GcpPulumiPlugin(CloudServicePlugin):
    """Executes the existing Pulumi program in ``project_dir`` via the Automation API."""

    id = "gcp-pulumi"

    def __init__(
        self,
        project_dir: str | Path,
        *,
        stack_name: str = "dev",
        timeout_seconds: float = 1800.0,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.stack_name = stack_name
        self.timeout_seconds = timeout_seconds

    # --- lifecycle ---
    def provision(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        stack, err = self._select_stack()
        if err is not None:
            return err
        try:
            self._apply_config(stack, inputs)
            up = stack.up(on_output=logger.debug)
            outputs = {k: v.value for k, v in up.outputs.items()}
            return PluginResult(
                PluginStatus.SUCCESS,
                f"pulumi up: {up.summary.result}",
                outputs=outputs,
                raw=(up.stdout or "")[-8000:],
            )
        except Exception as exc:  # noqa: BLE001 - surface Automation API failures cleanly
            return PluginResult(PluginStatus.FAILED, f"pulumi up failed: {exc}")

    def destroy(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        stack, err = self._select_stack()
        if err is not None:
            return err
        try:
            res = stack.destroy(on_output=logger.debug)
            return PluginResult(
                PluginStatus.DESTROYED,
                f"pulumi destroy: {res.summary.result}",
                raw=(res.stdout or "")[-8000:],
            )
        except Exception as exc:  # noqa: BLE001
            return PluginResult(PluginStatus.FAILED, f"pulumi destroy failed: {exc}")

    def get_status(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        stack, err = self._select_stack()
        if err is not None:
            return err
        try:
            outputs = {k: v.value for k, v in stack.outputs().items()}
            info = stack.info()
            has_state = bool(outputs) or (info is not None)
            return PluginResult(
                PluginStatus.RUNNING if has_state else PluginStatus.UNKNOWN,
                "stack has state" if has_state else "no stack state",
                outputs=outputs,
            )
        except Exception as exc:  # noqa: BLE001
            return PluginResult(PluginStatus.UNKNOWN, f"pulumi status unavailable: {exc}")

    # --- internals: wrap the Pulumi Automation API ---
    def _select_stack(self):
        """Create-or-select the stack for the existing program. Returns (stack, error)."""
        try:
            from pulumi import automation as auto
        except ImportError:
            return None, PluginResult(
                PluginStatus.SKIPPED,
                "Pulumi Automation API not installed (pip install pulumi)",
            )
        # Ensure the pulumi CLI (which the Automation API shells out to) is available.
        try:
            resolve_pulumi_bin(install_if_missing=True)
        except IaCCliError as exc:
            return None, PluginResult(PluginStatus.SKIPPED, f"pulumi CLI unavailable: {exc}")

        env_vars = ensure_pulumi_env()
        try:
            stack = auto.create_or_select_stack(
                stack_name=self.stack_name,
                work_dir=str(self.project_dir),
                opts=auto.LocalWorkspaceOptions(env_vars=env_vars),
            )
            return stack, None
        except Exception as exc:  # noqa: BLE001
            return None, PluginResult(PluginStatus.FAILED, f"pulumi stack select failed: {exc}")

    @staticmethod
    def _apply_config(stack: Any, inputs: Mapping[str, Any] | None) -> None:
        if not inputs:
            return
        from pulumi import automation as auto

        for key, value in inputs.items():
            stack.set_config(key, auto.ConfigValue(value=str(value)))

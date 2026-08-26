"""AWS runner: wraps the existing Terraform CLI against existing ``.tf`` code.

This plugin does NOT write or template any Terraform - it runs ``terraform`` in the
directory you point it at (where your ``.tf`` files already live). The Terraform binary
is resolved through the existing ``iac_cli.resolve_terraform_bin`` helper, so tool
bootstrapping stays consistent with the rest of Launchpad.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.services.iac_cli import resolve_terraform_bin

from .base import CloudServicePlugin, PluginResult, PluginStatus

logger = get_logger(__name__)


class AwsTerraformPlugin(CloudServicePlugin):
    """Executes the existing Terraform code in ``working_dir`` (its current directory)."""

    id = "aws-terraform"

    def __init__(
        self,
        working_dir: str | Path,
        *,
        engine: str = "terraform",
        timeout_seconds: float = 1800.0,
        base_env: dict[str, str] | None = None,
    ) -> None:
        self.working_dir = Path(working_dir)
        self.engine = engine  # "terraform" or "opentofu" (tofu)
        self.timeout_seconds = timeout_seconds
        # When set, the process runs with ONLY this env (plus TF automation flags) instead
        # of inheriting the full control-plane environment. Used to isolate untrusted
        # user IaC so it cannot read host secrets from the environment.
        self.base_env = base_env

    # --- lifecycle ---
    def provision(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        binary = self._binary()
        if binary is None:
            return PluginResult(PluginStatus.SKIPPED, "terraform/opentofu CLI not available")
        init = self._run([binary, "init", "-input=false", "-no-color"])
        if init.returncode != 0:
            return PluginResult(PluginStatus.FAILED, "terraform init failed", raw=_combined(init))
        cmd = [binary, "apply", "-auto-approve", "-input=false", "-no-color", *self._var_args(inputs)]
        proc = self._run(cmd)
        if proc.returncode != 0:
            return PluginResult(PluginStatus.FAILED, "terraform apply failed", raw=_combined(proc))
        return PluginResult(
            PluginStatus.SUCCESS,
            "terraform apply complete",
            outputs=self._outputs(binary),
            raw=_combined(proc),
        )

    def destroy(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        binary = self._binary()
        if binary is None:
            return PluginResult(PluginStatus.SKIPPED, "terraform/opentofu CLI not available")
        cmd = [binary, "destroy", "-auto-approve", "-input=false", "-no-color", *self._var_args(inputs)]
        proc = self._run(cmd)
        if proc.returncode != 0:
            return PluginResult(PluginStatus.FAILED, "terraform destroy failed", raw=_combined(proc))
        return PluginResult(PluginStatus.DESTROYED, "terraform destroy complete", raw=_combined(proc))

    def get_status(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        binary = self._binary()
        if binary is None:
            return PluginResult(PluginStatus.SKIPPED, "terraform/opentofu CLI not available")
        show = self._run([binary, "show", "-json", "-no-color"])
        if show.returncode != 0:
            return PluginResult(PluginStatus.UNKNOWN, "terraform show failed", raw=_combined(show))
        try:
            state = json.loads(show.stdout or "{}")
        except json.JSONDecodeError:
            return PluginResult(PluginStatus.UNKNOWN, "unparseable terraform state")
        has_resources = bool(state.get("values", {}).get("root_module", {}).get("resources"))
        return PluginResult(
            PluginStatus.RUNNING if has_resources else PluginStatus.UNKNOWN,
            "state present" if has_resources else "no resources in state",
            outputs=self._outputs(binary),
        )

    # --- internals: wrap the Terraform CLI ---
    def _binary(self) -> str | None:
        return resolve_terraform_bin(prefer="tofu" if self.engine == "opentofu" else "terraform")

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        base = self.base_env if self.base_env is not None else dict(os.environ)
        merged = {**base, "TF_IN_AUTOMATION": "1"}
        return subprocess.run(
            cmd,
            cwd=str(self.working_dir),
            env=merged,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )

    @staticmethod
    def _var_args(inputs: Mapping[str, Any] | None) -> list[str]:
        args: list[str] = []
        for key, value in (inputs or {}).items():
            args += ["-var", f"{key}={value}"]
        return args

    def _outputs(self, binary: str) -> dict[str, Any]:
        proc = self._run([binary, "output", "-json", "-no-color"])
        if proc.returncode != 0:
            return {}
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return {}
        # terraform output -json => {name: {value, type, sensitive}}
        return {name: entry.get("value") for name, entry in data.items()}


def _combined(proc: subprocess.CompletedProcess[str]) -> str:
    return ((proc.stdout or "") + (proc.stderr or ""))[-8000:]

"""Deployment-runner plugin architecture.

Each plugin is a thin ADAPTER around an existing IaC/config tool - Terraform CLI, the
Pulumi Automation API, or the Ansible CLI. The plugins DO NOT own or generate any
infrastructure code; they only execute the existing ``.tf`` / Pulumi program /
``playbook.yml`` that already lives on disk. The real tool is the internal driver.

Contract (uniform across every runner):
    provision(inputs)   -> apply / bring up
    destroy(inputs)     -> tear down
    get_status(inputs)  -> report current state

``inputs`` is an optional mapping whose meaning is tool-specific (Terraform/Pulumi vars,
or the target host IP for Ansible). Keeping one signature lets the PluginRegistry treat
every runner the same way.
"""

from __future__ import annotations

import abc
import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class PluginStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RUNNING = "running"
    DESTROYED = "destroyed"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


@dataclass
class PluginResult:
    """Uniform outcome for every runner action."""

    status: PluginStatus
    message: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    resource_ids: list[str] = field(default_factory=list)
    # Raw combined stdout/stderr from the underlying tool, for logs/debugging.
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (PluginStatus.SUCCESS, PluginStatus.RUNNING, PluginStatus.DESTROYED)


class CloudServicePlugin(abc.ABC):
    """Interface every deployment runner implements.

    Concrete plugins receive the location of the EXISTING IaC code (a Terraform
    directory, a Pulumi project dir, or a playbook path) at construction time, and run
    that code in place.
    """

    # Stable key used by the PluginRegistry (e.g. "aws-terraform").
    id: str = ""

    @abc.abstractmethod
    def provision(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        """Apply / bring up the target using the wrapped tool."""

    @abc.abstractmethod
    def destroy(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        """Tear down the target using the wrapped tool."""

    @abc.abstractmethod
    def get_status(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        """Report the current state of the target."""

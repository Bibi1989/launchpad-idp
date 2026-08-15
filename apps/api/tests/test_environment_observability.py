"""Environment observability metrics and health pings."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.domain import EnvironmentStatus
from app.services.cost_metering import NamespaceUsage
from app.services.environment_observability import (
    build_metrics_for_environment,
    ping_environment_health,
)
from app.services.preview_smoke import SmokeCheckResult


def _env(**kwargs):
    row = MagicMock()
    row.id = kwargs.get("id", uuid4())
    row.name = kwargs.get("name", "demo")
    row.status = kwargs.get("status", EnvironmentStatus.RUNNING)
    row.namespace_name = kwargs.get("namespace_name", "ns-demo")
    row.preview_url = kwargs.get("preview_url", "http://127.0.0.1:3080/p/demo")
    row.provider = "local"
    row.deploy_mode = "preview"
    return row


def test_build_metrics_for_environment_available() -> None:
    env = _env()
    usage = NamespaceUsage(
        cpu_cores=Decimal("0.5"),
        memory_gib=Decimal("1.0"),
        source="usage_requests",
    )
    provisioner = MagicMock()
    provisioner.clients_ready = True
    provisioner.read_namespace_usage.return_value = usage
    with patch(
        "app.services.kubernetes.KubernetesProvisioner",
        return_value=provisioner,
    ):
        metrics = build_metrics_for_environment(env)
    assert metrics.available is True
    assert metrics.cpu_cores == 0.5
    assert metrics.memory_gib == 1.0
    assert metrics.source == "usage_requests"
    assert metrics.cpu_percent is not None


def test_ping_environment_health_ok() -> None:
    env = _env()
    with patch(
        "app.services.environment_observability.run_preview_smoke_check",
        return_value=SmokeCheckResult(True, 200, "ok"),
    ):
        health = ping_environment_health(env)
    assert health.ok is True
    assert health.status_code == 200
    assert health.latency_ms is not None


def test_ping_environment_health_no_url() -> None:
    env = _env(preview_url=None)
    health = ping_environment_health(env)
    assert health.ok is False
    assert health.message == "no_preview_url"

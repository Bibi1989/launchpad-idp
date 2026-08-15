"""Live environment metrics (CPU/mem) and HTTP health pings."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.domain import Environment, EnvironmentStatus, User
from app.schemas.observability import (
    EnvironmentHealthPingRead,
    EnvironmentMetricsRead,
    EnvironmentObservabilityItem,
    EnvironmentObservabilitySummary,
)
from app.services.preview_smoke import run_preview_smoke_check

logger = get_logger(__name__)

_ACTIVE_STATUSES = frozenset(
    {
        EnvironmentStatus.RUNNING,
        EnvironmentStatus.PROVISIONING,
        EnvironmentStatus.PAUSED,
    }
)


def _quota_cpu_cores(settings: Settings) -> Decimal:
    raw = (settings.kubernetes_cpu_request or "2").strip() or "2"
    from app.services.cost_metering import parse_cpu_cores

    value = parse_cpu_cores(raw)
    return value if value > 0 else Decimal("2")


def _quota_memory_gib(settings: Settings) -> Decimal:
    from app.services.cost_metering import parse_memory_gib

    raw = (settings.kubernetes_memory_request or "2Gi").strip() or "2Gi"
    value = parse_memory_gib(raw)
    return value if value > 0 else Decimal("2")


def _pct(used: Decimal, limit: Decimal) -> float | None:
    if limit <= 0:
        return None
    return float(min(Decimal("100"), (used / limit) * Decimal("100")))


def build_metrics_for_environment(
    environment: Environment,
    *,
    settings: Settings | None = None,
) -> EnvironmentMetricsRead:
    """Sample namespace CPU/memory via the cluster API (best-effort)."""
    cfg = settings or get_settings()
    now = datetime.now(UTC)
    base = EnvironmentMetricsRead(
        environment_id=environment.id,
        name=environment.name,
        status=environment.status.value,
        namespace_name=environment.namespace_name,
        sampled_at=now,
        available=False,
        detail="kubernetes_unavailable",
    )
    if environment.status == EnvironmentStatus.DESTROYED:
        return base.model_copy(update={"detail": "environment_destroyed"})

    from app.services.kubernetes import KubernetesProvisioner

    provisioner = KubernetesProvisioner(cfg)
    if not provisioner.clients_ready:
        return base

    try:
        usage = provisioner.read_namespace_usage(environment.namespace_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "environment_metrics_read_failed",
            environment_id=str(environment.id),
            error=str(exc)[:300],
        )
        return base.model_copy(update={"detail": f"read_failed:{exc}"[:200]})

    if usage is None:
        return base.model_copy(update={"detail": "no_usage_data"})

    cpu = Decimal(usage.cpu_cores)
    mem = Decimal(usage.memory_gib)
    return EnvironmentMetricsRead(
        environment_id=environment.id,
        name=environment.name,
        status=environment.status.value,
        namespace_name=environment.namespace_name,
        cpu_cores=float(cpu),
        memory_gib=float(mem),
        cpu_percent=_pct(cpu, _quota_cpu_cores(cfg)),
        memory_percent=_pct(mem, _quota_memory_gib(cfg)),
        source=usage.source,
        available=True,
        detail=None,
        sampled_at=now,
    )


def ping_environment_health(
    environment: Environment,
    *,
    settings: Settings | None = None,
) -> EnvironmentHealthPingRead:
    """HTTP GET the preview URL (same smoke path as GitHub status checks)."""
    cfg = settings or get_settings()
    now = datetime.now(UTC)
    url = (environment.preview_url or "").strip() or None
    if not url:
        return EnvironmentHealthPingRead(
            environment_id=environment.id,
            name=environment.name,
            status=environment.status.value,
            ok=False,
            status_code=None,
            message="no_preview_url",
            preview_url=None,
            latency_ms=None,
            checked_at=now,
        )
    if environment.status not in {
        EnvironmentStatus.RUNNING,
        EnvironmentStatus.PROVISIONING,
    }:
        return EnvironmentHealthPingRead(
            environment_id=environment.id,
            name=environment.name,
            status=environment.status.value,
            ok=False,
            status_code=None,
            message=f"status_{environment.status.value.lower()}",
            preview_url=url,
            latency_ms=None,
            checked_at=now,
        )

    started = time.perf_counter()
    result = run_preview_smoke_check(url, settings=cfg)
    latency_ms = (time.perf_counter() - started) * 1000.0
    return EnvironmentHealthPingRead(
        environment_id=environment.id,
        name=environment.name,
        status=environment.status.value,
        ok=result.ok,
        status_code=result.status_code,
        message=result.message,
        preview_url=url,
        latency_ms=round(latency_ms, 1),
        checked_at=now,
    )


async def summarize_observability_for_owner(
    session: AsyncSession,
    owner: User,
    *,
    settings: Settings | None = None,
    limit: int = 24,
) -> EnvironmentObservabilitySummary:
    """Batch metrics + health pings for the owner's active environments."""
    cfg = settings or get_settings()
    now = datetime.now(UTC)
    result = await session.execute(
        select(Environment)
        .where(
            Environment.owner_id == owner.id,
            Environment.status.in_(list(_ACTIVE_STATUSES)),
        )
        .order_by(Environment.updated_at.desc())
        .limit(max(1, min(limit, 50)))
    )
    rows = list(result.scalars().all())
    items: list[EnvironmentObservabilityItem] = []
    healthy = unhealthy = unknown = 0
    for env in rows:
        metrics = build_metrics_for_environment(env, settings=cfg)
        health = ping_environment_health(env, settings=cfg)
        if not health.preview_url:
            unknown += 1
        elif health.ok:
            healthy += 1
        else:
            unhealthy += 1
        items.append(
            EnvironmentObservabilityItem(
                environment_id=env.id,
                name=env.name,
                status=env.status.value,
                provider=env.provider,
                deploy_mode=env.deploy_mode,
                app_ready=bool(env.preview_url)
                and env.status
                in {EnvironmentStatus.RUNNING, EnvironmentStatus.PROVISIONING},
                preview_url=env.preview_url,
                metrics=metrics,
                health=health,
            )
        )
    return EnvironmentObservabilitySummary(
        items=items,
        healthy_count=healthy,
        unhealthy_count=unhealthy,
        unknown_count=unknown,
        sampled_at=now,
    )

"""Usage-based environment cost metering (infra rate card).

Accrues cost from Kubernetes ResourceQuota usage (preferred) or pod resource
requests, multiplied by configurable $/hour rates. Falls back to the stored
``cost_estimate_hourly`` when the cluster is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.domain import Environment, EnvironmentStatus

logger = get_logger(__name__)

CostSource = Literal["usage_quota", "usage_requests", "estimate", "idle"]

BILLABLE_STATUSES = frozenset(
    {
        EnvironmentStatus.RUNNING,
        EnvironmentStatus.PROVISIONING,
        EnvironmentStatus.FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class NamespaceUsage:
    cpu_cores: Decimal
    memory_gib: Decimal
    source: CostSource


def parse_cpu_cores(value: str | None) -> Decimal:
    """Parse Kubernetes CPU quantity to cores (``100m`` → ``0.1``)."""
    if value is None:
        return Decimal("0")
    raw = str(value).strip()
    if not raw:
        return Decimal("0")
    if raw.endswith("m"):
        return (Decimal(raw[:-1]) / Decimal("1000")).quantize(Decimal("0.0001"))
    return Decimal(raw).quantize(Decimal("0.0001"))


def parse_memory_gib(value: str | None) -> Decimal:
    """Parse Kubernetes memory quantity to GiB."""
    if value is None:
        return Decimal("0")
    raw = str(value).strip()
    if not raw:
        return Decimal("0")
    multipliers: dict[str, Decimal] = {
        "Ki": Decimal(1024) ** 1,
        "Mi": Decimal(1024) ** 2,
        "Gi": Decimal(1024) ** 3,
        "Ti": Decimal(1024) ** 4,
        "K": Decimal(1000) ** 1,
        "M": Decimal(1000) ** 2,
        "G": Decimal(1000) ** 3,
        "T": Decimal(1000) ** 4,
    }
    for suffix, factor in multipliers.items():
        if raw.endswith(suffix):
            bytes_val = Decimal(raw[: -len(suffix)]) * factor
            return (bytes_val / Decimal(1024**3)).quantize(Decimal("0.0001"))
    # Plain integer → bytes
    return (Decimal(raw) / Decimal(1024**3)).quantize(Decimal("0.0001"))


def burn_rate_hourly(
    usage: NamespaceUsage,
    *,
    environment: Environment,
    settings: Settings,
) -> Decimal:
    """Compute $/hour from measured usage + datastore add-ons."""
    cpu_cost = usage.cpu_cores * settings.cost_rate_cpu_core_hour
    mem_cost = usage.memory_gib * settings.cost_rate_memory_gib_hour
    datastore = Decimal("0")
    if environment.enable_postgres:
        datastore += settings.cost_rate_postgres_hour
    if environment.enable_redis:
        datastore += settings.cost_rate_redis_hour
    total = cpu_cost + mem_cost + datastore
    if total <= 0:
        return environment.cost_estimate_hourly
    return total.quantize(Decimal("0.0001"))


def resolve_burn_rate(
    *,
    environment: Environment,
    settings: Settings,
    usage: NamespaceUsage | None,
) -> tuple[Decimal, CostSource]:
    if environment.status not in BILLABLE_STATUSES:
        return Decimal("0.0000"), "idle"
    if usage is None or (usage.cpu_cores <= 0 and usage.memory_gib <= 0):
        return environment.cost_estimate_hourly, "estimate"
    rate = burn_rate_hourly(usage, environment=environment, settings=settings)
    return rate, usage.source


def accrue_environment_cost(
    environment: Environment,
    *,
    settings: Settings,
    usage: NamespaceUsage | None,
    now: datetime | None = None,
) -> Decimal:
    """Increment ``cost_accrued`` for the interval since the last sample.

    Returns the burn rate applied ($/hour) for observability.
    """
    ts = now or datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    baseline = environment.cost_sampled_at or environment.created_at
    if baseline.tzinfo is None:
        baseline = baseline.replace(tzinfo=UTC)

    delta_hours = max((ts - baseline).total_seconds() / 3600.0, 0.0)
    rate, source = resolve_burn_rate(
        environment=environment,
        settings=settings,
        usage=usage,
    )
    increment = (rate * Decimal(str(round(delta_hours, 6)))).quantize(Decimal("0.0001"))
    previous = environment.cost_accrued or Decimal("0.0000")
    environment.cost_accrued = (previous + increment).quantize(Decimal("0.0001"))
    environment.cost_sampled_at = ts
    environment.cost_source = source
    # Keep the displayed hourly estimate aligned with the latest measured burn
    # when we have real usage - soft-cap UX and cards stay coherent.
    if source in {"usage_quota", "usage_requests"} and rate > 0:
        environment.cost_estimate_hourly = rate
    return rate


def convert_display_cost(amount_usd: Decimal, *, settings: Settings) -> Decimal:
    """Convert stored USD amounts for UI when ``cost_display_currency`` is EUR."""
    base = amount_usd or Decimal("0.0000")
    currency = (settings.cost_display_currency or "USD").strip().upper()
    if currency == "EUR":
        rate = settings.cost_usd_to_eur_rate or Decimal("0.92")
        return (base * rate).quantize(Decimal("0.0001"))
    return base.quantize(Decimal("0.0001"))


def display_cost_accrued(
    *,
    cost_accrued: Decimal,
    cost_estimate_hourly: Decimal,
    cost_sampled_at: datetime | None,
    created_at: datetime,
    status: EnvironmentStatus,
    now: datetime | None = None,
) -> Decimal:
    """Return accrued cost for API responses, with provisional tick since last sample."""
    ts = now or datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    if cost_sampled_at is not None:
        sampled = cost_sampled_at
        if sampled.tzinfo is None:
            sampled = sampled.replace(tzinfo=UTC)
        base = cost_accrued or Decimal("0.0000")
        if status in BILLABLE_STATUSES:
            hours = max((ts - sampled).total_seconds() / 3600.0, 0.0)
            provisional = (
                cost_estimate_hourly * Decimal(str(round(hours, 4)))
            ).quantize(Decimal("0.0001"))
            return (base + provisional).quantize(Decimal("0.0001"))
        return base.quantize(Decimal("0.0001"))

    created = created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    elapsed_hours = max((ts - created).total_seconds() / 3600.0, 0.0)
    return (
        cost_estimate_hourly * Decimal(str(round(elapsed_hours, 4)))
    ).quantize(Decimal("0.0001"))

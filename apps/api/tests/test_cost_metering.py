"""Unit tests for usage-based cost metering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.core.config import Settings
from app.models.domain import Environment, EnvironmentStatus
from app.services.cost_metering import (
    NamespaceUsage,
    accrue_environment_cost,
    burn_rate_hourly,
    display_cost_accrued,
    parse_cpu_cores,
    parse_memory_gib,
)


def test_parse_cpu_and_memory_quantities() -> None:
    assert parse_cpu_cores("100m") == Decimal("0.1000")
    assert parse_cpu_cores("2") == Decimal("2.0000")
    assert parse_memory_gib("512Mi") == Decimal("0.5000")
    assert parse_memory_gib("1Gi") == Decimal("1.0000")


def test_burn_rate_includes_datastore_addons() -> None:
    settings = Settings(
        cost_rate_cpu_core_hour=Decimal("1.0000"),
        cost_rate_memory_gib_hour=Decimal("2.0000"),
        cost_rate_postgres_hour=Decimal("0.0800"),
        cost_rate_redis_hour=Decimal("0.0400"),
    )
    env = Environment(
        id=uuid4(),
        owner_id=uuid4(),
        name="cost-demo",
        git_branch="main",
        git_repo_url="https://github.com/acme/app.git",
        namespace_name="ns-cost",
        status=EnvironmentStatus.RUNNING,
        ttl_expires_at=datetime.now(UTC) + timedelta(hours=1),
        cost_estimate_hourly=Decimal("0.4200"),
        enable_postgres=True,
        enable_redis=True,
    )
    usage = NamespaceUsage(
        cpu_cores=Decimal("0.5"),
        memory_gib=Decimal("1.0"),
        source="usage_requests",
    )
    rate = burn_rate_hourly(usage, environment=env, settings=settings)
    # 0.5*1 + 1*2 + 0.08 + 0.04 = 2.62
    assert rate == Decimal("2.6200")


def test_accrue_uses_usage_over_estimate() -> None:
    settings = Settings(
        cost_rate_cpu_core_hour=Decimal("1.0000"),
        cost_rate_memory_gib_hour=Decimal("0"),
        cost_rate_postgres_hour=Decimal("0"),
        cost_rate_redis_hour=Decimal("0"),
    )
    created = datetime.now(UTC) - timedelta(hours=2)
    env = Environment(
        id=uuid4(),
        owner_id=uuid4(),
        name="accrue-demo",
        git_branch="main",
        git_repo_url="https://github.com/acme/app.git",
        namespace_name="ns-accrue",
        status=EnvironmentStatus.RUNNING,
        ttl_expires_at=datetime.now(UTC) + timedelta(hours=1),
        cost_estimate_hourly=Decimal("9.0000"),
        cost_accrued=Decimal("0.0000"),
        created_at=created,
        enable_postgres=False,
        enable_redis=False,
    )
    usage = NamespaceUsage(
        cpu_cores=Decimal("1.0"),
        memory_gib=Decimal("0"),
        source="usage_quota",
    )
    now = created + timedelta(hours=2)
    rate = accrue_environment_cost(env, settings=settings, usage=usage, now=now)
    assert rate == Decimal("1.0000")
    assert env.cost_accrued == Decimal("2.0000")
    assert env.cost_source == "usage_quota"
    assert env.cost_estimate_hourly == Decimal("1.0000")


def test_idle_statuses_do_not_accrue() -> None:
    settings = Settings()
    created = datetime.now(UTC) - timedelta(hours=3)
    env = Environment(
        id=uuid4(),
        owner_id=uuid4(),
        name="idle-demo",
        git_branch="main",
        git_repo_url="https://github.com/acme/app.git",
        namespace_name="ns-idle",
        status=EnvironmentStatus.EXPIRED,
        ttl_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        cost_estimate_hourly=Decimal("1.0000"),
        cost_accrued=Decimal("1.5000"),
        cost_sampled_at=created + timedelta(hours=1),
        created_at=created,
    )
    rate = accrue_environment_cost(env, settings=settings, usage=None, now=datetime.now(UTC))
    assert rate == Decimal("0.0000")
    assert env.cost_accrued == Decimal("1.5000")
    assert env.cost_source == "idle"


def test_display_cost_provisional_tick() -> None:
    sampled = datetime.now(UTC) - timedelta(hours=1)
    display = display_cost_accrued(
        cost_accrued=Decimal("1.0000"),
        cost_estimate_hourly=Decimal("0.5000"),
        cost_sampled_at=sampled,
        created_at=sampled - timedelta(hours=2),
        status=EnvironmentStatus.RUNNING,
        now=sampled + timedelta(hours=1),
    )
    assert display == Decimal("1.5000")

"""Preview drift detection — comparator, audit resolution, and periodic scan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db_session
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.domain import AuditAction, AuditStatus, Base, EnvironmentStatus, User
from app.repositories.environment import EnvironmentRepository
from app.services.audit import AuditService
from app.services.drift_scanner import (
    DRIFT_SCANNER_ACTOR,
    DriftFinding,
    inspect_live_deployment,
    scan_environment,
)
from app.workers.tasks import _run_drift_scan


def _deployment(
    *,
    image: str,
    commit_label: str | None = None,
    commit_env: str | None = None,
) -> SimpleNamespace:
    env_vars: list[SimpleNamespace] = []
    if commit_env is not None:
        env_vars.append(SimpleNamespace(name="GIT_COMMIT_SHA", value=commit_env))
    labels: dict[str, str] = {}
    if commit_label is not None:
        labels["launchpad.io/git-commit"] = commit_label
    return SimpleNamespace(
        metadata=SimpleNamespace(labels=labels, annotations={}),
        spec=SimpleNamespace(
            template=SimpleNamespace(
                spec=SimpleNamespace(containers=[SimpleNamespace(image=image, env=env_vars)]),
            ),
        ),
    )


def test_inspect_live_deployment_in_sync() -> None:
    deployment = _deployment(
        image="launchpad/demo:abc",
        commit_label="deadbeef",
    )
    assert (
        inspect_live_deployment(
            deployment,
            expected_image="launchpad/demo:abc",
            expected_commit="deadbeef",
        )
        == []
    )


def test_inspect_live_deployment_image_mismatch() -> None:
    deployment = _deployment(image="launchpad/demo:old", commit_label="deadbeef")
    mismatches = inspect_live_deployment(
        deployment,
        expected_image="launchpad/demo:new",
        expected_commit="deadbeef",
    )
    assert len(mismatches) == 1
    assert "image expected=" in mismatches[0]


def test_inspect_live_deployment_commit_from_env() -> None:
    deployment = _deployment(image="img:1", commit_env="cafebabe")
    mismatches = inspect_live_deployment(
        deployment,
        expected_image="img:1",
        expected_commit="deadbeef",
    )
    assert len(mismatches) == 1
    assert "commit expected=deadbeef" in mismatches[0]


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def test_user(session_factory: async_sessionmaker[AsyncSession]) -> User:
    async with session_factory() as session:
        user = User(
            email="drift@example.com",
            password_hash=hash_password("password123"),
            display_name="Drift Owner",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.mark.asyncio
async def test_has_unresolved_drift_cleared_after_rebuild(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="drift-env",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-drift",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0"),
            workload_image="launchpad/demo:1",
            latest_commit_sha="abc123",
        )
        await repo.update_status(environment, EnvironmentStatus.RUNNING)
        audits = AuditService(session)
        await audits.record(
            action=AuditAction.DRIFT_DETECTED,
            actor_id=DRIFT_SCANNER_ACTOR,
            status=AuditStatus.SUCCESS,
            environment_id=environment.id,
            detail="image expected=launchpad/demo:1 live=launchpad/demo:2",
        )
        await session.commit()
        env_id = environment.id

    async with session_factory() as session:
        audits = AuditService(session)
        assert await audits.has_unresolved_drift(env_id) is True

    async with session_factory() as session:
        audits = AuditService(session)
        await audits.record(
            action=AuditAction.REBUILD_SUCCEEDED,
            actor_id=str(test_user.id),
            status=AuditStatus.SUCCESS,
            environment_id=env_id,
            commit_sha="abc123",
        )
        await session.commit()

    async with session_factory() as session:
        audits = AuditService(session)
        assert await audits.has_unresolved_drift(env_id) is False


@pytest.mark.asyncio
async def test_run_drift_scan_records_audit_once() -> None:
    env_id = uuid4()
    finding = DriftFinding(
        environment_id=str(env_id),
        namespace="launchpad-env-x",
        mismatches=("image expected=a live=b",),
    )
    environment = MagicMock()
    environment.id = env_id
    environment.workspace_id = None
    environment.latest_commit_sha = "sha1"

    session = AsyncMock()
    session.commit = AsyncMock()

    env_repo = MagicMock()
    env_repo.list_running = AsyncMock(return_value=[environment])

    audit = MagicMock()

    with (
        patch("app.workers.tasks.get_settings") as settings_mock,
        patch("app.workers.tasks._session_factory") as factory_mock,
        patch("app.workers.tasks.EnvironmentRepository", return_value=env_repo),
        patch("app.workers.tasks.AuditService", return_value=audit),
        patch("app.workers.tasks.KubernetesProvisioner"),
        patch("app.workers.tasks.scan_environment", return_value=finding),
        patch(
            "app.workers.tasks.record_drift_if_changed",
            new_callable=AsyncMock,
            return_value=True,
        ) as record_mock,
    ):
        settings = settings_mock.return_value
        settings.drift_scan_enabled = True
        settings.kubernetes_enabled = True
        settings.default_workload_image = "launchpad/default:latest"
        factory_mock.return_value.return_value.__aenter__ = AsyncMock(return_value=session)
        factory_mock.return_value.return_value.__aexit__ = AsyncMock(return_value=False)

        recorded = await _run_drift_scan()

    assert recorded == 1
    record_mock.assert_awaited_once()
    kwargs = record_mock.await_args.kwargs
    assert kwargs["environment"] is environment
    assert kwargs["finding"] is finding
    assert kwargs["actor_id"] == DRIFT_SCANNER_ACTOR


@pytest.mark.asyncio
async def test_run_drift_scan_skips_duplicate_detail() -> None:
    env_id = uuid4()
    finding = DriftFinding(
        environment_id=str(env_id),
        namespace="launchpad-env-x",
        mismatches=("image expected=a live=b",),
    )
    environment = MagicMock()
    environment.id = env_id
    environment.workspace_id = None
    environment.latest_commit_sha = None

    session = AsyncMock()
    session.commit = AsyncMock()
    env_repo = MagicMock()
    env_repo.list_running = AsyncMock(return_value=[environment])
    audit = MagicMock()

    with (
        patch("app.workers.tasks.get_settings") as settings_mock,
        patch("app.workers.tasks._session_factory") as factory_mock,
        patch("app.workers.tasks.EnvironmentRepository", return_value=env_repo),
        patch("app.workers.tasks.AuditService", return_value=audit),
        patch("app.workers.tasks.KubernetesProvisioner"),
        patch("app.workers.tasks.scan_environment", return_value=finding),
        patch(
            "app.workers.tasks.record_drift_if_changed",
            new_callable=AsyncMock,
            return_value=False,
        ) as record_mock,
    ):
        settings = settings_mock.return_value
        settings.drift_scan_enabled = True
        settings.kubernetes_enabled = True
        settings.default_workload_image = "launchpad/default:latest"
        factory_mock.return_value.return_value.__aenter__ = AsyncMock(return_value=session)
        factory_mock.return_value.return_value.__aexit__ = AsyncMock(return_value=False)

        recorded = await _run_drift_scan()

    assert recorded == 0
    record_mock.assert_awaited_once()


def test_scan_environment_skips_when_kubernetes_disabled() -> None:
    provisioner = MagicMock()
    provisioner._settings.kubernetes_enabled = False
    environment = MagicMock()
    assert scan_environment(provisioner, environment, default_image="img:1") is None


@pytest_asyncio.fixture
async def client(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_environment_read_includes_drift_fields(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    from app.core.config import get_settings

    get_settings().kubernetes_enabled = True

    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="drift-read",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-read",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0"),
        )
        await repo.update_status(environment, EnvironmentStatus.RUNNING)
        audits = AuditService(session)
        await audits.record(
            action=AuditAction.DRIFT_DETECTED,
            actor_id=DRIFT_SCANNER_ACTOR,
            status=AuditStatus.SUCCESS,
            environment_id=environment.id,
            detail="image expected=x live=y",
        )
        await session.commit()
        env_id = environment.id

    response = await client.get(
        f"/api/v1/environments/{env_id}",
        headers=auth_header(test_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["drift_detected"] is True
    assert body["drift_summary"] == "image expected=x live=y"


@pytest.mark.asyncio
async def test_manual_drift_scan_endpoint(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    from app.core.config import get_settings
    from app.services.drift_scanner import DriftFinding

    get_settings().kubernetes_enabled = True

    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="drift-manual",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-manual",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0"),
            workload_image="launchpad/demo:expected",
        )
        await repo.update_status(environment, EnvironmentStatus.RUNNING)
        await session.commit()
        env_id = environment.id

    finding = DriftFinding(
        environment_id=str(env_id),
        namespace="launchpad-env-manual",
        mismatches=("image expected=launchpad/demo:expected live=launchpad/demo:live",),
    )

    with patch(
        "app.services.environment.scan_environment",
        return_value=finding,
    ), patch("app.services.environment.KubernetesProvisioner"):
        response = await client.post(
            f"/api/v1/environments/{env_id}/drift-scan",
            headers=auth_header(test_user),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["drift_detected"] is True
    assert "live=launchpad/demo:live" in (body["drift_summary"] or "")

    audits = await client.get(
        f"/api/v1/environments/{env_id}/audits",
        headers=auth_header(test_user),
    )
    assert audits.status_code == 200
    actions = [row["action"] for row in audits.json()]
    assert "DRIFT_DETECTED" in actions


@pytest.mark.asyncio
async def test_manual_drift_scan_rejects_non_running(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    from app.core.config import get_settings

    get_settings().kubernetes_enabled = True

    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="drift-pending",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-pending",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0"),
        )
        await session.commit()
        env_id = environment.id

    response = await client.post(
        f"/api/v1/environments/{env_id}/drift-scan",
        headers=auth_header(test_user),
    )
    assert response.status_code == 409

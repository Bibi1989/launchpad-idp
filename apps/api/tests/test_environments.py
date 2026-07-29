from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db_session
from app.core.logging import sanitize_log_message
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.domain import Base, EnvironmentStatus, LogLevel, User
from app.repositories.environment import DeploymentLogRepository, EnvironmentRepository
from app.schemas.environment import EnvironmentCreate
from app.services.environment import EnvironmentService
from app.services.kubernetes import KubernetesProvisioner
from app.workers.tasks import _run_provision, _run_ttl_reaper


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
            email="owner@example.com",
            password_hash=hash_password("password123"),
            display_name="Owner",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def other_user(session_factory: async_sessionmaker[AsyncSession]) -> User:
    async with session_factory() as session:
        user = User(
            email="other@example.com",
            password_hash=hash_password("password123"),
            display_name="Other",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


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


def test_sanitize_log_message_redacts_secrets() -> None:
    raw = "Connecting with password=supersecret token: abc123"
    cleaned = sanitize_log_message(raw)
    assert "supersecret" not in cleaned
    assert "abc123" not in cleaned
    assert "[REDACTED]" in cleaned


@pytest.mark.asyncio
async def test_enqueue_provision_creates_environment(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        service = EnvironmentService(session)
        with patch("app.services.environment.enqueue_provision_environment") as enqueue:
            enqueue.return_value = "celery-task-id"
            result = await service.enqueue_provision(
                EnvironmentCreate(
                    name="demo-env",
                    git_branch="feature/demo",
                    git_repo_url="https://github.com/acme/demo.git",
                    ttl_hours=24,
                ),
                owner=test_user,
                correlation_id="corr-1",
            )
            enqueue.assert_called_once()

        assert result.name == "demo-env"
        assert result.git_branch == "feature/demo"
        assert result.git_repo_url == "https://github.com/acme/demo.git"
        assert result.owner_id == test_user.id
        assert result.namespace_name == f"launchpad-env-{result.id}"
        assert result.status == EnvironmentStatus.PROVISIONING
        assert result.cost_estimate_hourly == Decimal("0.4200")


@pytest.mark.asyncio
async def test_create_environment_endpoint_returns_202(
    client: AsyncClient,
    test_user: User,
) -> None:
    with patch("app.services.environment.enqueue_provision_environment") as enqueue:
        enqueue.return_value = "celery-task-id"
        response = await client.post(
            "/api/v1/environments",
            headers=auth_header(test_user),
            json={
                "name": "staging-01",
                "git_branch": "main",
                "git_repo_url": "https://github.com/acme/app.git",
                "ttl_hours": 48,
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["name"] == "staging-01"
    assert body["git_branch"] == "main"
    assert body["git_repo_url"] == "https://github.com/acme/app.git"
    assert body["status"] == "PROVISIONING"
    assert "X-Correlation-ID" in response.headers


@pytest.mark.asyncio
async def test_create_environment_requires_auth(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/environments",
        json={
            "name": "staging-01",
            "git_branch": "main",
            "git_repo_url": "https://github.com/acme/app.git",
            "ttl_hours": 48,
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_owner_isolation_returns_404(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    other_user: User,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="owned-env",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-owned",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await session.commit()
        env_id = environment.id

    response = await client.get(
        f"/api/v1/environments/{env_id}",
        headers=auth_header(other_user),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_environment_returns_409(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        await repo.create(
            owner_id=test_user.id,
            name="dup",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-dup",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await session.commit()

        service = EnvironmentService(session)
        with patch("app.services.environment.enqueue_provision_environment"):
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc:
                await service.enqueue_provision(
                    EnvironmentCreate(
                        name="dup",
                        git_branch="main",
                        git_repo_url="https://github.com/acme/app.git",
                        ttl_hours=12,
                    ),
                    owner=test_user,
                    correlation_id=str(uuid4()),
                )
            assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_provision_task_marks_running_and_emits_logs(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVISION_STEP_DELAY_SECONDS", "0")
    monkeypatch.setenv("KUBERNETES_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()

    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="worker-env",
            git_branch="develop",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-worker",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=2),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await session.commit()
        env_id = str(environment.id)

    monkeypatch.setattr(
        "app.workers.tasks._session_factory",
        lambda: session_factory,
    )

    await _run_provision(env_id, "corr-worker")

    async with session_factory() as session:
        env_repo = EnvironmentRepository(session)
        log_repo = DeploymentLogRepository(session)
        environment = await env_repo.get_by_id(UUID(env_id))
        assert environment is not None
        assert environment.status == EnvironmentStatus.RUNNING
        logs = await log_repo.list_for_environment(environment.id)
        assert len(logs) >= 3
        assert any("simulate mode" in log.message.lower() for log in logs)
        assert any("RUNNING" in log.message for log in logs)
        assert not any("Cloning repository" in log.message for log in logs)
        assert any(log.log_level == LogLevel.INFO for log in logs)

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ttl_reaper_queues_teardown(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="expired-env",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-expired",
            ttl_expires_at=datetime.now(UTC) - timedelta(minutes=1),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(environment, EnvironmentStatus.RUNNING)
        await session.commit()
        env_id = environment.id

    monkeypatch.setattr("app.workers.tasks._session_factory", lambda: session_factory)

    with patch("app.workers.tasks.enqueue_teardown_environment") as enqueue:
        reaped = await _run_ttl_reaper()
        enqueue.assert_called_once()
        assert reaped == 1

    async with session_factory() as session:
        env_repo = EnvironmentRepository(session)
        environment = await env_repo.get_by_id(env_id)
        assert environment is not None
        assert environment.status == EnvironmentStatus.TEARDOWN_PENDING


def test_kubernetes_simulate_mode_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUBERNETES_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    provisioner = KubernetesProvisioner()
    resources = provisioner.provision(
        namespace="launchpad-env-test",
        environment_id=str(uuid4()),
        name="test",
        git_branch="main",
        git_repo_url="https://github.com/acme/app.git",
        ttl_expires_at=datetime.now(UTC).isoformat(),
        owner_label="owner@example.com",
    )
    assert resources.created_namespace is True
    assert resources.created_workload is True
    assert resources.simulated is True
    assert resources.preview_url is not None
    assert "/p/" in resources.preview_url
    provisioner.rollback(resources)
    get_settings.cache_clear()


def test_allocate_node_port_is_stable_and_in_range() -> None:
    from app.services.kubernetes import allocate_node_port

    env_id = "11111111-2222-3333-4444-555555555555"
    port_a = allocate_node_port(env_id, port_min=30080, port_max=30084)
    port_b = allocate_node_port(env_id, port_min=30080, port_max=30084)
    assert port_a == port_b
    assert 30080 <= port_a <= 30084
    other = allocate_node_port("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", port_min=30080, port_max=30084)
    assert 30080 <= other <= 30084


def test_allocate_node_port_skips_used_ports() -> None:
    from app.services.kubernetes import allocate_node_port

    env_id = "11111111-2222-3333-4444-555555555555"
    preferred = allocate_node_port(env_id, port_min=30080, port_max=30084)
    next_port = allocate_node_port(
        env_id,
        port_min=30080,
        port_max=30084,
        used_ports={preferred},
    )
    assert next_port != preferred
    assert 30080 <= next_port <= 30084


def test_allocate_node_port_raises_when_range_exhausted() -> None:
    from app.services.kubernetes import allocate_node_port

    with pytest.raises(RuntimeError, match="No free NodePort"):
        allocate_node_port(
            "any-id",
            port_min=30080,
            port_max=30082,
            used_ports={30080, 30081, 30082},
        )


def test_resolve_preview_node_port_keeps_in_range_sticky() -> None:
    from app.services.kubernetes import resolve_preview_node_port

    assert (
        resolve_preview_node_port(
            "env-1",
            existing_port=30081,
            port_min=30080,
            port_max=30084,
        )
        == 30081
    )


def test_resolve_preview_node_port_reallocates_out_of_range() -> None:
    from app.services.kubernetes import resolve_preview_node_port

    port = resolve_preview_node_port(
        "11111111-2222-3333-4444-555555555555",
        existing_port=31196,
        port_min=30080,
        port_max=30084,
    )
    assert 30080 <= port <= 30084
    assert port != 31196

@pytest.mark.asyncio
async def test_retry_failed_environment_requeues_provision(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="retry-me",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-retry",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(
            environment,
            EnvironmentStatus.FAILED,
            error_message="Provision failed — rolling back: timeout",
        )
        await session.commit()
        env_id = environment.id

        service = EnvironmentService(session)
        with patch("app.services.environment.enqueue_provision_environment") as enqueue:
            with patch("app.services.environment.publish_env_event", new=AsyncMock()):
                result = await service.retry_provision(
                    env_id,
                    owner=test_user,
                    correlation_id="corr-retry",
                )
            enqueue.assert_called_once_with(
                environment_id=str(env_id),
                correlation_id="corr-retry",
            )

        assert result.status == EnvironmentStatus.PROVISIONING.value or result.status == EnvironmentStatus.PROVISIONING
        await session.refresh(environment)
        assert environment.status == EnvironmentStatus.PROVISIONING
        assert environment.error_message is None


@pytest.mark.asyncio
async def test_retry_rejects_provisioning(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    from fastapi import HTTPException

    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="running-env",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-running",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(environment, EnvironmentStatus.PROVISIONING)
        await session.commit()

        service = EnvironmentService(session)
        with pytest.raises(HTTPException) as exc:
            await service.retry_provision(
                environment.id,
                owner=test_user,
                correlation_id="corr-retry",
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_retry_allows_running_environment(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="running-retry",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-running-retry",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(environment, EnvironmentStatus.RUNNING)
        await session.commit()
        env_id = environment.id

        service = EnvironmentService(session)
        with patch("app.services.environment.enqueue_provision_environment") as enqueue:
            with patch("app.services.environment.publish_env_event", new=AsyncMock()):
                result = await service.retry_provision(
                    env_id,
                    owner=test_user,
                    correlation_id="corr-running-retry",
                )
            enqueue.assert_called_once()

        assert result.status in {
            EnvironmentStatus.PROVISIONING,
            EnvironmentStatus.PROVISIONING.value,
        }
        await session.refresh(environment)
        assert environment.status == EnvironmentStatus.PROVISIONING


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_pause_and_resume_environment(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="pause-demo",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-pause-demo",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(environment, EnvironmentStatus.RUNNING)
        await session.commit()
        env_id = environment.id

        service = EnvironmentService(session)
        with patch("app.services.environment.KubernetesProvisioner") as prov_cls:
            prov = prov_cls.return_value
            paused = await service.pause_environment(
                env_id,
                owner=test_user,
                correlation_id="corr-pause",
            )
            prov.scale_deployment.assert_called_with(namespace="launchpad-env-pause-demo", replicas=0)
            assert paused.status == EnvironmentStatus.PAUSED

            resumed = await service.resume_environment(
                env_id,
                owner=test_user,
                correlation_id="corr-resume",
            )
            prov.scale_deployment.assert_called_with(namespace="launchpad-env-pause-demo", replicas=1)
            assert resumed.status == EnvironmentStatus.RUNNING


@pytest.mark.asyncio
async def test_auto_pause_oldest_when_exceeding_max_concurrent_limit(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        created_envs = []
        for i in range(4):
            env = await repo.create(
                owner_id=test_user.id,
                name=f"app-env-{i+1}",
                git_branch="main",
                git_repo_url="https://github.com/acme/app.git",
                namespace_name=f"launchpad-env-{i+1}",
                ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
                cost_estimate_hourly=Decimal("0.10"),
            )
            await repo.update_status(env, EnvironmentStatus.RUNNING)
            created_envs.append(env)
        await session.commit()

        service = EnvironmentService(session)
        with patch("app.services.environment.enqueue_provision_environment"):
            with patch("app.services.environment.KubernetesProvisioner"):
                new_env = await service.enqueue_provision(
                    EnvironmentCreate(
                        name="app-env-5",
                        git_branch="main",
                        git_repo_url="https://github.com/acme/app.git",
                        ttl_hours=24,
                    ),
                    owner=test_user,
                    correlation_id="corr-auto-pause",
                )

        oldest = created_envs[0]
        await session.refresh(oldest)
        assert oldest.status == EnvironmentStatus.PAUSED
        assert new_env.name == "app-env-5"


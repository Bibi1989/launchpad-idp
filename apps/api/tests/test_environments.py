from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
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
            now = datetime.now(UTC)
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
        # TTL starts counting only once the environment is RUNNING (successful
        # provision), so at creation (PROVISIONING) ttl_expires_at is not yet set.
        assert result.ttl_expires_at is None


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

    with patch("app.workers.tasks._maybe_build_preview_image", return_value=(None, None)):
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

    with (
        patch("app.workers.tasks.KubernetesProvisioner") as mock_prov_cls,
        patch("app.workers.tasks._reclaim_environment_runtime", new_callable=AsyncMock) as mock_reclaim,
    ):
        mock_prov_cls.return_value = MagicMock()
        mock_reclaim.return_value = "kubernetes namespace removed; removed 1 image(s)"
        reaped = await _run_ttl_reaper()
        mock_reclaim.assert_called_once()
        assert reaped == 1

    async with session_factory() as session:
        env_repo = EnvironmentRepository(session)
        environment = await env_repo.get_by_id(env_id)
        assert environment is not None
        assert environment.status == EnvironmentStatus.EXPIRED


def test_kubernetes_rollback_keeps_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """First-preview Ready failure must not delete the namespace (Retry/Destroy stay usable)."""
    monkeypatch.setenv("KUBERNETES_ENABLED", "true")
    from app.core.config import get_settings
    from app.services.kubernetes import ProvisionedResources

    get_settings.cache_clear()
    provisioner = KubernetesProvisioner()
    resources = ProvisionedResources(
        namespace="launchpad-env-first-fail",
        created_namespace=True,
        created_workload=True,
        image="nginx:1.27-alpine",
        node_port=30080,
        preview_url="http://127.0.0.1:30080",
    )
    with patch.object(provisioner, "teardown") as teardown:
        provisioner.rollback(resources)
        teardown.assert_not_called()
    get_settings.cache_clear()


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
            error_message="Provision failed: timeout",
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
                regenerate_dockerfile=False,
            )

        assert result.status == EnvironmentStatus.PROVISIONING.value or result.status == EnvironmentStatus.PROVISIONING
        await session.refresh(environment)
        assert environment.status == EnvironmentStatus.PROVISIONING
        assert environment.error_message is None


@pytest.mark.asyncio
async def test_manifest_launch_extracts_image_despite_default_nginx(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
    tmp_path,
) -> None:
    """A workspace launch that sends the default nginx image must still resolve the
    real workload image from the manifests (regression: nginx shown/used even
    though the workspace deploys web:latest)."""
    from app.models.domain import ProvisioningWorkspace
    from app.schemas.environment import EnvironmentCreate
    from app.services.k8s_bundle import additional_workload_manifests
    from app.services.orgs import OrganizationService

    root = tmp_path / "shop-ws"
    (root / "infra" / "k8s" / "manifests").mkdir(parents=True)
    additional_workload_manifests(
        root,
        env_name="shop",
        services=[
            {"name": "launch-web", "image": "web:latest", "port": 8080,
             "service_type": "ClusterIP", "selector": "web", "expose_preview": True},
            {"name": "launch-server", "image": "server:latest", "port": 8000,
             "service_type": "ClusterIP", "selector": "server", "expose_preview": False},
        ],
    )

    async with session_factory() as session:
        personal = await OrganizationService(session).ensure_personal_org(test_user)
        ws_id = uuid4()
        session.add(
            ProvisioningWorkspace(
                id=ws_id,
                owner_id=test_user.id,
                org_id=personal.id,
                name="shop",
                engine="terraform",
                provider="local",
                root_dir=str(root),
                status="ready",
            )
        )
        await session.commit()

        service = EnvironmentService(session)
        payload = EnvironmentCreate(
            name="shop-preview",
            git_branch="main",
            git_repo_url=f"https://launchpad.local/workspaces/{ws_id}",
            ttl_hours=1,
            workspace_id=ws_id,
            provider="local",
            # The launch form always sends the default placeholder - must NOT win.
            workload_image="nginx:1.27-alpine",
        )
        with patch("app.services.environment.enqueue_provision_environment"):
            with patch("app.services.environment.publish_env_event", new=AsyncMock()):
                result = await service.enqueue_provision(
                    payload, owner=test_user, correlation_id="corr-img"
                )

        environment = await EnvironmentRepository(session).get_by_id(result.id)
        # The exposed preview-target (web) image wins over both the client default
        # nginx and the alphabetically-first backend.
        assert environment.workload_image == "web:latest"


@pytest.mark.asyncio
async def test_teardown_rejects_provisioning_without_force(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    from fastapi import HTTPException

    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="provisioning-env",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-provisioning-1",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(environment, EnvironmentStatus.PROVISIONING)
        await session.commit()

        service = EnvironmentService(session)
        with pytest.raises(HTTPException) as exc:
            await service.request_teardown(
                environment.id, owner=test_user, correlation_id="corr-td"
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "environment_still_provisioning"


@pytest.mark.asyncio
async def test_cancel_provision_stops_without_teardown(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="provisioning-env-stop",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-provisioning-stop",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(environment, EnvironmentStatus.PROVISIONING)
        await session.commit()
        env_id = environment.id

        service = EnvironmentService(session)
        with (
            patch("app.services.environment.enqueue_teardown_environment") as enqueue,
            patch("app.services.environment.force_release_state_lock", new=AsyncMock(return_value=False)),
        ):
            result = await service.cancel_provision(
                env_id, owner=test_user, correlation_id="corr-stop"
            )
            enqueue.assert_not_called()

        await session.refresh(environment)
        assert environment.status == EnvironmentStatus.FAILED
        assert environment.error_message == "Provisioning stopped by user"
        assert result.status in (EnvironmentStatus.FAILED, EnvironmentStatus.FAILED.value)


@pytest.mark.asyncio
async def test_cancel_provision_rejects_non_provisioning(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    from fastapi import HTTPException

    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="running-env-stop",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-running-stop",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(environment, EnvironmentStatus.RUNNING)
        await session.commit()

        service = EnvironmentService(session)
        with pytest.raises(HTTPException) as exc:
            await service.cancel_provision(
                environment.id, owner=test_user, correlation_id="corr-stop-bad"
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "cancel_not_allowed"


@pytest.mark.asyncio
async def test_destroy_after_cancel_provision_with_stale_lock(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="provisioning-env-stop-destroy",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-stop-destroy",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(environment, EnvironmentStatus.PROVISIONING)
        await session.commit()
        env_id = environment.id

        service = EnvironmentService(session)
        with patch("app.services.environment.force_release_state_lock", new=AsyncMock(return_value=True)):
            await service.cancel_provision(
                env_id, owner=test_user, correlation_id="corr-stop-destroy"
            )

        await session.refresh(environment)
        assert environment.status == EnvironmentStatus.FAILED

        with (
            patch("app.services.environment.is_state_locked", new=AsyncMock(return_value=True)),
            patch("app.services.environment.enqueue_teardown_environment") as enqueue,
        ):
            result = await service.request_teardown(
                env_id, owner=test_user, correlation_id="corr-destroy-after-stop"
            )
            enqueue.assert_called_once()

        assert result.status in (
            EnvironmentStatus.TEARDOWN_PENDING,
            EnvironmentStatus.TEARDOWN_PENDING.value,
        )


@pytest.mark.asyncio
async def test_force_teardown_cancels_provisioning(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="provisioning-env-force",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-provisioning-2",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(environment, EnvironmentStatus.PROVISIONING)
        await session.commit()
        env_id = environment.id

        service = EnvironmentService(session)
        with patch("app.services.environment.enqueue_teardown_environment") as enqueue:
            result = await service.request_teardown(
                env_id, owner=test_user, correlation_id="corr-force", force=True
            )
            enqueue.assert_called_once_with(
                environment_id=str(env_id), correlation_id="corr-force"
            )

        # Status flips to TEARDOWN_PENDING - the provision task observes this at
        # its next checkpoint and aborts (cooperative cancellation signal).
        await session.refresh(environment)
        assert environment.status == EnvironmentStatus.TEARDOWN_PENDING
        assert result.status in (
            EnvironmentStatus.TEARDOWN_PENDING,
            EnvironmentStatus.TEARDOWN_PENDING.value,
        )


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
async def test_resume_blocked_when_ttl_expired(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    from fastapi import HTTPException

    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            name="expired-resume",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-expired-resume",
            ttl_expires_at=datetime.now(UTC) - timedelta(minutes=1),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(environment, EnvironmentStatus.EXPIRED)
        await session.commit()
        env_id = environment.id

        service = EnvironmentService(session)
        with pytest.raises(HTTPException) as exc:
            await service.resume_environment(
                env_id,
                owner=test_user,
                correlation_id="corr-expired",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "ttl_expired"


@pytest.mark.asyncio
async def test_relaunch_environment_allows_when_ttl_expired(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        from app.services.orgs import OrganizationService
        from app.services.projects import ProjectService

        org = await OrganizationService(session).ensure_personal_org(test_user)
        default_project = await ProjectService(session, get_settings()).ensure_default_project(
            org=org,
            actor=test_user,
        )

        repo = EnvironmentRepository(session)
        environment = await repo.create(
            owner_id=test_user.id,
            org_id=org.id,
            project_id=default_project.id,
            name="relaunch-expired",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-relaunch-expired",
            ttl_expires_at=datetime.now(UTC) - timedelta(minutes=1),
            cost_estimate_hourly=Decimal("0.42"),
        )
        await repo.update_status(environment, EnvironmentStatus.EXPIRED)
        await session.commit()

        service = EnvironmentService(
            session,
            settings=get_settings().model_copy(update={"ttl_max_total_hours_from_create": 168}),
        )
        with patch("app.services.environment.enqueue_provision_environment") as enqueue:
            enqueue.return_value = "celery-task-id"
            env_read = await service.relaunch_environment(
                environment.id,
                owner=test_user,
                correlation_id="corr-relaunch",
            )

        assert env_read.status == EnvironmentStatus.PROVISIONING
        assert enqueue.called
        assert env_read.time_remaining_seconds > 160 * 3600
        assert env_read.time_remaining_seconds <= 168 * 3600 + 60


@pytest.mark.asyncio
async def test_create_environment_accepts_ttl_minutes(
    client: AsyncClient,
    test_user: User,
) -> None:
    with patch("app.services.environment.enqueue_provision_environment") as enqueue:
        enqueue.return_value = "celery-task-id"
        response = await client.post(
            "/api/v1/environments",
            headers=auth_header(test_user),
            json={
                "name": "minute-ttl",
                "git_branch": "main",
                "git_repo_url": "https://github.com/acme/app.git",
                "ttl_minutes": 30,
            },
        )
    assert response.status_code == 202
    body = response.json()
    expires = datetime.fromisoformat(body["ttl_expires_at"].replace("Z", "+00:00"))
    created = datetime.fromisoformat(body["created_at"].replace("Z", "+00:00"))
    delta_minutes = (expires - created).total_seconds() / 60
    assert 29 <= delta_minutes <= 31


@pytest.mark.asyncio
async def test_auto_pause_oldest_when_exceeding_max_concurrent_limit(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    async with session_factory() as session:
        # Free tier: cap applies per project, so seed a personal org + default project
        # and create running envs attached to that project.
        from app.services.orgs import OrganizationService
        from app.services.projects import ProjectService

        org = await OrganizationService(session).ensure_personal_org(test_user)
        default_project = await ProjectService(session, get_settings()).ensure_default_project(org=org, actor=test_user)

        limit = get_settings().max_concurrent_environments
        repo = EnvironmentRepository(session)
        created_envs = []
        for i in range(limit):
            env = await repo.create(
                owner_id=test_user.id,
                name=f"app-env-{i+1}",
                git_branch="main",
                git_repo_url="https://github.com/acme/app.git",
                namespace_name=f"launchpad-env-{i+1}",
                ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
                cost_estimate_hourly=Decimal("0.10"),
                project_id=default_project.id,
                org_id=org.id,
            )
            await repo.update_status(env, EnvironmentStatus.RUNNING)
            created_envs.append(env)
        await session.commit()

        service = EnvironmentService(session)
        with patch("app.services.environment.enqueue_provision_environment"):
            with patch("app.services.environment.KubernetesProvisioner"):
                new_env = await service.enqueue_provision(
                    EnvironmentCreate(
                        name=f"app-env-{limit+1}",
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
        assert new_env.name == f"app-env-{limit+1}"


@pytest.mark.asyncio
async def test_extend_ttl_resets_to_full_window(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    """Clicking extend RESETS the TTL to the full window (default_ttl_hours) from now,
    even when it was nearly expired - not just a small append."""
    from app.schemas.environment import EnvironmentExtendRequest

    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        env = await repo.create(
            owner_id=test_user.id,
            name="ttl-reset",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-ttl",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=2),
            cost_estimate_hourly=Decimal("0.10"),
        )
        # Simulate a running env that is about to expire (5 minutes left).
        env.status = EnvironmentStatus.RUNNING
        env.ttl_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        await session.commit()

        service = EnvironmentService(session)
        result = await service.extend_ttl(
            env.id,
            EnvironmentExtendRequest(),
            owner=test_user,
            correlation_id="corr-reset",
        )

        expires = result.ttl_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        remaining_hours = (expires - datetime.now(UTC)).total_seconds() / 3600
        default_hours = get_settings().default_ttl_hours
        # Reset to ~default window (well beyond the 5 minutes it had left).
        assert default_hours - 0.1 <= remaining_hours <= default_hours + 0.1



@pytest.mark.asyncio
async def test_get_environment_restores_latest_stage(
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    """The detail-page read surfaces the last logged execution stage so a reload shows
    the real stage (BUILD/APPLY) instead of resetting the pipeline to INIT."""
    from app.models.domain import ExecutionStage

    async with session_factory() as session:
        repo = EnvironmentRepository(session)
        logs = DeploymentLogRepository(session)
        env = await repo.create(
            owner_id=test_user.id,
            name="stage-env",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            namespace_name="launchpad-env-stage",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=2),
            cost_estimate_hourly=Decimal("0.10"),
        )
        await logs.create(environment_id=env.id, message="init", stage=ExecutionStage.INIT)
        await logs.create(environment_id=env.id, message="building", stage=ExecutionStage.BUILD)
        await session.commit()

        assert await logs.latest_stage_for(env.id) == ExecutionStage.BUILD

        service = EnvironmentService(session)
        read = await service.get_environment(env.id, test_user)
        assert read.stage == ExecutionStage.BUILD

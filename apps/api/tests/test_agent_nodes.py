"""Hybrid agent-node registry: enrollment, registration, HMAC auth, telemetry."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.domain import AgentNodeStatus, Base, Organization, User
from app.schemas.nodes import ContainerSummary, NodeTelemetry
from app.services import agent_install
from app.services.node_registry import NodeRegistryService, sign_agent_payload


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def owner_org(session: AsyncSession) -> tuple[User, Organization]:
    user = User(email="owner@example.com", display_name="Owner")
    org = Organization(slug="acme", name="Acme")
    session.add_all([user, org])
    await session.commit()
    await session.refresh(user)
    await session.refresh(org)
    return user, org


@pytest.mark.asyncio
async def test_enroll_then_register_burns_token(
    session: AsyncSession, owner_org: tuple[User, Organization]
) -> None:
    user, org = owner_org
    svc = NodeRegistryService(session)

    node, token = await svc.create_enrollment(name="homelab-1", owner=user, org_id=org.id, labels={"zone": "garage"})
    assert node.status == AgentNodeStatus.PENDING
    assert token.startswith("lp_")

    reg_node, secret = await svc.register_agent(
        enrollment_token=token,
        hostname="nas",
        platform="linux/amd64",
        agent_version="1.0.0",
        cpu_cores=4,
        mem_total_mb=16000,
    )
    assert reg_node.status == AgentNodeStatus.OFFLINE
    assert reg_node.enrollment_token_hash is None  # single-use burn
    assert secret

    # Reusing a burned token must fail.
    with pytest.raises(ValueError):
        await svc.register_agent(
            enrollment_token=token,
            hostname="x",
            platform="x",
            agent_version="x",
            cpu_cores=1,
            mem_total_mb=1,
        )


@pytest.mark.asyncio
async def test_ws_hmac_authentication(
    session: AsyncSession, owner_org: tuple[User, Organization]
) -> None:
    user, org = owner_org
    svc = NodeRegistryService(session)
    node, token = await svc.create_enrollment(name="edge", owner=user, org_id=org.id, labels={})
    _, secret = await svc.register_agent(
        enrollment_token=token, hostname="h", platform="linux/arm64", agent_version="1.0.0",
        cpu_cores=2, mem_total_mb=2048,
    )

    nid = str(node.id)
    ts = str(int(time.time()))
    nonce = os.urandom(8).hex()
    sig = sign_agent_payload(secret, nid, ts, nonce)

    # Parity with the agent's own computation.
    assert sig == hmac.new(secret.encode(), f"{nid}.{ts}.{nonce}".encode(), hashlib.sha256).hexdigest()

    authed = await svc.authenticate_ws({"node_id": nid, "ts": ts, "nonce": nonce, "sig": sig})
    assert authed is not None and authed.id == node.id

    assert await svc.authenticate_ws({"node_id": nid, "ts": ts, "nonce": nonce, "sig": "bad"}) is None
    stale = str(int(time.time()) - 3600)
    assert await svc.authenticate_ws({"node_id": nid, "ts": stale, "nonce": nonce, "sig": sig}) is None


@pytest.mark.asyncio
async def test_heartbeat_updates_telemetry(
    session: AsyncSession, owner_org: tuple[User, Organization]
) -> None:
    user, org = owner_org
    svc = NodeRegistryService(session)
    node, token = await svc.create_enrollment(name="node-a", owner=user, org_id=org.id, labels={})
    await svc.register_agent(
        enrollment_token=token, hostname="h", platform="linux/amd64", agent_version="1.0.0",
        cpu_cores=4, mem_total_mb=8000,
    )

    await svc.apply_heartbeat(
        node.id,
        NodeTelemetry(
            cpu_percent=12.5,
            mem_percent=40.0,
            disk_percent=55.0,
            docker_status="running",
            cpu_cores=4,
            mem_total_mb=8000,
            containers=[ContainerSummary(id="abc", name="redis", image="redis:7", status="running", ports=["6379->6379"])],
        ),
    )
    reads = await svc.list_nodes(org_id=org.id)
    assert len(reads) == 1
    read = reads[0]
    assert float(read.cpu_percent) == 12.5
    assert len(read.containers) == 1
    # Online is driven by heartbeat freshness (shared DB), not live-socket ownership,
    # so a just-applied heartbeat reports ONLINE even without a hub connection here.
    assert read.online is True
    assert read.status.value == "ONLINE"


@pytest.mark.asyncio
async def test_revoke_disables_auth(
    session: AsyncSession, owner_org: tuple[User, Organization]
) -> None:
    user, org = owner_org
    svc = NodeRegistryService(session)
    node, token = await svc.create_enrollment(name="node-r", owner=user, org_id=org.id, labels={})
    _, secret = await svc.register_agent(
        enrollment_token=token, hostname="h", platform="linux/amd64", agent_version="1.0.0",
        cpu_cores=1, mem_total_mb=1024,
    )
    nid = str(node.id)
    node_uuid = node.id
    await svc.revoke_node(node)

    assert await svc.get_node(node_uuid, org_id=org.id) is None
    assert await svc.list_nodes(org_id=org.id) == []

    ts = str(int(time.time()))
    nonce = os.urandom(8).hex()
    sig = sign_agent_payload(secret, nid, ts, nonce)
    # Row is gone, so authentication must fail.
    assert await svc.authenticate_ws({"node_id": nid, "ts": ts, "nonce": nonce, "sig": sig}) is None


def test_install_script_and_ws_url() -> None:
    script = agent_install.render_install_script()
    assert "TOKEN" in script
    assert "docker run" in script
    ws = agent_install.agent_ws_url()
    assert ws.endswith("/api/v1/ws/nodes/connect")
    assert ws.startswith("ws")


def test_install_script_points_container_at_host() -> None:
    # localhost inside the agent container must be rewritten to reach the host,
    # otherwise registration hits the container itself and the node stays PENDING.
    script = agent_install.render_install_script(request_base_url="http://localhost:8000")
    assert "--add-host=host.docker.internal:host-gateway" in script
    assert "//host.docker.internal" in script  # sed rewrite of localhost/127.0.0.1
    assert "AGENT_LAUNCHPAD_URL" in script


def test_install_script_builds_from_bundle_without_registry() -> None:
    script = agent_install.render_install_script(request_base_url="http://localhost:8000")
    assert "docker image inspect" in script
    assert "/agent/bundle.tar.gz" in script
    assert "docker build -t" in script
    assert agent_install.one_line_install_command("lp_demo").startswith("curl -sSL")

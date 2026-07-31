from __future__ import annotations

import pytest
from httpx import AsyncClient
from uuid import uuid4

from app.services.k8s_manager import get_k8s_manager, PipelineStageEvent


@pytest.mark.asyncio
async def test_k8s_manager_acquire_cluster_context():
    mgr = get_k8s_manager()
    ws_id = str(uuid4())
    ctx = mgr.acquire_cluster_context(ws_id, provider="gcp", cloud_config={"cluster_name": "test-gke", "region": "us-central1-a"})

    assert ctx.workspace_id == ws_id
    assert ctx.provider == "gcp"
    assert ctx.cluster_name == "test-gke"
    assert ctx.region == "us-central1-a"
    assert ctx.status in ("connected", "simulated")
    assert ctx.node_count >= 1
    assert "Healthy" in ctx.control_plane_health


@pytest.mark.asyncio
async def test_k8s_manager_resource_grid_parsing():
    mgr = get_k8s_manager()
    ws_id = str(uuid4())
    manifests = [
        {
            "path": "k8s/deployment.yaml",
            "content": """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web-app
  namespace: prod
spec:
  replicas: 2
---
apiVersion: v1
kind: Service
metadata:
  name: web-app-svc
  namespace: prod
spec:
  ports:
  - port: 80
""",
        }
    ]

    items = mgr.get_resource_grid(ws_id, manifests, namespace="prod")
    kinds = [item.kind for item in items]

    assert "Deployment" in kinds
    assert "Service" in kinds
    assert "Pod" in kinds
    dep_item = next(item for item in items if item.kind == "Deployment")
    assert dep_item.name == "web-app"
    assert dep_item.namespace == "prod"


@pytest.mark.asyncio
async def test_k8s_manager_apply_pipeline_stream():
    mgr = get_k8s_manager()
    ws_id = str(uuid4())
    manifests = [
        {
            "path": "k8s/app.yaml",
            "content": """apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo
spec:
  replicas: 1
  selector:
    matchLabels:
      app: demo
  template:
    metadata:
      labels:
        app: demo
    spec:
      containers:
      - name: demo
        image: nginx:alpine
""",
        }
    ]

    events: list[PipelineStageEvent] = []
    async for event in mgr.execute_apply_pipeline(ws_id, manifests):
        events.append(event)

    stage_ids = [e.stage_id for e in events if e.status == "success"]
    assert "manifest_parsed" in stage_ids
    assert "kube_api_accepted" in stage_ids
    assert "pods_provisioning" in stage_ids
    assert "ingress_ready" in stage_ids


@pytest.mark.asyncio
async def test_k8s_manager_describe_and_delete():
    mgr = get_k8s_manager()
    ws_id = str(uuid4())

    desc = mgr.describe_resource(ws_id, "Deployment", "default", "web-app", [])
    assert desc["kind"] == "Deployment"
    assert desc["name"] == "web-app"
    assert "metadata" in desc["manifest_yaml"]
    assert isinstance(desc["events"], list)

    del_res = mgr.delete_resource(ws_id, "Deployment", "default", "web-app")
    assert del_res["success"] is True
    assert "deleted" in del_res["message"].lower()


from collections.abc import AsyncIterator
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db_session
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.domain import Base, User


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
            email="k8s-owner@example.com",
            password_hash=hash_password("password123"),
            display_name="K8s Owner",
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


@pytest.mark.asyncio
async def test_k8s_api_routes(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
) -> None:
    from app.models.domain import ProvisioningWorkspace

    ws_id = uuid4()
    async with session_factory() as session:
        session.add(
            ProvisioningWorkspace(
                id=ws_id,
                owner_id=test_user.id,
                name="django",
                engine="terraform",
                provider="local",
                root_dir=f"/tmp/launchpad-workspaces/django-{ws_id}",
                status="ready",
            )
        )
        await session.commit()

    headers = {
        "Authorization": f"Bearer {create_access_token(user_id=test_user.id, email=test_user.email)}"
    }

    # 1. Get cluster context
    res = await client.get(f"/api/v1/workspaces/{ws_id}/k8s/context", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["workspace_id"] == str(ws_id)
    assert data["target_namespace"] == "lp-django"
    assert "node_count" in data

    # 2. Get resource grid (scoped to workspace namespace)
    res = await client.get(f"/api/v1/workspaces/{ws_id}/k8s/resources", headers=headers)
    assert res.status_code == 200
    resources = res.json()
    assert isinstance(resources, list)

    # 3. Describe resource
    res = await client.get(
        f"/api/v1/workspaces/{ws_id}/k8s/describe?kind=Deployment&name=launchpad-api",
        headers=headers,
    )
    assert res.status_code == 200
    desc_data = res.json()
    assert desc_data["name"] == "launchpad-api"

    # 4. Delete resource
    res = await client.request(
        "DELETE",
        f"/api/v1/workspaces/{ws_id}/k8s/resource",
        json={"kind": "Deployment", "namespace": "lp-django", "name": "launchpad-api"},
        headers=headers,
    )
    assert res.status_code == 200
    del_data = res.json()
    assert del_data["success"] is True


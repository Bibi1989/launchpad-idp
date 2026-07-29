"""Preview image build pipeline tests."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.database import get_db_session
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.domain import Base, User
from app.schemas.k8s import DeployMode
from app.services.preview_build import (
    PreviewBuildResult,
    build_image_ref,
    build_preview_image,
    preview_build_eligible,
)


def test_preview_build_eligible_custom_repo_only() -> None:
    settings = Settings(preview_build_enabled=True)
    assert preview_build_eligible(
        settings=settings,
        git_repo_url="https://github.com/acme/app.git",
        template_id=None,
        deploy_mode=DeployMode.PREVIEW.value,
    )
    assert not preview_build_eligible(
        settings=settings,
        git_repo_url="https://github.com/acme/app.git",
        template_id=None,
        deploy_mode=DeployMode.PREVIEW.value,
        workload_image_override=True,
    )
    assert not preview_build_eligible(
        settings=settings,
        git_repo_url="https://github.com/acme/app.git",
        template_id="hello-world",
        deploy_mode=DeployMode.PREVIEW.value,
    )
    assert not preview_build_eligible(
        settings=settings,
        git_repo_url="https://launchpad.local/workspaces/abc",
        template_id=None,
        deploy_mode=DeployMode.PREVIEW.value,
    )
    assert not preview_build_eligible(
        settings=Settings(preview_build_enabled=False),
        git_repo_url="https://github.com/acme/app.git",
        template_id=None,
        deploy_mode=DeployMode.PREVIEW.value,
    )


def test_build_image_ref_local_and_registry() -> None:
    env_id = str(uuid4())
    local = Settings(preview_image_registry=None, preview_build_image_prefix="launchpad-preview")
    assert build_image_ref(settings=local, environment_id=env_id, commit_sha="a8f9c12") == (
        f"launchpad-preview/{env_id.replace('-', '')[:12]}:a8f9c12"
    )
    registry = Settings(preview_image_registry="localhost:5001/launchpad")
    assert build_image_ref(settings=registry, environment_id=env_id, commit_sha="a8f9c12").startswith(
        "localhost:5001/launchpad/"
    )


@pytest.mark.asyncio
async def test_build_preview_image_simulated_without_docker() -> None:
    settings = Settings(
        preview_build_enabled=True,
        kubernetes_enabled=False,
        preview_build_image_prefix="launchpad-preview",
    )
    with patch("app.services.preview_build._docker_available", return_value=False):
        result = await build_preview_image(
            settings=settings,
            environment_id=str(uuid4()),
            git_repo_url="https://github.com/acme/app.git",
            git_branch="main",
        )
    assert isinstance(result, PreviewBuildResult)
    assert result.simulated is True
    assert result.image.startswith("launchpad-preview/")


@pytest.mark.asyncio
async def test_preview_build_status_endpoint() -> None:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async with factory() as session:
        user = User(
            email="owner@example.com",
            password_hash=hash_password("password123"),
            display_name="Owner",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    app = create_app()

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
    token = create_access_token(user_id=user.id, email=user.email)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.routers.api.get_settings") as settings_mock:
            settings_mock.return_value = Settings(preview_build_enabled=True)
            response = await client.get(
                "/api/v1/preview/build/status",
                headers={"Authorization": f"Bearer {token}"},
            )

    await engine.dispose()
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["dockerfile"] == "Dockerfile"

"""A push to ANY linked repo (frontend or backend) must rebuild the environment."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.domain import Base, ProvisioningWorkspace, User
from app.repositories.environment import EnvironmentRepository


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_push_to_any_linked_repo_matches_environment(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = User(
            email="link@example.com",
            password_hash=hash_password("password123"),
            display_name="Link",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        workspace = ProvisioningWorkspace(
            owner_id=user.id,
            name="launch-test-link",
            engine="terraform",
            provider="gcp",
            root_dir="/tmp/ws",
            status="ready",
            wizard_config_json=json.dumps(
                {
                    "linked_repos": [
                        {"full_name": "acme/launch-test-frontend", "git_branch": "main", "primary": True},
                        {"full_name": "acme/launch-test-backend", "git_branch": "main"},
                    ]
                }
            ),
        )
        session.add(workspace)
        await session.commit()
        await session.refresh(workspace)

        repo = EnvironmentRepository(session)
        env = await repo.create(
            owner_id=user.id,
            name="launch-test",
            git_branch="main",
            # Environment's primary repo is the frontend.
            git_repo_url="https://github.com/acme/launch-test-frontend.git",
            namespace_name="launchpad-env-lt",
            ttl_expires_at=datetime.now(UTC) + timedelta(hours=24),
            cost_estimate_hourly=Decimal("0"),
            workspace_id=workspace.id,
        )
        await session.commit()

        # Push to the PRIMARY (frontend) repo matches.
        fe = await repo.list_active_for_any_linked_repo(
            repo_full_name="acme/launch-test-frontend", branch="main"
        )
        assert [e.id for e in fe] == [env.id]

        # Push to the NON-PRIMARY (backend) linked repo also matches (the bug fix).
        be = await repo.list_active_for_any_linked_repo(
            repo_full_name="acme/launch-test-backend", branch="main"
        )
        assert [e.id for e in be] == [env.id]

        # A repo not linked to the workspace does not match.
        other = await repo.list_active_for_any_linked_repo(
            repo_full_name="acme/unrelated", branch="main"
        )
        assert other == []

        # Wrong branch for the linked repo does not match.
        wrong_branch = await repo.list_active_for_any_linked_repo(
            repo_full_name="acme/launch-test-backend", branch="dev"
        )
        assert wrong_branch == []


def test_build_linked_repos_marks_frontend_primary() -> None:
    """Link save records linked_repos, defaulting the frontend to the primary."""
    from app.services.repo_import import RepoImportService

    refs = [
        {"git_repo_url": "https://github.com/acme/launch-test-backend.git", "git_branch": "main"},
        {"git_repo_url": "https://github.com/acme/launch-test-frontend.git", "git_branch": "dev"},
    ]
    linked = RepoImportService._build_linked_repos(refs, {})
    by_name = {e["full_name"]: e for e in linked}
    assert by_name["acme/launch-test-frontend"]["primary"] is True
    assert by_name["acme/launch-test-backend"]["primary"] is False
    # Each repo tracks its own branch and defaults to webhook CD.
    assert by_name["acme/launch-test-frontend"]["git_branch"] == "dev"
    assert all(e["cd_mode"] == "webhook" for e in linked)


def test_link_save_persists_infra_not_source(tmp_path) -> None:
    """Link mode must persist ONLY generated infra + .launchpad metadata - never the
    linked repos' app source or app dirs (the repos are re-cloned on deploy)."""
    from pathlib import Path

    from app.services.repo_import import RepoImportService

    src = Path(tmp_path) / "import"
    durable = Path(tmp_path) / "ws"
    (src / "apps" / "launch-test-frontend").mkdir(parents=True)
    (src / "apps" / "launch-test-frontend" / "server.js").write_text("SRC", encoding="utf-8")
    (src / "apps" / "launch-test-frontend" / "package.json").write_text("{}", encoding="utf-8")
    (src / "apps" / "launch-test-frontend" / "Dockerfile").write_text("FROM node", encoding="utf-8")
    (src / "infra" / "k8s" / "manifests").mkdir(parents=True)
    (src / "infra" / "k8s" / "manifests" / "launch-test-frontend-deployment.yaml").write_text(
        "kind: Deployment", encoding="utf-8"
    )
    (src / ".launchpad").mkdir(parents=True)
    (src / ".launchpad" / "image-builds.json").write_text("[]", encoding="utf-8")

    RepoImportService._persist_link_infra(
        src=src,
        durable=durable,
        generated_files=[
            "apps/launch-test-frontend/Dockerfile",  # under apps/ -> excluded
            "infra/k8s/manifests/launch-test-frontend-deployment.yaml",
        ],
    )
    kept = {str(p.relative_to(durable)) for p in durable.rglob("*") if p.is_file()}
    assert "infra/k8s/manifests/launch-test-frontend-deployment.yaml" in kept
    assert ".launchpad/image-builds.json" in kept
    # No app source and no apps/ dir is scaffolded into a linked workspace.
    assert not any(k.startswith("apps/") for k in kept)
    assert not any("server.js" in k or "package.json" in k for k in kept)


def test_build_linked_repos_falls_back_to_primary_meta() -> None:
    from app.services.repo_import import RepoImportService

    linked = RepoImportService._build_linked_repos(
        [], {"repo_url": "https://github.com/acme/solo.git", "branch": "main"}
    )
    assert len(linked) == 1
    assert linked[0]["full_name"] == "acme/solo"
    assert linked[0]["primary"] is True

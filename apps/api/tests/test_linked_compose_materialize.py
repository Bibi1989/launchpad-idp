"""Linked repos are cloned into apps/<slug>/ and wired for Docker Compose.

A COMPOSE-mode workspace links repositories by URL (they are not files on disk).
Provision must materialize each linked repo into ``apps/<slug>/`` so the Compose
stack builds the ACTUAL repositories instead of the workspace's internal template,
and inter-service connections must be injected into the generated compose.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services import preview_build as pb
from app.services.compose_deploy import repair_compose_for_scaffolded_apps
from app.services.service_connection_env import connection_env_from_snapshot


def test_linked_repo_slug_derivation() -> None:
    assert pb._linked_repo_slug(
        {"full_name": "acme/virtual-office-frontend"}
    ) == "virtual-office-frontend"
    # launch- prefix stripped; .git suffix removed; URL fallback.
    assert pb._linked_repo_slug(
        {"git_repo_url": "https://github.com/acme/launch-billing.git"}
    ) == "billing"
    assert pb._linked_repo_slug({}) == "app"


def test_materialize_linked_repos_clones_into_apps(tmp_path) -> None:
    repos = [
        {
            "full_name": "acme/virtual-office-frontend",
            "git_repo_url": "https://github.com/acme/virtual-office-frontend.git",
            "git_branch": "main",
        },
        {
            "git_repo_url": "https://github.com/acme/virtual-office-backend.git",
            "git_branch": "dev",
        },
        # launchpad.local placeholder is skipped (not a real repo).
        {"git_repo_url": "https://launchpad.local/workspaces/abc"},
    ]
    with (
        patch.object(pb, "_resolve_build_token", return_value=None),
        patch.object(pb, "clone_git_repository", return_value="sha1234") as clone,
        patch.object(pb, "_ensure_dockerfile", return_value=None),
    ):
        slugs = pb.materialize_linked_repos_to_apps(
            workspace_root=tmp_path, linked_repos=repos
        )
    assert slugs == ["virtual-office-frontend", "virtual-office-backend"]
    assert (tmp_path / "apps").is_dir()
    # launchpad.local repo was not cloned.
    assert clone.call_count == 2


def test_materialize_uses_build_plan_contexts_with_kind_matching(tmp_path) -> None:
    """When .launchpad/image-builds.json exists, repos clone into the plan's contexts,
    matched frontend->web / backend->server, so built tags match the manifest images."""
    import json

    plan_dir = tmp_path / ".launchpad"
    plan_dir.mkdir()
    (plan_dir / "image-builds.json").write_text(
        json.dumps(
            [
                {"image": "ws-launch-web:latest", "context": "apps/web"},
                {"image": "ws-launch-server:latest", "context": "apps/server"},
            ]
        ),
        encoding="utf-8",
    )
    repos = [
        {"git_repo_url": "https://github.com/acme/virtual-office-backend.git"},
        {"git_repo_url": "https://github.com/acme/virtual-office-frontend.git"},
    ]
    dests: list[str] = []

    def _fake_clone(*, repo_url, branch, commit_sha, token, dest):
        dests.append(f"{dest.name}<-{repo_url.rsplit('/', 1)[-1]}")
        return "sha"

    with (
        patch.object(pb, "_resolve_build_token", return_value=None),
        patch.object(pb, "clone_git_repository", side_effect=_fake_clone),
        patch.object(pb, "_ensure_dockerfile", return_value=None),
    ):
        materialized = pb.materialize_linked_repos_to_apps(
            workspace_root=tmp_path, linked_repos=repos
        )
    # web context got the frontend repo, server context got the backend repo.
    assert "web<-virtual-office-frontend.git" in dests
    assert "server<-virtual-office-backend.git" in dests
    assert sorted(materialized) == ["server", "web"]


def test_connection_env_from_snapshot_http_target() -> None:
    snapshot = {
        "service_comms": [
            {"service": "virtual-office-frontend", "capabilities": []},
            {"service": "virtual-office-backend", "capabilities": []},
        ],
        "service_connections": [
            {
                "source": "virtual-office-frontend",
                "target": "virtual-office-backend",
                "protocol": "http",
            }
        ],
    }
    env = connection_env_from_snapshot(snapshot)
    assert env.get("VIRTUAL_OFFICE_BACKEND_URL") == "http://virtual-office-backend:8080"


def test_connection_env_from_snapshot_empty() -> None:
    assert connection_env_from_snapshot(None) == {}
    assert connection_env_from_snapshot({}) == {}


def test_repair_compose_injects_connection_env(tmp_path) -> None:
    # Two scaffolded apps with Dockerfiles that EXPOSE a port.
    apps = tmp_path / "apps"
    for name in ("virtual-office-frontend", "virtual-office-backend"):
        d = apps / name
        d.mkdir(parents=True)
        (d / "Dockerfile").write_text("FROM alpine\nEXPOSE 8080\n", encoding="utf-8")

    compose = repair_compose_for_scaffolded_apps(
        tmp_path,
        connection_env={"VIRTUAL_OFFICE_BACKEND_URL": "http://virtual-office-backend:8080"},
    )
    assert compose is not None
    text = compose.read_text(encoding="utf-8")
    # Build contexts point at the real repos, and the connection env is wired in.
    assert "context: apps/virtual-office-frontend" in text
    assert "context: apps/virtual-office-backend" in text
    assert "VIRTUAL_OFFICE_BACKEND_URL=http://virtual-office-backend:8080" in text

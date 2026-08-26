"""Linked-repo preview: open the primary (frontend), auto-provision the backend DB."""

from __future__ import annotations

from pathlib import Path

from app.services.env_blueprint import workspace_datastore_needs
from app.services.manifest_deploy import _resolve_preview_target
from app.workers.tasks import _primary_linked_repo_slug


def _deployment(name: str, *, image: str, app_label: str, port: int = 3000) -> dict:
    return {
        "kind": "Deployment",
        "metadata": {"name": name, "labels": {"app": app_label}},
        "spec": {
            "selector": {"matchLabels": {"app": app_label}},
            "template": {
                "metadata": {"labels": {"app": app_label}},
                "spec": {"containers": [{"name": name, "image": image, "ports": [{"containerPort": port}]}]},
            },
        },
    }


def test_preview_target_picks_wrong_service_without_hint() -> None:
    # Custom names with no frontend/backend tokens: the heuristic ties and falls to
    # alphabetical order, so the backend "core" wins over the frontend "storefront"
    # (the reported bug - Open app lands on the backend).
    docs = [
        _deployment("core", image="launch-test-core:latest", app_label="core"),
        _deployment("storefront", image="launch-test-storefront:latest", app_label="storefront"),
    ]
    label, _port = _resolve_preview_target(docs)
    assert label == "core"  # backend wins on the tie-break heuristic


def test_preview_target_honors_primary_slug() -> None:
    docs = [
        _deployment("core", image="launch-test-core:latest", app_label="core"),
        _deployment("storefront", image="launch-test-storefront:latest", app_label="storefront"),
    ]
    label, _port = _resolve_preview_target(docs, preferred_slug="launch-test-storefront")
    assert label == "storefront"  # operator's primary (frontend) wins


def test_preview_target_frontend_repo_matches_by_token() -> None:
    docs = [
        _deployment("launch-test-backend", image="launch-test-backend:latest", app_label="launch-test-backend"),
        _deployment("launch-test-frontend", image="launch-test-frontend:latest", app_label="launch-test-frontend"),
    ]
    label, _port = _resolve_preview_target(docs, preferred_slug="launch-test-frontend")
    assert label == "launch-test-frontend"


def test_primary_linked_repo_slug_prefers_explicit_primary() -> None:
    snap = {
        "linked_repos": [
            {"full_name": "org/launch-test-backend"},
            {"full_name": "org/launch-test-frontend", "primary": True},
        ]
    }
    assert _primary_linked_repo_slug(snap) == "launch-test-frontend"


def test_primary_linked_repo_slug_falls_back_to_frontend() -> None:
    snap = {
        "linked_repos": [
            {"full_name": "org/launch-test-backend"},
            {"full_name": "org/launch-test-frontend"},
        ]
    }
    assert _primary_linked_repo_slug(snap) == "launch-test-frontend"


def test_workspace_datastore_needs_detects_postgres_from_env_example(tmp_path) -> None:
    (tmp_path / ".env.example").write_text(
        "NODE_ENV=production\nDATABASE_URL=postgres://user:pass@db:5432/app\n",
        encoding="utf-8",
    )
    needs_pg, needs_redis = workspace_datastore_needs(Path(tmp_path))
    assert needs_pg is True
    assert needs_redis is False


def test_workspace_datastore_needs_detects_redis_and_ignores_mysql(tmp_path) -> None:
    (tmp_path / ".env.example").write_text(
        "DATABASE_URL=mysql://user:pass@db:3306/app\nREDIS_URL=redis://cache:6379/0\n",
        encoding="utf-8",
    )
    needs_pg, needs_redis = workspace_datastore_needs(Path(tmp_path))
    assert needs_pg is False  # explicit MySQL should not request Postgres
    assert needs_redis is True


def test_workspace_datastore_needs_none_when_no_db(tmp_path) -> None:
    (tmp_path / ".env.example").write_text("NODE_ENV=production\nPORT=3000\n", encoding="utf-8")
    assert workspace_datastore_needs(Path(tmp_path)) == (False, False)

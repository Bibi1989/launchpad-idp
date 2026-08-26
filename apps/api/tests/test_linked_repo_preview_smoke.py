"""End-to-end smoke test for the linked-repo preview fixes.

Reproduces the exact "Launch Test Link" scenario observed on the live GKE preview
namespace (frontend + backend + in-cluster postgres) and asserts, at the manifest
transformation layer, that:

  1. the in-cluster Postgres keeps its real image (DB actually connects),
  2. Open-app targets the FRONTEND (primary), even when the backend was
     previously annotated as the preview target,
  3. the backend gets DATABASE_URL pointing at ``postgres`` (not the repo's ``db``),
  4. the frontend gets a same-origin ``/api`` base (backend reachable from FE),
  5. marking both repos primary exposes both services.

These are pure transformations (no live cluster), so they run deterministically in CI.
"""

from __future__ import annotations

from app.services.manifest_deploy import (
    _datastore_env_from_documents,
    _exposed_deployment_targets,
    _has_preview_target_annotation,
    _resolve_preview_target,
    inject_extra_env_into_documents,
    remap_manifest_image_references,
    same_origin_frontend_api_env,
)
from app.workers.tasks import _exposed_linked_repo_slugs, _primary_linked_repo_slug


def _deploy(name: str, *, image: str, port: int, preview_target: bool = False, datastore: bool = False) -> dict:
    meta: dict = {"name": name, "labels": {"app": name}}
    if preview_target:
        meta["annotations"] = {"launchpad.io/preview-target": "true"}
    if datastore:
        meta.setdefault("labels", {})["launchpad.io/component"] = "datastore"
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": meta,
        "spec": {
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {"containers": [{"name": name, "image": image, "ports": [{"containerPort": port}]}]},
            },
        },
    }


def _workspace_docs(*, backend_preannotated: bool = True) -> list[dict]:
    return [
        _deploy("launch-test-frontend", image="launch-test-frontend:latest", port=8080),
        _deploy(
            "launch-test-backend",
            image="launch-test-backend:latest",
            port=3333,
            preview_target=backend_preannotated,
        ),
        # In-cluster datastore scaffolded by Launchpad (real postgres image).
        _deploy("postgres", image="postgres:16-alpine", port=5432, datastore=True),
    ]


IMAGE_MAP = {
    "launch-test-frontend:latest": "europe-west3-docker.pkg.dev/p/prev/launch-test-frontend:latest",
    "launch-test-backend:latest": "europe-west3-docker.pkg.dev/p/prev/launch-test-backend:latest",
}


def _container_image(doc: dict) -> str:
    return doc["spec"]["template"]["spec"]["containers"][0]["image"]


def _env_map(doc: dict) -> dict[str, str]:
    return {e["name"]: e.get("value") for e in doc["spec"]["template"]["spec"]["containers"][0].get("env", [])}


def test_postgres_keeps_its_image_after_remap() -> None:
    """The DB bug: the unqualified postgres image must NOT be remapped to the app image."""
    docs = _workspace_docs()
    remap_manifest_image_references(docs, IMAGE_MAP)
    by_name = {d["metadata"]["name"]: d for d in docs}
    assert _container_image(by_name["postgres"]) == "postgres:16-alpine"
    assert _container_image(by_name["launch-test-frontend"]).endswith("/launch-test-frontend:latest")
    assert _container_image(by_name["launch-test-backend"]).endswith("/launch-test-backend:latest")


def test_open_app_targets_frontend_even_when_backend_preannotated() -> None:
    """The core bug: preview must resolve to the frontend primary, not the backend."""
    docs = _workspace_docs(backend_preannotated=True)
    label, port = _resolve_preview_target(docs, preferred_slug="launch-test-frontend")
    assert label == "launch-test-frontend"
    assert port == 8080
    by_name = {d["metadata"]["name"]: d for d in docs}
    # Annotation moved to the frontend; backend cleared.
    assert _has_preview_target_annotation(by_name["launch-test-frontend"]) is True
    assert by_name["launch-test-backend"]["metadata"]["annotations"]["launchpad.io/preview-target"] == "false"


def test_frontend_is_default_target_without_annotation_or_hint() -> None:
    docs = _workspace_docs(backend_preannotated=False)
    label, _port = _resolve_preview_target(docs)
    assert label == "launch-test-frontend"


def test_marking_both_primary_exposes_both_services() -> None:
    docs = _workspace_docs()
    targets = _exposed_deployment_targets(docs, ["launch-test-frontend", "launch-test-backend"])
    labels = {label for label, _ in targets}
    assert labels == {"launch-test-frontend", "launch-test-backend"}
    # Datastore is never exposed.
    assert "postgres" not in labels


def test_backend_gets_postgres_database_url_frontend_does_not() -> None:
    docs = _workspace_docs()
    datastore_env = _datastore_env_from_documents(docs, app_name="launch-test")
    assert datastore_env["DATABASE_URL"] == "postgresql://launchpad:changeme@postgres:5432/launch_test"
    inject_extra_env_into_documents(docs, datastore_env, only_backend=True)
    by_name = {d["metadata"]["name"]: d for d in docs}
    assert _env_map(by_name["launch-test-backend"]).get("DATABASE_URL") == datastore_env["DATABASE_URL"]
    assert "DATABASE_URL" not in _env_map(by_name["launch-test-frontend"])


def test_frontend_api_base_defaults_to_base_not_api() -> None:
    """By default the frontend gets the backend BASE (empty), never a forced /api."""
    docs = _workspace_docs()
    inject_extra_env_into_documents(docs, same_origin_frontend_api_env(""), only_frontend=True)
    by_name = {d["metadata"]["name"]: d for d in docs}
    fe_env = _env_map(by_name["launch-test-frontend"])
    assert fe_env.get("NEXT_PUBLIC_API_URL") == ""
    assert fe_env.get("VITE_API_URL") == ""
    # Backend must not receive the frontend API base.
    assert "NEXT_PUBLIC_API_URL" not in _env_map(by_name["launch-test-backend"])


def test_frontend_api_base_honors_configured_path() -> None:
    """When the operator sets a path on the connector, it is appended to the base."""
    docs = _workspace_docs()
    inject_extra_env_into_documents(docs, same_origin_frontend_api_env("/api"), only_frontend=True)
    fe_env = _env_map({d["metadata"]["name"]: d for d in docs}["launch-test-frontend"])
    assert fe_env.get("VITE_API_URL") == "/api"


def test_linked_repos_generate_per_service_manifests(tmp_path) -> None:
    """A 2-repo link must produce a Deployment per repo (not one generic ``app`` pod)."""
    import json
    from pathlib import Path

    from app.services.preview_build import ensure_linked_repo_workload_manifests

    for name, port in [("launch-test-frontend", 8080), ("launch-test-backend", 3333)]:
        svc = tmp_path / "apps" / name
        svc.mkdir(parents=True)
        (svc / "Dockerfile").write_text(f"FROM node:20-alpine\nEXPOSE {port}\n", encoding="utf-8")

    written = ensure_linked_repo_workload_manifests(
        Path(tmp_path), env_name="launch-test", primary_slug="launch-test-frontend"
    )
    files = {Path(w).name for w in written}
    assert "launch-test-frontend-deployment.yaml" in files
    assert "launch-test-backend-deployment.yaml" in files

    # Build plan maps each service to its apps/<slug> context so both images build.
    plan = json.loads((tmp_path / ".launchpad" / "image-builds.json").read_text())
    services = {e["service"] for e in plan}
    assert services == {"launch-test-frontend", "launch-test-backend"}

    fe = (tmp_path / "infra/k8s/manifests/launch-test-frontend-deployment.yaml").read_text()
    be = (tmp_path / "infra/k8s/manifests/launch-test-backend-deployment.yaml").read_text()
    assert 'containerPort: 8080' in fe and 'launchpad.io/preview-target: "true"' in fe
    assert 'containerPort: 3333' in be and 'launchpad.io/preview-target: "true"' not in be
    # The orphan generic ``app`` Deployment is pruned so no extra pod is created.
    assert not (tmp_path / "infra/k8s/manifests/deployment.yaml").exists()


def test_single_linked_repo_keeps_generic_app(tmp_path) -> None:
    """One repo does not trigger per-service generation (generic app is fine)."""
    from pathlib import Path

    from app.services.preview_build import ensure_linked_repo_workload_manifests

    svc = tmp_path / "apps" / "solo"
    svc.mkdir(parents=True)
    (svc / "Dockerfile").write_text("FROM node:20-alpine\nEXPOSE 3000\n", encoding="utf-8")
    assert ensure_linked_repo_workload_manifests(Path(tmp_path), env_name="solo") == []


def test_worker_primary_and_exposed_slug_selection() -> None:
    both_primary = {
        "linked_repos": [
            {"full_name": "org/launch-test-backend", "primary": True},
            {"full_name": "org/launch-test-frontend", "primary": True},
        ]
    }
    # Open-app prefers the frontend even when backend is also primary and listed first.
    assert _primary_linked_repo_slug(both_primary) == "launch-test-frontend"
    assert set(_exposed_linked_repo_slugs(both_primary)) == {
        "launch-test-frontend",
        "launch-test-backend",
    }
    # Default (nothing marked primary): frontend is the single exposed target.
    none_primary = {
        "linked_repos": [
            {"full_name": "org/launch-test-backend"},
            {"full_name": "org/launch-test-frontend"},
        ]
    }
    assert _exposed_linked_repo_slugs(none_primary) == ["launch-test-frontend"]

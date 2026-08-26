"""Tests for per-service env blueprint loading and merge precedence."""

from __future__ import annotations

from pathlib import Path

from app.services.env_blueprint import (
    DEFAULT_ENV_BLUEPRINT_WRITE_NAME,
    apply_blueprints_to_build_env,
    ensure_env_blueprint_stub,
    load_service_env_blueprint,
    merge_env_layers,
    resolve_blueprint_for_name,
)
from pkg.detector.env_example import ENV_BLUEPRINT_FILENAMES, collect_env_blueprint_map


def test_env_launchpad_preferred_over_example(tmp_path: Path) -> None:
    assert ENV_BLUEPRINT_FILENAMES[0] == ".env.launchpad"
    (tmp_path / ".env.example").write_text("PORT=3000\nSHARED=from-example\n", encoding="utf-8")
    (tmp_path / ".env.launchpad").write_text(
        "PORT=8080\nSHARED=from-launchpad\n",
        encoding="utf-8",
    )
    blueprint = collect_env_blueprint_map(tmp_path)
    assert blueprint["PORT"] == "8080"
    assert blueprint["SHARED"] == "from-launchpad"


def test_merge_env_layers_later_wins_empty_skipped() -> None:
    merged = merge_env_layers(
        {"A": "1", "B": "keep", "C": "old"},
        {"A": "2", "B": "", "D": "new"},
        {"A": "3"},
    )
    assert merged == {"A": "3", "B": "keep", "C": "old", "D": "new"}


def test_resolve_blueprint_for_named_app(tmp_path: Path) -> None:
    frontend = tmp_path / "apps" / "frontend"
    frontend.mkdir(parents=True)
    (frontend / ".env.example").write_text(
        "VITE_API_URL=http://localhost:3333\nNODE_ENV=production\n",
        encoding="utf-8",
    )
    backend = tmp_path / "apps" / "backend"
    backend.mkdir(parents=True)
    (backend / ".env.example").write_text("DATABASE_URL=postgresql://db/app\n", encoding="utf-8")

    fe = resolve_blueprint_for_name(tmp_path, "launch-test-frontend")
    assert fe["VITE_API_URL"] == "http://localhost:3333"
    be = resolve_blueprint_for_name(tmp_path, "backend")
    assert be["DATABASE_URL"].startswith("postgresql://")


def test_ensure_stub_writes_example_not_overwrite(tmp_path: Path) -> None:
    written = ensure_env_blueprint_stub(tmp_path, keys={"PORT": "8080"})
    assert written is not None
    assert written.name == DEFAULT_ENV_BLUEPRINT_WRITE_NAME
    assert "PORT=8080" in written.read_text(encoding="utf-8")
    assert ensure_env_blueprint_stub(tmp_path) is None


def test_apply_blueprints_to_build_env_platform_wins(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        "VITE_API_URL=http://example.local\nFOO=from-blueprint\n",
        encoding="utf-8",
    )
    merged = apply_blueprints_to_build_env(
        tmp_path,
        {"VITE_API_URL": "http://backend:3333", "BAR": "platform"},
    )
    assert merged["VITE_API_URL"] == "http://backend:3333"
    assert merged["FOO"] == "from-blueprint"
    assert merged["BAR"] == "platform"
    assert load_service_env_blueprint(tmp_path)["FOO"] == "from-blueprint"


def test_same_origin_frontend_api_env() -> None:
    from app.services.manifest_deploy import same_origin_frontend_api_env

    env = same_origin_frontend_api_env("/api")
    assert env["VITE_API_URL"] == "/api"
    assert env["NEXT_PUBLIC_API_URL"] == "/api"


def test_inject_frontend_api_proxy_mounts_nginx_config() -> None:
    from app.services.manifest_deploy import inject_frontend_api_proxy

    docs: list[dict] = [
        {
            "kind": "Deployment",
            "metadata": {"name": "launch-test-frontend"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "web",
                                "image": "nginxinc/nginx-unprivileged:alpine",
                                "ports": [{"containerPort": 8080}],
                            }
                        ]
                    }
                }
            },
        },
        {
            "kind": "Deployment",
            "metadata": {"name": "launch-test-backend"},
            "spec": {"template": {"spec": {"containers": [{"name": "api"}]}}},
        },
    ]
    inject_frontend_api_proxy(
        docs,
        backend_url="http://launch-test-backend-service:3333",
        listen_port=8080,
    )
    assert any(
        d.get("kind") == "ConfigMap"
        and str(d.get("metadata", {}).get("name") or "").startswith(
            "launchpad-frontend-api-proxy"
        )
        for d in docs
    )
    fe = next(d for d in docs if d.get("metadata", {}).get("name") == "launch-test-frontend")
    container = fe["spec"]["template"]["spec"]["containers"][0]
    mounts = container["volumeMounts"]
    assert any(m.get("mountPath") == "/etc/nginx/conf.d/default.conf" for m in mounts)
    assert container["command"] == ["/bin/sh", "-c"]
    # The undefined-rewrite still runs (now generic: undefined/ -> the configured base).
    assert "undefined/" in container["args"][0]
    assert "exec nginx" in container["args"][0]
    be = next(d for d in docs if d.get("metadata", {}).get("name") == "launch-test-backend")
    assert "volumeMounts" not in be["spec"]["template"]["spec"]["containers"][0]


def test_inject_frontend_api_proxy_skips_non_nginx_frontend() -> None:
    from app.services.manifest_deploy import inject_frontend_api_proxy

    docs: list[dict] = [
        {
            "kind": "Deployment",
            "metadata": {"name": "launch-next-frontend"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "web",
                                "image": "launch-next-frontend:latest",
                                "ports": [{"containerPort": 3000}],
                            }
                        ]
                    }
                }
            },
        },
    ]
    inject_frontend_api_proxy(
        docs,
        backend_url="http://launch-api-service:8080",
        listen_port=3000,
    )
    assert not any(d.get("kind") == "ConfigMap" for d in docs)
    container = docs[0]["spec"]["template"]["spec"]["containers"][0]
    assert "command" not in container


def test_gcp_image_package_ref_strips_tag_and_digest() -> None:
    from app.services.cloud_instance_compute import _gcp_image_package_ref

    assert (
        _gcp_image_package_ref(
            "europe-west3-docker.pkg.dev/acme/launchpad-previews/aaaa/web:latest"
        )
        == "europe-west3-docker.pkg.dev/acme/launchpad-previews/aaaa/web"
    )
    assert (
        _gcp_image_package_ref(
            "europe-west3-docker.pkg.dev/acme/launchpad-previews/web@sha256:abc"
        )
        == "europe-west3-docker.pkg.dev/acme/launchpad-previews/web"
    )


def test_datastore_env_from_documents_postgres() -> None:
    from app.services.manifest_deploy import _datastore_env_from_documents

    docs = [
        {"kind": "Deployment", "metadata": {"name": "postgres"}},
        {"kind": "Deployment", "metadata": {"name": "backend"}},
    ]
    env = _datastore_env_from_documents(docs, app_name="launch-test")
    assert env["DATABASE_URL"].startswith("postgresql://")
    assert "@postgres:5432/" in env["DATABASE_URL"]

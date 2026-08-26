"""User-defined env vars flow from the wizard into every app workload container."""

from __future__ import annotations

import json

from app.schemas.cloud import (
    LocalCloudConfig,
    ProvisioningWizardRequest,
    WorkspaceEnvVar,
)
from app.services.manifest_deploy import (
    inject_extra_env_into_documents,
    merge_preview_cors_origin,
)
from app.services.provisioning import ProvisioningService
from app.services.service_connection_env import custom_env_from_snapshot


def test_wizard_config_round_trips_env_vars() -> None:
    req = ProvisioningWizardRequest(
        name="demo-app",
        cloud=LocalCloudConfig(),
        env_vars=[WorkspaceEnvVar(key="API_KEY", value="sk-1"), WorkspaceEnvVar(key="FLAG", value="true")],
    )
    data = json.loads(ProvisioningService._wizard_config_json(req))
    assert data["env_vars"] == [
        {"key": "API_KEY", "value": "sk-1"},
        {"key": "FLAG", "value": "true"},
    ]
    assert custom_env_from_snapshot(data) == {"API_KEY": "sk-1", "FLAG": "true"}


def test_custom_env_from_snapshot_skips_blank_keys() -> None:
    snap = {"env_vars": [{"key": "OK", "value": "1"}, {"key": "  ", "value": "x"}, {"value": "y"}]}
    assert custom_env_from_snapshot(snap) == {"OK": "1"}
    assert custom_env_from_snapshot(None) == {}


def test_inject_extra_env_overrides_and_skips_datastores() -> None:
    app_dep = {
        "kind": "Deployment",
        "metadata": {"name": "launch-web"},
        "spec": {"template": {"spec": {"containers": [{"name": "web", "env": [{"name": "PORT", "value": "8080"}]}]}}},
    }
    postgres_dep = {
        "kind": "Deployment",
        "metadata": {"name": "postgres"},
        "spec": {"template": {"spec": {"containers": [{"name": "postgres", "env": []}]}}},
    }
    inject_extra_env_into_documents([app_dep, postgres_dep], {"API_KEY": "sk-1", "PORT": "9090"})

    app_env = {e["name"]: e["value"] for e in app_dep["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert app_env == {"PORT": "9090", "API_KEY": "sk-1"}  # user PORT overrides, API_KEY added
    # Datastore workload is left untouched.
    assert postgres_dep["spec"]["template"]["spec"]["containers"][0]["env"] == []


def test_cors_origin_added_when_absent() -> None:
    out = merge_preview_cors_origin({"API_KEY": "x"}, "https://ws-1.example.com")
    assert out["CORS_ALLOWED_ORIGINS"] == "https://ws-1.example.com"
    assert out["FRONTEND_URL"] == "https://ws-1.example.com"
    assert out["API_KEY"] == "x"


def test_cors_origin_appended_and_deduped() -> None:
    appended = merge_preview_cors_origin(
        {"CORS_ORIGINS": "http://localhost:3000"}, "https://ws-1.example.com"
    )
    assert appended["CORS_ORIGINS"] == "http://localhost:3000,https://ws-1.example.com"
    # Already present -> not duplicated.
    deduped = merge_preview_cors_origin(
        {"CORS_ORIGINS": "https://ws-1.example.com"}, "https://ws-1.example.com"
    )
    assert deduped["CORS_ORIGINS"] == "https://ws-1.example.com"


def test_cors_noop_without_origin() -> None:
    assert merge_preview_cors_origin({"A": "1"}, None) == {"A": "1"}
    assert merge_preview_cors_origin({"A": "1"}, "") == {"A": "1"}


def test_apply_ephemeral_datastores_returns_connection_env() -> None:
    """The datastore apply returns DATABASE_URL / REDIS_URL so MANIFEST deploys can inject
    them straight into the app container (this is what connects the datastore to the app)."""
    from app.core.config import Settings
    from app.services.kubernetes import KubernetesProvisioner

    prov = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    env = prov.apply_ephemeral_datastores(
        namespace="lp-x", name="app", enable_postgres=True, enable_redis=True
    )
    assert "DATABASE_URL" in env and env["DATABASE_URL"]
    assert "REDIS_URL" in env and env["REDIS_URL"]
    # Nothing enabled -> empty map (no injection).
    assert prov.apply_ephemeral_datastores(namespace="lp-x", name="app") == {}


def test_inject_extra_env_noop_when_empty() -> None:
    doc = {"kind": "Deployment", "metadata": {"name": "launch-web"},
           "spec": {"template": {"spec": {"containers": [{"name": "web"}]}}}}
    inject_extra_env_into_documents([doc], None)
    inject_extra_env_into_documents([doc], {})
    assert "env" not in doc["spec"]["template"]["spec"]["containers"][0]


def test_write_build_env_file_writes_production_local(tmp_path) -> None:
    from app.services.preview_build import _LAUNCHPAD_BUILD_ENV_LOCAL, _write_build_env_file

    (tmp_path / ".env.production").write_text("VITE_API_URL=\nEXISTING=keep\n", encoding="utf-8")
    _write_build_env_file(
        tmp_path, {"VITE_API_URL": "/api", "EXISTING": "forced"}
    )
    # Launchpad overwrites dotenv files so empty VITE_* cannot bake as undefined.
    prod = (tmp_path / ".env.production").read_text(encoding="utf-8")
    assert "VITE_API_URL=/api" in prod
    assert "EXISTING=forced" in prod
    local = (tmp_path / _LAUNCHPAD_BUILD_ENV_LOCAL).read_text(encoding="utf-8")
    assert "VITE_API_URL=/api" in local


def test_ensure_dockerfile_bakes_frontend_api(tmp_path) -> None:
    from app.services.preview_build import ensure_dockerfile_bakes_frontend_api

    df = tmp_path / "Dockerfile"
    df.write_text(
        "FROM node:22-alpine\nWORKDIR /src\nCOPY . .\nRUN npm run build\n",
        encoding="utf-8",
    )
    assert ensure_dockerfile_bakes_frontend_api(df) is True
    text = df.read_text(encoding="utf-8")
    # Default ARG is the same-origin BASE (empty); the real value is supplied via
    # --build-arg from the connector's API path (blank = base URL, no forced /api).
    assert "ARG VITE_API_URL=" in text
    assert "ARG VITE_API_URL=/api" not in text
    assert "ENV VITE_API_URL=$VITE_API_URL" in text
    assert text.index("ARG VITE_API_URL") < text.index("RUN npm run build")


def test_write_build_env_file_noop_when_empty(tmp_path) -> None:
    from app.services.preview_build import _LAUNCHPAD_BUILD_ENV_LOCAL, _write_build_env_file

    _write_build_env_file(tmp_path, {})
    assert not (tmp_path / _LAUNCHPAD_BUILD_ENV_LOCAL).exists()


def test_native_bootstrap_writes_and_sources_env_file() -> None:
    """Bare systemd/pm2 VM deploys write injected vars to .env (build-time) and the
    runtime wrapper sources it, so a frontend on a VM gets the backend URL too."""
    from app.schemas.cloud import InstanceProcessStrategy
    from app.services.attach_deploy import _native_bootstrap_and_start, _native_env_file_snippet

    snippet = _native_env_file_snippet({"VITE_API_URL": "http://api:8080", "API_KEY": "sk-1"})
    assert "VITE_API_URL='http://api:8080'" in snippet
    assert "LAUNCHPAD-ENV-START" in snippet

    script = _native_bootstrap_and_start(
        strategy=InstanceProcessStrategy.SYSTEMD,
        app_dir="/opt/launchpad/app",
        workdir_rel=".",
        listen=8080,
        unit="demo",
        start_command="npm start",
        autodetect_on_vm=True,
        env_vars={"VITE_API_URL": "http://api:8080"},
    )
    # .env is written (before build) and sourced at runtime by the wrapper.
    assert "VITE_API_URL='http://api:8080'" in script
    assert "[ -f .env ] && . ./.env" in script

    # No env -> no .env block injected.
    plain = _native_bootstrap_and_start(
        strategy=InstanceProcessStrategy.SYSTEMD,
        app_dir="/opt/launchpad/app",
        workdir_rel=".",
        listen=8080,
        unit="demo",
        start_command="npm start",
        autodetect_on_vm=True,
    )
    assert "LAUNCHPAD-ENV-START" not in plain

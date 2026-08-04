from pathlib import Path

import pytest
from app.core.config import Settings
from app.services.manifest_deploy import _load_image_to_local_cluster
from app.services.kubernetes import _detect_kind_forwarded_node_ports, resolve_preview_node_port


def test_local_k8s_engine_default_is_k3s():
    # k3s (via k3d) is the default engine; kind is the opt-in alternative.
    # Ignore any developer .env override so this asserts the *code* default.
    settings = Settings(_env_file=None)
    assert settings.local_k8s_engine == "k3s"


def test_local_k8s_engine_normalizes_aliases():
    settings_k3d = Settings(local_k8s_engine="k3d")
    assert settings_k3d.local_k8s_engine == "k3s"

    settings_kind = Settings(local_k8s_engine="KIND")
    assert settings_kind.local_k8s_engine == "kind"

    # An unrecognized value falls back to the default engine (k3s).
    settings_bad = Settings(local_k8s_engine="whatever")
    assert settings_bad.local_k8s_engine == "k3s"


def test_resolve_preview_node_port_with_k3s_engine():
    port = resolve_preview_node_port(
        "test-env-123",
        existing_port=None,
        port_min=30080,
        port_max=30089,
        cluster_name="launchpad",
    )
    assert 30080 <= port <= 30089


def test_engine_selects_tool_and_context():
    k3s = Settings(local_k8s_engine="k3s", kind_cluster_name="launchpad")
    assert k3s.local_cluster_tool == "k3d"
    assert k3s.local_cluster_context == "k3d-launchpad"
    assert k3s.resolved_kubernetes_context == "k3d-launchpad"

    kind = Settings(local_k8s_engine="kind", kind_cluster_name="launchpad")
    assert kind.local_cluster_tool == "kind"
    assert kind.local_cluster_context == "kind-launchpad"
    assert kind.resolved_kubernetes_context == "kind-launchpad"


def test_context_follows_active_engine_when_it_names_other_engine():
    # One env var (LOCAL_K8S_ENGINE) must re-point a stale cross-engine context.
    s = Settings(local_k8s_engine="k3s", kubernetes_context="kind-launchpad")
    assert s.resolved_kubernetes_context == "k3d-launchpad"
    s2 = Settings(local_k8s_engine="kind", kubernetes_context="k3d-launchpad")
    assert s2.resolved_kubernetes_context == "kind-launchpad"


def test_explicit_remote_context_is_preserved():
    s = Settings(local_k8s_engine="k3s", kubernetes_context="gke-prod-us")
    assert s.resolved_kubernetes_context == "gke-prod-us"


def test_repo_root_survives_oci_image_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    """OCI Dockerfile lays code at /app/app/...; parents[4] must not IndexError."""
    from app.services import kind_cluster as kc

    monkeypatch.setattr(kc, "__file__", "/app/app/services/kind_cluster.py")
    root = kc._repo_root()
    assert root is not None
    assert isinstance(root, Path)


def test_repo_root_prefers_monorepo_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import kind_cluster as kc

    repo = tmp_path / "launchpad"
    (repo / "scripts").mkdir(parents=True)
    (repo / "apps").mkdir()
    fake = repo / "apps" / "api" / "app" / "services" / "kind_cluster.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(kc, "__file__", str(fake))

    assert kc._repo_root() == repo.resolve()


def test_kind_cluster_module_dispatches_script_by_engine(monkeypatch):
    from app.services import kind_cluster as kc

    monkeypatch.setattr(kc, "get_settings", lambda: Settings(local_k8s_engine="k3s"))
    assert kc._engine() == "k3s"
    assert kc._cluster_tool() == "k3d"
    assert kc._script_name("up") == "k3s-up.sh"
    assert kc._context_for("launchpad") == "k3d-launchpad"

    monkeypatch.setattr(kc, "get_settings", lambda: Settings(local_k8s_engine="kind"))
    assert kc._script_name("down") == "kind-down.sh"
    assert kc._context_for("launchpad") == "kind-launchpad"

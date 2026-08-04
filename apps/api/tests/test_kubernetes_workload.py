"""Kubernetes provisioner workload spec tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.services.kubernetes import KubernetesProvisioner


def test_rebuild_workload_container_spec_includes_quota_resources() -> None:
    provisioner = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    container = provisioner._build_app_container(
        image="nginx:1.27-alpine",
        git_repo_url="https://github.com/acme/app.git",
        git_branch="main",
        commit_sha="abc1234",
        listen_port=80,
    )
    assert container.resources.requests["cpu"] == "100m"
    assert container.resources.requests["memory"] == "128Mi"
    assert container.resources.limits["cpu"] == "500m"
    assert container.resources.limits["memory"] == "512Mi"
    assert container.liveness_probe is not None
    assert container.readiness_probe is not None


def test_build_app_deployment_sets_non_root_uid_for_nginx() -> None:
    provisioner = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    deployment = provisioner._build_app_deployment(
        namespace="launchpad-env-test",
        labels={"app": "app"},
        annotations={},
        image="nginx:1.27-alpine",
        git_repo_url="https://github.com/acme/app.git",
        git_branch="main",
        listen_port=80,
    )
    pod_spec = deployment.spec.template.spec
    assert pod_spec.security_context.run_as_non_root is True
    assert pod_spec.security_context.run_as_user == 101


def test_build_app_container_non_nginx_uses_tcp_probes() -> None:
    provisioner = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    listen_port = 5173
    container = provisioner._build_app_container(
        image="example/non-webserver:1.0",
        git_repo_url="https://github.com/acme/app.git",
        git_branch="main",
        commit_sha=None,
        listen_port=listen_port,
    )

    assert container.ports is not None
    assert container.ports[0].container_port == listen_port

    assert container.startup_probe is not None
    assert container.startup_probe.tcp_socket is not None
    assert container.startup_probe.tcp_socket.port == listen_port

    assert container.readiness_probe is not None
    assert container.readiness_probe.tcp_socket is not None
    assert container.readiness_probe.tcp_socket.port == listen_port


def test_build_app_deployment_wires_datastore_secret_and_waits() -> None:
    provisioner = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    deployment = provisioner._build_app_deployment(
        namespace="launchpad-env-test",
        labels={"app": "app"},
        annotations={},
        image="example/api:1.0",
        git_repo_url="https://github.com/acme/app.git",
        git_branch="main",
        listen_port=8000,
        enable_postgres=True,
        enable_redis=True,
    )
    pod_spec = deployment.spec.template.spec
    assert pod_spec.init_containers is not None
    init_names = [c.name for c in pod_spec.init_containers]
    assert init_names == ["wait-for-postgres", "wait-for-redis"]
    container = pod_spec.containers[0]
    env = {e.name: e.value for e in container.env}
    assert env["HAS_DATABASE"] == "true"
    assert env["HAS_REDIS"] == "true"
    assert container.env_from is not None
    assert container.env_from[0].secret_ref.name == "app-secrets"
    annotations = deployment.metadata.annotations
    assert annotations["launchpad.io/enable-postgres"] == "true"
    assert annotations["launchpad.io/enable-redis"] == "true"


def test_build_app_container_skips_secrets_without_datastores() -> None:
    provisioner = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    container = provisioner._build_app_container(
        image="example/api:1.0",
        git_repo_url="https://github.com/acme/app.git",
        git_branch="main",
        listen_port=8000,
    )
    assert container.env_from is None
    env_names = {e.name for e in container.env}
    assert "HAS_DATABASE" not in env_names


def test_build_app_deployment_sets_pod_security_context_only_for_nginx() -> None:
    provisioner = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    deployment = provisioner._build_app_deployment(
        namespace="launchpad-env-test",
        labels={"app": "app"},
        annotations={},
        image="example/non-webserver:1.0",
        git_repo_url="https://github.com/acme/app.git",
        git_branch="main",
        listen_port=5173,
    )
    pod_spec = deployment.spec.template.spec
    assert pod_spec.security_context is None


def test_provision_applies_datastores_after_namespace_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ephemeral datastores must be applied after governance (namespace) and
    before the app workload - otherwise the Secret/manifests 404 into a
    namespace that has not been created yet.
    """
    # Avoid loading real kube clients / touching a cluster.
    monkeypatch.setattr(KubernetesProvisioner, "_load_clients", lambda self: None)
    monkeypatch.setattr(
        "app.services.manifest_deploy.build_and_load_kind_images", lambda **_: None
    )
    monkeypatch.setattr(
        "app.services.manifest_deploy._is_image_in_kind", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        "app.services.kubernetes.resolve_preview_node_port", lambda *_a, **_k: 30080
    )

    provisioner = KubernetesProvisioner(Settings(kubernetes_enabled=True))

    manager = MagicMock()
    monkeypatch.setattr(provisioner, "apply_governance", manager.apply_governance)
    monkeypatch.setattr(
        provisioner, "apply_ephemeral_datastores", manager.apply_ephemeral_datastores
    )
    monkeypatch.setattr(provisioner, "_list_allocated_node_ports", lambda **_: set())
    monkeypatch.setattr(provisioner, "_read_namespaced_app_node_port", lambda _ns: None)
    monkeypatch.setattr(provisioner, "wait_for_workload_ready", lambda **_: None)

    def fake_apply_workload(**kwargs: object) -> int:
        manager._apply_workload(**kwargs)
        return 30080

    monkeypatch.setattr(provisioner, "_apply_workload", fake_apply_workload)

    provisioner.provision(
        namespace="launchpad-env-abc",
        environment_id="env-abc",
        name="demo",
        git_branch="main",
        git_repo_url="https://github.com/acme/demo.git",
        ttl_expires_at="2026-12-01T00:00:00+00:00",
        enable_postgres=True,
        enable_redis=True,
    )

    ordered = [c[0] for c in manager.mock_calls]
    assert "apply_governance" in ordered
    assert "apply_ephemeral_datastores" in ordered
    assert ordered.index("apply_governance") < ordered.index("apply_ephemeral_datastores")
    assert ordered.index("apply_ephemeral_datastores") < ordered.index("_apply_workload")

    ds_call = next(c for c in manager.mock_calls if c[0] == "apply_ephemeral_datastores")
    assert ds_call.kwargs["namespace"] == "launchpad-env-abc"
    assert ds_call.kwargs["enable_postgres"] is True
    assert ds_call.kwargs["enable_redis"] is True

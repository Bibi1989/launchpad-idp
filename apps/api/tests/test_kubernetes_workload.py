"""Kubernetes provisioner workload spec tests."""

from __future__ import annotations

from pathlib import Path
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

    provisioner = KubernetesProvisioner(Settings(kubernetes_enabled=True, default_workload_image="app:latest"))

    manager = MagicMock()
    monkeypatch.setattr(provisioner, "apply_governance", manager.apply_governance)
    monkeypatch.setattr(
        provisioner, "apply_ephemeral_datastores", manager.apply_ephemeral_datastores
    )
    monkeypatch.setattr(provisioner, "list_allocated_node_ports", lambda **_: set())
    monkeypatch.setattr(provisioner, "read_namespaced_app_node_port", lambda _ns: None)
    monkeypatch.setattr(provisioner, "wait_for_workload_ready", lambda **_: None)
    monkeypatch.setattr(provisioner, "ensure_registry_pull_secret", lambda **_: None)
    monkeypatch.setattr(provisioner, "workspace_preview_host", lambda **_: None)

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


def test_read_or_none_swallows_404_and_reraises_others() -> None:
    from kubernetes.client.rest import ApiException

    from app.services.kubernetes import _read_or_none

    assert _read_or_none(lambda: "resource") == "resource"

    def not_found() -> None:
        raise ApiException(status=404)

    assert _read_or_none(not_found) is None

    def server_error() -> None:
        raise ApiException(status=500)

    with pytest.raises(ApiException):
        _read_or_none(server_error)


def test_ignore_404_swallows_404_and_reraises_others() -> None:
    from kubernetes.client.rest import ApiException

    from app.services.kubernetes import _ignore_404

    calls: list[int] = []
    _ignore_404(lambda: calls.append(1))
    assert calls == [1]

    def not_found() -> None:
        raise ApiException(status=404)

    _ignore_404(not_found)  # already gone: no raise

    def conflict() -> None:
        raise ApiException(status=409)

    with pytest.raises(ApiException):
        _ignore_404(conflict)


def test_recreate_service_retries_being_deleted_409() -> None:
    from kubernetes.client.rest import ApiException

    from app.services.kubernetes import KubernetesProvisioner

    provisioner = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    core = MagicMock()
    provisioner._core = core

    existing = MagicMock()
    existing.spec.type = "NodePort"
    existing.metadata.deletion_timestamp = None
    gone = ApiException(status=404)
    conflict = ApiException(status=409, reason="Conflict")
    conflict.body = (
        '{"message":"object is being deleted: services \\"app\\" already exists",'
        '"reason":"AlreadyExists","code":409}'
    )

    core.read_namespaced_service.side_effect = [existing, gone]
    core.create_namespaced_service.side_effect = [conflict, MagicMock()]

    from unittest.mock import patch

    with patch("time.sleep"):
        provisioner.recreate_service(namespace="ns", body=MagicMock(), name="app")

    assert core.delete_namespaced_service.called
    assert core.create_namespaced_service.call_count == 2


def test_clients_ready_false_when_kubernetes_disabled() -> None:
    provisioner = KubernetesProvisioner(Settings(kubernetes_enabled=False))
    assert provisioner.clients_ready is False


def test_local_sandbox_deploy_uses_kind_load_not_gcp_push(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Local Sandbox deployments on a local cluster must build and load images into Kind/k3s
    rather than pushing to GCP Artifact Registry.
    """
    from app.services.manifest_deploy import ManifestDeployer

    provisioner = MagicMock()
    provisioner.remote_cluster = False
    provisioner.container_build_platform.return_value = "linux/amd64"
    settings = Settings(kubernetes_enabled=True, default_workload_image="app:latest")
    deployer = ManifestDeployer(provisioner=provisioner, settings=settings)

    # Setup minimal workspace files
    (tmp_path / "package.json").write_text("{}")
    manifests_dir = tmp_path / "infra" / "k8s" / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    (manifests_dir / "app.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web-frontend\nspec:\n"
        "  selector:\n    matchLabels:\n      app: web\n  template:\n    metadata:\n"
        "      labels:\n        app: web\n    spec:\n      containers:\n      - name: app\n"
        "        image: nginx:alpine\n"
    )

    build_kind_called = []
    build_push_called = []

    monkeypatch.setattr(
        "app.services.manifest_deploy.build_and_load_kind_images",
        lambda *a, **k: build_kind_called.append((a, k)),
    )
    monkeypatch.setattr(
        "app.services.manifest_deploy.build_and_push_workspace_images",
        lambda *a, **k: build_push_called.append((a, k)),
    )
    monkeypatch.setattr(deployer, "_apply_documents", lambda **_: None)
    monkeypatch.setattr(deployer, "_strip_preview_incompatible_controllers", lambda **_: None)
    monkeypatch.setattr(provisioner, "list_allocated_node_ports", lambda **_: set())
    monkeypatch.setattr(provisioner, "read_namespaced_app_node_port", lambda _ns: None)

    deployer.deploy(
        workspace_root=tmp_path,
        namespace="launchpad-env-test",
        environment_id="env-test",
        name="test-app",
        git_branch="main",
        git_repo_url="https://github.com/acme/test.git",
        ttl_expires_at=None,
        cloud_provider="local",
    )

    assert len(build_kind_called) >= 1
    assert len(build_push_called) == 0


def test_local_sandbox_deploy_injects_frontend_api_proxy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Frontend API proxying must be injected whenever a backend Service exists,
    including for local cluster NodePort previews.
    """
    from app.services.manifest_deploy import ManifestDeployer

    provisioner = MagicMock()
    provisioner.remote_cluster = False
    provisioner.container_build_platform.return_value = "linux/amd64"
    settings = Settings(kubernetes_enabled=True, default_workload_image="app:latest")
    deployer = ManifestDeployer(provisioner=provisioner, settings=settings)

    (tmp_path / "package.json").write_text("{}")
    manifests_dir = tmp_path / "infra" / "k8s" / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    (manifests_dir / "services.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web-frontend\nspec:\n"
        "  selector:\n    matchLabels:\n      app: web\n  template:\n    metadata:\n"
        "      labels:\n        app: web\n    spec:\n      containers:\n      - name: app\n"
        "        image: nginx:alpine\n"
        "---\n"
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: api-backend\nspec:\n"
        "  ports:\n  - port: 8080\n"
    )

    applied_documents: list[list[dict]] = []

    monkeypatch.setattr(
        "app.services.manifest_deploy.build_and_load_kind_images", lambda *a, **k: None
    )
    monkeypatch.setattr(
        deployer, "_apply_documents", lambda namespace, documents: applied_documents.append(documents)
    )
    monkeypatch.setattr(deployer, "_strip_preview_incompatible_controllers", lambda **_: None)
    monkeypatch.setattr(provisioner, "list_allocated_node_ports", lambda **_: set())
    monkeypatch.setattr(provisioner, "read_namespaced_app_node_port", lambda _ns: None)

    deployer.deploy(
        workspace_root=tmp_path,
        namespace="launchpad-env-test",
        environment_id="env-test",
        name="test-app",
        git_branch="main",
        git_repo_url="https://github.com/acme/test.git",
        ttl_expires_at=None,
        cloud_provider="local",
        frontend_api_path="/api",
    )

    assert len(applied_documents) == 1
    docs = applied_documents[0]
    # Check that ConfigMap for Nginx API proxy was appended
    configmaps = [d for d in docs if isinstance(d, dict) and d.get("kind") == "ConfigMap" and "proxy" in d.get("metadata", {}).get("name", "")]
    assert len(configmaps) >= 1
    cm_data = configmaps[0].get("data", {}).get("default.conf", "")
    assert "proxy_pass http://api-backend:8080/;" in cm_data


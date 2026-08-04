"""Kubernetes provisioner workload spec tests."""

from __future__ import annotations

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

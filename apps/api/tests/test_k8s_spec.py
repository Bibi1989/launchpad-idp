"""Tests for shared Kubernetes spec and manifest deploy helpers."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.services.k8s_spec import (
    LIMIT_RANGE_NAME,
    QUOTA_NAME,
    render_limit_range_yaml,
    render_resource_quota_yaml,
    workspace_governance_quota_hard,
)
from app.services.manifest_deploy import (
    load_manifest_documents,
    patch_manifest_documents,
    workspace_has_raw_manifests,
)


def test_workspace_quota_uses_settings() -> None:
    settings = Settings(
        kubernetes_cpu_request="500m",
        kubernetes_memory_request="512Mi",
        kubernetes_cpu_limit="2",
        kubernetes_memory_limit="2Gi",
        kubernetes_pod_limit="5",
    )
    hard = workspace_governance_quota_hard(settings)
    assert hard["requests.cpu"] == "500m"
    assert hard["pods"] == "5"


def test_governance_yaml_contains_shared_names() -> None:
    settings = Settings()
    quota = render_resource_quota_yaml(
        namespace="lp-demo",
        environment_name="demo",
        settings=settings,
    )
    limits = render_limit_range_yaml(namespace="lp-demo", environment_name="demo")
    assert QUOTA_NAME in quota
    assert LIMIT_RANGE_NAME in limits


def test_patch_manifest_documents_injects_run_as_user_for_nginx() -> None:
    docs = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "app", "namespace": "lp-old"},
            "spec": {
                "template": {
                    "spec": {
                        "securityContext": {"runAsNonRoot": True},
                        "containers": [
                            {
                                "name": "app",
                                "image": "nginx:1.27-alpine",
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                },
                            }
                        ],
                    }
                }
            },
        }
    ]
    patched = patch_manifest_documents(
        docs,
        target_namespace="launchpad-env-abc",
        environment_id="abc",
        name="preview",
        git_branch="main",
        git_repo_url="https://launchpad.local/workspaces/ws",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev@example.com",
        image="nginx:1.27-alpine",
    )
    pod_spec = patched[0]["spec"]["template"]["spec"]
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert pod_spec["securityContext"]["runAsUser"] == 101
    assert pod_spec["securityContext"]["runAsGroup"] == 101
    assert pod_spec["containers"][0]["securityContext"]["runAsUser"] == 101

    docs = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "app", "namespace": "lp-old"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "app", "image": "nginx:1.27-alpine"}],
                    }
                }
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": "lp-old"},
        },
        {
            "apiVersion": "v1",
            "kind": "LimitRange",
            "metadata": {"name": LIMIT_RANGE_NAME, "namespace": "lp-old"},
            "spec": {"limits": []},
        },
        {
            "apiVersion": "v1",
            "kind": "ResourceQuota",
            "metadata": {"name": QUOTA_NAME, "namespace": "lp-old"},
            "spec": {"hard": {"pods": "10"}},
        },
    ]
    patched = patch_manifest_documents(
        docs,
        target_namespace="launchpad-env-abc",
        environment_id="abc",
        name="preview",
        git_branch="feature/x",
        git_repo_url="https://github.com/acme/app.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev@example.com",
        image="hashicorp/http-echo:0.2.3",
    )
    assert len(patched) == 1
    assert patched[0]["kind"] == "Deployment"
    deployment = patched[0]
    assert deployment["metadata"]["namespace"] == "launchpad-env-abc"
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["template"]["spec"]["containers"][0]["image"] == "hashicorp/http-echo:0.2.3"
    env = {
        item["name"]: item["value"]
        for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["GIT_REPO_URL"] == "https://github.com/acme/app.git"
    assert env["GIT_BRANCH"] == "feature/x"


def test_workspace_has_raw_manifests(tmp_path: Path) -> None:
    from app.services.manifest_deploy import (
        load_manifest_documents,
        workspace_has_helm_chart,
        workspace_has_raw_manifests,
    )

    manifest_dir = tmp_path / "infra" / "k8s" / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\n",
        encoding="utf-8",
    )
    assert workspace_has_raw_manifests(tmp_path) is True
    assert workspace_has_helm_chart(tmp_path) is False
    docs = load_manifest_documents(tmp_path)
    assert len(docs) == 1
    assert docs[0]["kind"] == "Deployment"


def test_load_workspace_manifest_documents_renders_helm_chart(tmp_path: Path) -> None:
    import shutil

    from app.services.manifest_deploy import (
        load_workspace_manifest_documents,
        workspace_has_deployable_k8s,
        workspace_has_helm_chart,
    )

    src = Path("/tmp/launchpad-workspaces/demo/infra/helm/app-chart")
    if not (src / "Chart.yaml").is_file():
        pytest.skip("demo helm chart not available")
    if shutil.which("helm") is None:
        pytest.skip("helm CLI not available")

    dest = tmp_path / "infra" / "helm" / "app-chart"
    dest.parent.mkdir(parents=True)
    shutil.copytree(src, dest)
    assert workspace_has_helm_chart(tmp_path) is True
    assert workspace_has_deployable_k8s(tmp_path) is True

    docs = load_workspace_manifest_documents(tmp_path, namespace="lp-test")
    kinds = {doc.get("kind") for doc in docs}
    assert "Deployment" in kinds
    assert "Service" in kinds
    deployment = next(doc for doc in docs if doc.get("kind") == "Deployment")
    assert deployment["metadata"]["name"] == "app"
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    assert "bibi1989/afroshopclient" in image


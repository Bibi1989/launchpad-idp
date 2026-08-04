"""Preview deploy must apply the app image only to the app workload.

Regression tests for the bug where the resolved application image (e.g.
``app:latest``) was patched onto in-cluster datastore Deployments (postgres,
redis, …), causing ``ErrImagePull`` on the datastore pods, and where the
datastore Service ports were rewritten to the app port.
"""

from __future__ import annotations

import copy

from app.services.manifest_deploy import (
    patch_manifest_documents,
    resolve_manifest_workload_image,
)


def _workspace_docs() -> list[dict]:
    return [
        {
            "kind": "Deployment",
            "metadata": {"name": "app"},
            "spec": {
                "template": {
                    "spec": {"containers": [
                        {"name": "app", "image": "myapp:latest",
                         "ports": [{"containerPort": 8000}]}
                    ]}
                }
            },
        },
        {
            "kind": "Deployment",
            "metadata": {"name": "postgres", "labels": {"launchpad.io/component": "datastore"}},
            "spec": {
                "template": {
                    "spec": {"containers": [
                        {"name": "postgres", "image": "postgres:16-alpine",
                         "ports": [{"containerPort": 5432}]}
                    ]}
                }
            },
        },
        {
            "kind": "Deployment",
            "metadata": {"name": "redis", "labels": {"launchpad.io/component": "datastore"}},
            "spec": {
                "template": {
                    "spec": {"containers": [
                        {"name": "redis", "image": "redis:7-alpine",
                         "ports": [{"containerPort": 6379}]}
                    ]}
                }
            },
        },
        {
            "kind": "Service",
            "metadata": {"name": "postgres"},
            "spec": {"type": "ClusterIP", "ports": [{"port": 5432, "targetPort": 5432}]},
        },
        {
            "kind": "Service",
            "metadata": {"name": "app"},
            "spec": {"type": "ClusterIP", "ports": [{"port": 80, "targetPort": "http"}]},
        },
    ]


def _patched():
    return patch_manifest_documents(
        copy.deepcopy(_workspace_docs()),
        target_namespace="lp-demo",
        environment_id="env1",
        name="demo",
        git_branch="main",
        git_repo_url="http://example/repo",
        ttl_expires_at="2026-01-01T00:00:00Z",
        owner_label="user",
        image="myapp:latest",
    )


def test_resolve_image_prefers_app_deployment_not_datastore() -> None:
    img = resolve_manifest_workload_image(
        copy.deepcopy(_workspace_docs()),
        provided_image=None,
        default_image="nginx:1.27-alpine",
    )
    assert img == "myapp:latest"


def test_app_deployment_gets_app_image() -> None:
    patched = {(d["kind"], d["metadata"]["name"]): d for d in _patched()}
    app = patched[("Deployment", "app")]
    assert app["spec"]["template"]["spec"]["containers"][0]["image"] == "myapp:latest"


def test_datastore_deployments_keep_their_own_image() -> None:
    patched = {(d["kind"], d["metadata"]["name"]): d for d in _patched()}
    pg = patched[("Deployment", "postgres")]
    rd = patched[("Deployment", "redis")]
    assert pg["spec"]["template"]["spec"]["containers"][0]["image"] == "postgres:16-alpine"
    assert rd["spec"]["template"]["spec"]["containers"][0]["image"] == "redis:7-alpine"


def test_datastore_deployments_are_pinned_to_target_namespace() -> None:
    patched = {(d["kind"], d["metadata"]["name"]): d for d in _patched()}
    assert patched[("Deployment", "postgres")]["metadata"]["namespace"] == "lp-demo"


def test_datastore_service_ports_are_not_rewritten() -> None:
    patched = {(d["kind"], d["metadata"]["name"]): d for d in _patched()}
    pg_svc = patched[("Service", "postgres")]
    assert pg_svc["spec"]["ports"][0]["targetPort"] == 5432


def test_datastore_selector_not_forced_to_app() -> None:
    # postgres Deployment must not be given the app workload selector/name.
    patched = {(d["kind"], d["metadata"]["name"]): d for d in _patched()}
    pg = patched[("Deployment", "postgres")]
    assert pg["metadata"]["name"] == "postgres"
    container = pg["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "postgres"


def test_resolve_preview_target_single_app() -> None:
    from app.services.manifest_deploy import _resolve_preview_target

    docs = [
        {"kind": "Deployment", "metadata": {"name": "app"},
         "spec": {"selector": {"matchLabels": {"app": "app"}},
                  "template": {"metadata": {"labels": {"app": "app"}},
                               "spec": {"containers": [{"image": "svc:latest",
                                        "ports": [{"name": "http", "containerPort": 8000}]}]}}}},
        {"kind": "Deployment", "metadata": {"name": "postgres", "labels": {"launchpad.io/component": "datastore"}},
         "spec": {"selector": {"matchLabels": {"app": "postgres"}},
                  "template": {"spec": {"containers": [{"image": "postgres:16-alpine"}]}}}},
    ]
    assert _resolve_preview_target(docs) == ("app", 8000)


def test_resolve_preview_target_multi_service_picks_exposed() -> None:
    from app.services.manifest_deploy import _resolve_preview_target

    docs = [
        {"kind": "Deployment", "metadata": {"name": "launch-server"},
         "spec": {"selector": {"matchLabels": {"app": "server"}},
                  "template": {"metadata": {"labels": {"app": "server"}},
                               "spec": {"containers": [{"image": "server:latest",
                                        "ports": [{"name": "http", "containerPort": 8000}]}]}}}},
        {"kind": "Deployment", "metadata": {"name": "launch-web",
                                            "annotations": {"launchpad.io/preview-target": "true"}},
         "spec": {"selector": {"matchLabels": {"app": "web"}},
                  "template": {"metadata": {"labels": {"app": "web"}},
                               "spec": {"containers": [{"image": "web:latest",
                                        "ports": [{"name": "http", "containerPort": 8080}]}]}}}},
    ]
    # NodePort must select the exposed web pods (not the alphabetically-first server),
    # otherwise the Service has no endpoints -> ERR_CONNECTION_RESET.
    assert _resolve_preview_target(docs) == ("web", 8080)

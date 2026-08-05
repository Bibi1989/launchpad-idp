"""Governance skip + FailToCreateError 409 handling for manifest deploy."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException
from kubernetes.utils import FailToCreateError

from app.core.config import Settings
from app.services.k8s_spec import LIMIT_RANGE_NAME, QUOTA_NAME
from app.services.kubernetes import KubernetesProvisioner, ProvisionedResources
from app.services.manifest_deploy import (
    ManifestDeployer,
    _all_already_exist,
    _is_already_exists_status,
    _is_preview_governance_document,
    _requires_dynamic_apply,
    patch_manifest_documents,
    resolve_manifest_workload_image,
)


def _limit_range_doc(*, name: str = LIMIT_RANGE_NAME) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "LimitRange",
        "metadata": {"name": name, "namespace": "lp-old"},
        "spec": {"limits": []},
    }


def _quota_doc(*, name: str = QUOTA_NAME) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": {"name": name, "namespace": "lp-old"},
        "spec": {"hard": {"pods": "10"}},
    }


def _deployment_doc() -> dict[str, Any]:
    return {
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
    }


def test_is_preview_governance_document_matches_scaffold_names() -> None:
    assert _is_preview_governance_document(_limit_range_doc()) is True
    assert _is_preview_governance_document(_quota_doc()) is True
    assert _is_preview_governance_document(_limit_range_doc(name="custom-limits")) is False
    assert _is_preview_governance_document(_quota_doc(name="custom-quota")) is False
    assert _is_preview_governance_document(_deployment_doc()) is False


def test_is_preview_governance_document_is_case_insensitive_for_kind() -> None:
    doc = _limit_range_doc()
    doc["kind"] = "limitrange"
    assert _is_preview_governance_document(doc) is True


def test_patch_manifest_documents_skips_namespace_and_scaffold_governance() -> None:
    docs = [
        _deployment_doc(),
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "lp-old"}},
        _limit_range_doc(),
        _quota_doc(),
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "app", "namespace": "lp-old"},
            "spec": {"ports": [{"port": 80}]},
        },
    ]
    patched = patch_manifest_documents(
        docs,
        target_namespace="launchpad-env-abc",
        environment_id="abc",
        name="preview",
        git_branch="main",
        git_repo_url="https://github.com/acme/app.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev@example.com",
        image="hashicorp/http-echo:0.2.3",
    )
    kinds = [doc["kind"] for doc in patched]
    assert kinds == ["Deployment", "Service"]
    assert all(doc["metadata"]["namespace"] == "launchpad-env-abc" for doc in patched)


def test_all_already_exist_detects_fail_to_create_409() -> None:
    exc = ApiException(status=409, reason="Conflict")
    exc.body = (
        '{"kind":"Status","apiVersion":"v1","metadata":{},"status":"Failure",'
        '"message":"limitranges \\"launchpad-defaults\\" already exists",'
        '"reason":"AlreadyExists","details":{"name":"launchpad-defaults",'
        '"kind":"limitranges"},"code":409}'
    )
    assert _all_already_exist(FailToCreateError([exc])) is True


def test_all_already_exist_accepts_string_status_and_body_reason() -> None:
    exc = ApiException(status="409", reason="Conflict")
    exc.body = '{"reason":"AlreadyExists","message":"already exists","code":409}'
    assert _all_already_exist(FailToCreateError([exc])) is True

    bare = ApiException(status=None, reason="Conflict")
    bare.body = '{"reason":"Conflict","message":"object has been modified"}'
    assert _is_already_exists_status(None, bare) is False


def test_apply_documents_skips_scaffold_limit_range_without_create() -> None:
    deployer = ManifestDeployer(Settings(kubernetes_enabled=True))
    with patch("kubernetes.client.ApiClient"), patch(
        "kubernetes.utils.create_from_dict"
    ) as create_from_dict:
        deployer._apply_documents(
            namespace="launchpad-env-abc",
            documents=[_limit_range_doc(), _deployment_doc()],
        )
        assert create_from_dict.call_count == 1
        assert create_from_dict.call_args.kwargs["data"]["kind"] == "Deployment"


def test_apply_documents_treats_fail_to_create_409_as_replace() -> None:
    deployer = ManifestDeployer(Settings(kubernetes_enabled=True))
    conflict = ApiException(status=409, reason="Conflict")
    conflict.body = (
        '{"message":"services \\"app\\" already exists","reason":"AlreadyExists","code":409}'
    )
    service_doc = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "app", "namespace": "launchpad-env-abc"},
        "spec": {"ports": [{"port": 80}]},
    }
    with (
        patch("kubernetes.client.ApiClient"),
        patch(
            "kubernetes.utils.create_from_dict",
            side_effect=FailToCreateError([conflict]),
        ),
        patch.object(deployer, "_replace_document") as replace_document,
    ):
        deployer._apply_documents(
            namespace="launchpad-env-abc",
            documents=[service_doc],
        )
        replace_document.assert_called_once()
        assert replace_document.call_args.kwargs["doc"]["kind"] == "Service"


def test_apply_documents_ignores_governance_409_without_replace() -> None:
    deployer = ManifestDeployer(Settings(kubernetes_enabled=True))
    conflict = ApiException(status=409, reason="Conflict")
    conflict.body = (
        '{"message":"limitranges \\"launchpad-defaults\\" already exists",'
        '"reason":"AlreadyExists","code":409}'
    )
    with (
        patch("kubernetes.client.ApiClient"),
        patch(
            "kubernetes.utils.create_from_dict",
            side_effect=FailToCreateError([conflict]),
        ) as create_from_dict,
        patch.object(deployer, "_replace_document") as replace_document,
        patch(
            "app.services.manifest_deploy._is_preview_governance_document",
            side_effect=[False, True],
        ),
    ):
        # First check in loop returns False so create runs; handler check returns True.
        deployer._apply_documents(
            namespace="launchpad-env-abc",
            documents=[_limit_range_doc()],
        )
        create_from_dict.assert_called_once()
        replace_document.assert_not_called()


def test_apply_governance_create_409_falls_back_to_replace() -> None:
    settings = Settings(kubernetes_enabled=True)
    provisioner = KubernetesProvisioner(settings)
    core = MagicMock()
    networking = MagicMock()
    provisioner._core = core
    provisioner._networking = networking

    not_found = ApiException(status=404)
    conflict = ApiException(status=409, reason="Conflict")
    conflict.body = '{"reason":"AlreadyExists","code":409}'

    existing_quota = SimpleNamespace(metadata=SimpleNamespace(resource_version="11"))
    existing_limit = SimpleNamespace(metadata=SimpleNamespace(resource_version="12"))
    existing_policy = SimpleNamespace(metadata=SimpleNamespace(resource_version="13"))

    core.read_namespace.side_effect = not_found
    core.create_namespace.return_value = None

    core.read_namespaced_resource_quota.side_effect = [not_found, existing_quota]
    core.create_namespaced_resource_quota.side_effect = conflict

    core.read_namespaced_limit_range.side_effect = [not_found, existing_limit]
    core.create_namespaced_limit_range.side_effect = conflict

    networking.read_namespaced_network_policy.side_effect = [not_found, existing_policy]
    networking.create_namespaced_network_policy.side_effect = conflict
    networking.delete_namespaced_network_policy.side_effect = ApiException(status=404)

    resources = ProvisionedResources(namespace="launchpad-env-abc")
    provisioner.apply_governance(
        namespace="launchpad-env-abc",
        labels={"app": "app"},
        resources=resources,
    )

    core.replace_namespaced_resource_quota.assert_called_once()
    core.replace_namespaced_limit_range.assert_called_once()
    networking.replace_namespaced_network_policy.assert_called_once()
    assert resources.created_quota is False
    assert resources.created_limit_range is False
    assert resources.created_network_policy is False


def test_apply_governance_replace_uses_existing_resource_version() -> None:
    settings = Settings(kubernetes_enabled=True)
    provisioner = KubernetesProvisioner(settings)
    core = MagicMock()
    networking = MagicMock()
    provisioner._core = core
    provisioner._networking = networking

    core.read_namespace.return_value = SimpleNamespace()
    core.read_namespaced_resource_quota.return_value = SimpleNamespace(
        metadata=SimpleNamespace(resource_version="42")
    )
    core.read_namespaced_limit_range.return_value = SimpleNamespace(
        metadata=SimpleNamespace(resource_version="43")
    )
    networking.read_namespaced_network_policy.return_value = SimpleNamespace(
        metadata=SimpleNamespace(resource_version="44")
    )
    networking.delete_namespaced_network_policy.side_effect = ApiException(status=404)

    resources = ProvisionedResources(namespace="launchpad-env-abc")
    provisioner.apply_governance(
        namespace="launchpad-env-abc",
        labels={"app": "app"},
        resources=resources,
    )

    replaced_quota = core.replace_namespaced_resource_quota.call_args.args[2]
    assert replaced_quota.metadata.resource_version == "42"
    replaced_limit = core.replace_namespaced_limit_range.call_args.args[2]
    assert replaced_limit.metadata.resource_version == "43"
    core.create_namespaced_limit_range.assert_not_called()


def test_requires_dynamic_apply_for_vpa() -> None:
    assert (
        _requires_dynamic_apply(
            {
                "apiVersion": "autoscaling.k8s.io/v1",
                "kind": "VerticalPodAutoscaler",
                "metadata": {"name": "app"},
            }
        )
        is True
    )
    assert _requires_dynamic_apply(_deployment_doc()) is False


def test_apply_documents_skips_vpa_for_preview() -> None:
    deployer = ManifestDeployer(Settings(kubernetes_enabled=True))
    vpa_doc = {
        "apiVersion": "autoscaling.k8s.io/v1",
        "kind": "VerticalPodAutoscaler",
        "metadata": {"name": "app", "namespace": "launchpad-env-abc"},
        "spec": {"updatePolicy": {"updateMode": "Off"}},
    }
    with (
        patch("kubernetes.client.ApiClient"),
        patch("kubernetes.utils.create_from_dict") as create_from_dict,
        patch.object(deployer, "_create_via_dynamic", return_value=True) as create_dynamic,
    ):
        deployer._apply_documents(
            namespace="launchpad-env-abc",
            documents=[vpa_doc, _deployment_doc()],
        )
        create_dynamic.assert_not_called()
        assert create_from_dict.call_count == 1
        assert create_from_dict.call_args.kwargs["data"]["kind"] == "Deployment"


def test_apply_documents_routes_dynamic_kinds_through_dynamic_client() -> None:
    deployer = ManifestDeployer(Settings(kubernetes_enabled=True))
    policy_doc = {
        "apiVersion": "policy/v1",
        "kind": "PodDisruptionBudget",
        "metadata": {"name": "app", "namespace": "launchpad-env-abc"},
        "spec": {"minAvailable": 1},
    }
    with (
        patch("kubernetes.client.ApiClient"),
        patch("kubernetes.utils.create_from_dict") as create_from_dict,
        patch.object(deployer, "_create_via_dynamic", return_value=True) as create_dynamic,
    ):
        from app.services import manifest_deploy as md

        original = md._requires_dynamic_apply

        def _force_dynamic(doc: dict) -> bool:
            return doc.get("kind") == "PodDisruptionBudget" or original(doc)

        with patch.object(md, "_requires_dynamic_apply", side_effect=_force_dynamic):
            deployer._apply_documents(
                namespace="launchpad-env-abc",
                documents=[policy_doc, _deployment_doc()],
            )
        create_dynamic.assert_called_once()
        assert create_dynamic.call_args.kwargs["doc"]["kind"] == "PodDisruptionBudget"
        assert create_from_dict.call_count == 1
        assert create_from_dict.call_args.kwargs["data"]["kind"] == "Deployment"


def test_apply_documents_skips_optional_vpa_when_crd_missing() -> None:
    deployer = ManifestDeployer(Settings(kubernetes_enabled=True))
    vpa_doc = {
        "apiVersion": "autoscaling.k8s.io/v1",
        "kind": "VerticalPodAutoscaler",
        "metadata": {"name": "app", "namespace": "launchpad-env-abc"},
        "spec": {"updatePolicy": {"updateMode": "Off"}},
    }
    with (
        patch("kubernetes.client.ApiClient"),
        patch("kubernetes.utils.create_from_dict") as create_from_dict,
        patch.object(deployer, "_create_via_dynamic", return_value=False),
    ):
        deployer._apply_documents(
            namespace="launchpad-env-abc",
            documents=[vpa_doc, _deployment_doc()],
        )
        # VPA is preview-skipped entirely now (no metrics / CRD on kind).
        assert create_from_dict.call_count == 1
        assert create_from_dict.call_args.kwargs["data"]["kind"] == "Deployment"


def test_patch_service_for_preview_uses_cluster_ip_until_mapped() -> None:
    from app.services.manifest_deploy import _patch_service_for_preview

    doc = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "app"},
        "spec": {
            "type": "NodePort",
            "ports": [{"port": 80, "targetPort": 80, "nodePort": 31196}],
        },
    }
    _patch_service_for_preview(doc)
    assert doc["spec"]["type"] == "ClusterIP"
    assert "nodePort" not in doc["spec"]["ports"][0]


def test_assign_node_port_recreates_when_missing_after_delete() -> None:
    """Regression: delete-then-create must not soft-return on 404 mid-loop."""
    from kubernetes.client.rest import ApiException

    deployer = ManifestDeployer(Settings(kubernetes_enabled=True, preview_node_port_min=30080, preview_node_port_max=30084))
    core = MagicMock()
    deployer._provisioner._core = core

    missing = ApiException(status=404)
    created = MagicMock()

    # First candidate: service exists → delete → create fails (collision)
    # Second candidate: service missing → must still create (not return)
    svc_existing = MagicMock()
    svc_existing.spec.type = "ClusterIP"
    svc_existing.spec.ports = [MagicMock(node_port=None)]

    core.read_namespaced_service.side_effect = [svc_existing, missing]
    core.delete_namespaced_service.return_value = None
    core.create_namespaced_service.side_effect = [
        ApiException(status=422, reason="nodePort already allocated"),
        created,
    ]

    with patch(
        "app.services.kubernetes._is_node_port_allocated_error",
        side_effect=lambda exc: "already allocated" in str(getattr(exc, "reason", "")),
    ):
        applied = deployer._assign_node_port(
            namespace="ns",
            node_port=30080,
            used_ports=set(),
            labels={"app": "app"},
        )

    assert applied == 30081
    assert core.create_namespaced_service.call_count == 2


def test_resolve_manifest_workload_image_prefers_deployment_over_default() -> None:
    from app.services.manifest_deploy import resolve_manifest_workload_image

    docs = [
        {
            "kind": "Deployment",
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "app", "image": "tiangolo/node-frontend:latest"}]
                    }
                }
            },
        }
    ]
    assert (
        resolve_manifest_workload_image(
            docs,
            provided_image="nginx:1.27-alpine",
            default_image="nginx:1.27-alpine",
        )
        == "tiangolo/node-frontend:latest"
    )


def test_resolve_manifest_workload_image_kind_is_case_insensitive() -> None:
    from app.services.manifest_deploy import resolve_manifest_workload_image

    docs = [
        {
            "kind": "deployment",
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "app", "image": "tiangolo/node-frontend:latest"}]
                    }
                }
            },
        }
    ]

    # If kind parsing is case-sensitive, this would fall back to `default_image` (nginx).
    assert (
        resolve_manifest_workload_image(
            docs,
            provided_image="nginx:1.27-alpine",
            default_image="nginx:1.27-alpine",
        )
        == "tiangolo/node-frontend:latest"
    )


def test_resolve_manifest_workload_image_prefers_non_default_override() -> None:
    from app.services.manifest_deploy import resolve_manifest_workload_image

    docs = [
        {
            "kind": "Deployment",
            "spec": {
                "template": {
                    "spec": {"containers": [{"name": "app", "image": "tiangolo/node-frontend:latest"}]}
                }
            },
        }
    ]
    assert (
        resolve_manifest_workload_image(
            docs,
            provided_image="launchpad-preview/abc:deadbeef",
            default_image="nginx:1.27-alpine",
        )
        == "launchpad-preview/abc:deadbeef"
    )


def test_patch_manifest_documents_keeps_resolved_custom_image() -> None:
    from app.services.manifest_deploy import resolve_manifest_workload_image

    docs = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "app", "namespace": "lp-old"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "app", "image": "tiangolo/node-frontend:latest"}]
                    }
                }
            },
        }
    ]
    resolved = resolve_manifest_workload_image(
        docs,
        provided_image="nginx:1.27-alpine",
        default_image="nginx:1.27-alpine",
    )
    patched = patch_manifest_documents(
        docs,
        target_namespace="launchpad-env-abc",
        environment_id="abc",
        name="preview",
        git_branch="main",
        git_repo_url="https://launchpad.local/workspaces/ws",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev@example.com",
        image=resolved,
    )
    assert patched[0]["spec"]["template"]["spec"]["containers"][0]["image"] == (
        "tiangolo/node-frontend:latest"
    )


def test_patch_deployment_uses_recreate_and_clears_nginx_uid_for_custom_images() -> None:
    docs = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "app"},
            "spec": {
                "template": {
                    "spec": {
                        "securityContext": {"runAsNonRoot": True, "runAsUser": 101},
                        "containers": [
                            {
                                "name": "app",
                                "image": "old",
                                "securityContext": {
                                    "runAsNonRoot": True,
                                    "runAsUser": 101,
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
        target_namespace="ns",
        environment_id="abc",
        name="preview",
        git_branch="main",
        git_repo_url="https://example.com/app.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev",
        image="dhi.io/build:2-source",
    )
    spec = patched[0]["spec"]
    assert spec["strategy"]["type"] == "RollingUpdate"
    assert spec["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
    assert spec["strategy"]["rollingUpdate"]["maxSurge"] == 1
    container = spec["template"]["spec"]["containers"][0]
    assert container["image"] == "dhi.io/build:2-source"
    assert "runAsNonRoot" not in spec["template"]["spec"]["securityContext"]
    assert "runAsNonRoot" not in container["securityContext"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is False
    assert spec["selector"]["matchLabels"]["app"] == "app"
    assert spec["selector"]["matchLabels"]["launchpad.io/managed-by"] == "launchpad-idp"
    assert spec["template"]["metadata"]["labels"]["app"] == "app"


def test_should_recreate_immutable_selector_error() -> None:
    from app.services.manifest_deploy import _should_recreate_immutable_resource

    class _Exc(Exception):
        status = 422
        body = (
            'Deployment.apps "app" is invalid: spec.selector: Invalid value: '
            '{"matchLabels":{"app":"app"}}: field is immutable'
        )

    assert _should_recreate_immutable_resource("Deployment", _Exc()) is True
    assert _should_recreate_immutable_resource("Service", _Exc()) is False


def test_resolve_workload_listen_port_prefers_image_expose_over_scaffold_80() -> None:
    from app.services.manifest_deploy import resolve_workload_listen_port

    assert (
        resolve_workload_listen_port(
            image="bibi1989/afroshopclient:1.0",
            manifest_port=80,
            exposed_ports=[5000],
        )
        == 5000
    )
    assert (
        resolve_workload_listen_port(
            image="custom:1",
            manifest_port=3000,
            exposed_ports=[5000],
        )
        == 3000
    )
    assert (
        resolve_workload_listen_port(
            image="custom:1",
            manifest_port=80,
            exposed_ports=[],
            service_target_port=5000,
        )
        == 5000
    )


def test_patch_manifest_documents_aligns_probes_and_service_to_expose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.manifest_deploy as md

    monkeypatch.setattr(md, "inspect_image_exposed_ports", lambda image: [5000])
    docs = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "app"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "image": "bibi1989/afroshopclient:1.0",
                                "ports": [{"name": "http", "containerPort": 80}],
                                "readinessProbe": {"httpGet": {"path": "/", "port": "http"}},
                                "livenessProbe": {"httpGet": {"path": "/", "port": "http"}},
                            }
                        ]
                    }
                }
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "app"},
            "spec": {"ports": [{"port": 80, "targetPort": "http"}]},
        },
    ]
    patched = patch_manifest_documents(
        docs,
        target_namespace="ns",
        environment_id="abc",
        name="preview",
        git_branch="main",
        git_repo_url="https://example.com/app.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev",
        image="bibi1989/afroshopclient:1.0",
    )
    container = patched[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["ports"][0]["containerPort"] == 5000
    assert "httpGet" not in container["readinessProbe"]
    assert container["readinessProbe"]["tcpSocket"]["port"] == 5000
    assert container["readinessProbe"]["initialDelaySeconds"] == 5
    assert container["readinessProbe"]["failureThreshold"] == 12
    assert "httpGet" not in container["livenessProbe"]
    assert container["livenessProbe"]["tcpSocket"]["port"] == 5000
    assert container["livenessProbe"]["initialDelaySeconds"] == 120
    assert container["startupProbe"]["tcpSocket"]["port"] == 5000
    assert container["startupProbe"]["failureThreshold"] == 48
    assert container["resources"]["requests"]["memory"] == "256Mi"
    assert container["resources"]["limits"]["memory"] == "768Mi"
    assert container["resources"]["limits"]["cpu"] == "500m"
    env = {item["name"]: item["value"] for item in container["env"]}
    assert env["PORT"] == "5000"
    assert env["HOST"] == "0.0.0.0"
    assert patched[1]["spec"]["ports"][0]["targetPort"] == 5000


def test_patch_manifest_documents_skips_hpa_for_preview() -> None:
    docs = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "app"},
            "spec": {"template": {"spec": {"containers": [{"name": "app", "image": "nginx:1.27-alpine"}]}}},
        },
        {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": "app"},
            "spec": {"minReplicas": 1, "maxReplicas": 4},
        },
    ]
    patched = patch_manifest_documents(
        docs,
        target_namespace="ns",
        environment_id="abc",
        name="preview",
        git_branch="main",
        git_repo_url="https://example.com/app.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev",
        image="nginx:1.27-alpine",
    )
    assert [doc["kind"] for doc in patched] == ["Deployment"]
    assert not any(doc.get("kind") == "HorizontalPodAutoscaler" for doc in patched)


def test_patch_keeps_http_probes_for_nginx_images() -> None:
    docs = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "app"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "image": "nginx:1.27-alpine",
                                "ports": [{"name": "http", "containerPort": 80}],
                                "readinessProbe": {"httpGet": {"path": "/", "port": "http"}},
                                "livenessProbe": {"httpGet": {"path": "/", "port": "http"}},
                            }
                        ]
                    }
                }
            },
        }
    ]
    patched = patch_manifest_documents(
        docs,
        target_namespace="ns",
        environment_id="abc",
        name="preview",
        git_branch="main",
        git_repo_url="https://example.com/app.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev",
        image="nginx:1.27-alpine",
    )
    container = patched[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["readinessProbe"]["httpGet"]["port"] == 80
    assert "tcpSocket" not in container["readinessProbe"]
    assert "startupProbe" not in container
    env_names = {item["name"] for item in container.get("env", [])}
    assert "HOST" not in env_names


def test_patch_keeps_nginx_scaffold_resources() -> None:
    docs = [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "app"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "image": "nginx:1.27-alpine",
                                "ports": [{"containerPort": 80}],
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "128Mi"},
                                    "limits": {"cpu": "250m", "memory": "256Mi"},
                                },
                            }
                        ]
                    }
                }
            },
        }
    ]
    patched = patch_manifest_documents(
        docs,
        target_namespace="ns",
        environment_id="abc",
        name="preview",
        git_branch="main",
        git_repo_url="https://example.com/app.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev",
        image="nginx:1.27-alpine",
    )
    container = patched[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"]["limits"]["memory"] == "256Mi"
    assert container["resources"]["requests"]["memory"] == "128Mi"


def test_resolve_manifest_workload_image_prefers_manifest_custom_image_over_default() -> None:
    docs = [
        {
            "kind": "Deployment",
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "app", "image": "bibi1989/afroshopclient:1.0"}]
                    }
                }
            },
        }
    ]
    resolved = resolve_manifest_workload_image(
        docs,
        provided_image="nginx:1.27-alpine",
        default_image="nginx:1.27-alpine",
    )
    assert resolved == "bibi1989/afroshopclient:1.0"


def test_patch_manifest_documents_sets_if_not_present_for_tagged_custom_image() -> None:
    docs = [
        {
            "kind": "Deployment",
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "app", "image": "bibi1989/afroshopclient:1.0"}]
                    }
                }
            },
        }
    ]
    patched = patch_manifest_documents(
        docs,
        target_namespace="ns",
        environment_id="abc",
        name="preview",
        git_branch="main",
        git_repo_url="https://example.com/app.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev",
        image="bibi1989/afroshopclient:1.0",
    )
    container = patched[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "bibi1989/afroshopclient:1.0"
    assert container["imagePullPolicy"] == "IfNotPresent"


def test_patch_launch_workload_stamps_pod_managed_by_label() -> None:
    docs = [
        {
            "kind": "Deployment",
            "metadata": {"name": "launch-web"},
            "spec": {
                "selector": {"matchLabels": {"app": "launch-web"}},
                "template": {
                    "metadata": {"labels": {"app": "launch-web"}},
                    "spec": {
                        "containers": [
                            {"name": "launch-web", "image": "launch-web:latest", "ports": [{"containerPort": 8080}]}
                        ]
                    },
                },
            },
        },
        {
            "kind": "Service",
            "metadata": {"name": "launch-web-service"},
            "spec": {
                "selector": {"app": "launch-web"},
                "ports": [{"port": 8080, "targetPort": 8080}],
            },
        },
    ]
    patched = patch_manifest_documents(
        docs,
        target_namespace="ns",
        environment_id="abc",
        name="preview",
        git_branch="main",
        git_repo_url="https://example.com/app.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev",
        image="nginx:1.27-alpine",
    )
    dep = next(d for d in patched if d["kind"] == "Deployment")
    pod_labels = dep["spec"]["template"]["metadata"]["labels"]
    assert pod_labels["app"] == "launch-web"
    assert pod_labels["launchpad.io/managed-by"] == "launchpad-idp"
    # Must not rewrite the stack image to the env default nginx.
    assert dep["spec"]["template"]["spec"]["containers"][0]["image"] == "launch-web:latest"


def test_resolve_preview_ingress_backend_prefers_launch_service() -> None:
    from app.services.manifest_deploy import _resolve_preview_ingress_backend

    docs = [
        {
            "kind": "Service",
            "metadata": {"name": "launch-web-service"},
            "spec": {"ports": [{"port": 8080}]},
        }
    ]
    name, port = _resolve_preview_ingress_backend(
        docs, preview_app_label="launch-web", listen_port=8080
    )
    assert name == "launch-web-service"
    assert port == 8080


def test_patch_rewrites_scaffold_ingress_host_and_skips_without_host() -> None:
    from app.services.manifest_deploy import patch_manifest_documents

    ingress = {
        "kind": "Ingress",
        "metadata": {
            "name": "launch-preview",
            "annotations": {
                "nginx.ingress.kubernetes.io/rewrite-target": "/",
            },
        },
        "spec": {
            "rules": [
                {
                    "host": "full.preview.127.0.0.1.nip.io",
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": "launch-nextjs-service",
                                        "port": {"number": 3000},
                                    }
                                },
                            }
                        ]
                    },
                }
            ]
        },
    }
    skipped = patch_manifest_documents(
        [ingress],
        target_namespace="ns-a",
        environment_id="aaa",
        name="full",
        git_branch="main",
        git_repo_url="https://example.com/app.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev",
        image="launch-nextjs:latest",
    )
    assert skipped == []

    patched = patch_manifest_documents(
        [ingress],
        target_namespace="ns-b",
        environment_id="bbb",
        name="full",
        git_branch="main",
        git_repo_url="https://example.com/app.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        owner_label="dev",
        image="launch-nextjs:latest",
        preview_host="ws-bbb.launchpad-idp.online",
    )
    assert len(patched) == 1
    assert patched[0]["spec"]["rules"][0]["host"] == "ws-bbb.launchpad-idp.online"
    assert patched[0]["metadata"]["namespace"] == "ns-b"
    assert "rewrite-target" not in (patched[0]["metadata"].get("annotations") or {})


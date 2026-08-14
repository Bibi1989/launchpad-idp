"""Tests for Kubernetes manifest image source / registry push wiring."""

from __future__ import annotations

from app.services.manifest_deploy import remap_manifest_image_references


def test_remap_manifest_image_references_updates_deployment() -> None:
    documents = [
        {
            "kind": "Deployment",
            "metadata": {"name": "web"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "web", "image": "launch-web:latest"}],
                    },
                },
            },
        },
    ]
    remap_manifest_image_references(
        documents,
        {"launch-web:latest": "123456789.dkr.ecr.us-east-1.amazonaws.com/launchpad-previews/launch-web-latest"},
    )
    image = documents[0]["spec"]["template"]["spec"]["containers"][0]["image"]
    assert "dkr.ecr.us-east-1.amazonaws.com" in image
    assert documents[0]["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] == "Always"


def test_image_name_from_tag_strips_registry_prefix() -> None:
    from app.services.manifest_deploy import _image_name_from_tag

    assert _image_name_from_tag("launch-web:latest") == "launch-web"
    assert _image_name_from_tag("ghcr.io/acme/api:v1") == "api"


def test_attach_image_pull_secret_on_deployment() -> None:
    from app.services.manifest_deploy import attach_image_pull_secret

    documents = [
        {
            "kind": "Deployment",
            "spec": {"template": {"spec": {"containers": [{"name": "app", "image": "x"}]}}},
        }
    ]
    attach_image_pull_secret(documents, "launchpad-registry")
    pulls = documents[0]["spec"]["template"]["spec"]["imagePullSecrets"]
    assert pulls == [{"name": "launchpad-registry"}]


def test_registry_host_from_artifact_registry() -> None:
    from app.services.cloud_instance_compute import (
        registry_host_from_image,
        resolve_registry_pull_auth,
    )
    from app.schemas.cloud import CloudCredentials

    image = "europe-west3-docker.pkg.dev/launchpad-504012/launchpad-previews/app:latest"
    assert registry_host_from_image(image) == "europe-west3-docker.pkg.dev"
    auth = resolve_registry_pull_auth(
        image=image,
        cloud_provider="gcp",
        credentials=CloudCredentials(
            gcp_sa_key_json='{"type":"service_account","project_id":"launchpad-504012"}'
        ),
        region="europe-west3",
        environment_id="env-1",
    )
    assert auth is not None
    server, username, password = auth
    assert server == "europe-west3-docker.pkg.dev"
    assert username == "_json_key"
    assert "service_account" in password


def test_dockerconfigjson_includes_https_and_bare_host() -> None:
    from app.services.kubernetes import build_registry_dockerconfigjson
    import json

    payload = json.loads(
        build_registry_dockerconfigjson(
            server="europe-west3-docker.pkg.dev",
            username="_json_key",
            password='{"type":"service_account"}',
        )
    )
    auths = payload["auths"]
    assert "europe-west3-docker.pkg.dev" in auths
    assert "https://europe-west3-docker.pkg.dev" in auths
    assert auths["https://europe-west3-docker.pkg.dev"]["username"] == "_json_key"


def test_first_cloud_registry_image_from_documents() -> None:
    from app.services.manifest_deploy import first_cloud_registry_image

    documents = [
        {
            "kind": "Deployment",
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "image": "europe-west3-docker.pkg.dev/p/launchpad-previews/app:latest",
                            }
                        ]
                    }
                }
            },
        }
    ]
    assert first_cloud_registry_image(documents) == (
        "europe-west3-docker.pkg.dev/p/launchpad-previews/app:latest"
    )
    assert first_cloud_registry_image([], extra="nginx:latest") is None


def test_platform_digest_skips_attestation_manifest() -> None:
    from app.services.cloud_instance_compute import (
        pin_registry_image_to_platform,
        platform_digest_from_inspect,
        registry_repo_from_image,
    )
    from unittest.mock import patch
    import json
    import subprocess

    arm_digest = "sha256:369c762eaeee9eeaab05f72d8b6458f92a5da9cbcc58ffdeca739593ec8923bf"
    payload = {
        "name": "europe-west3-docker.pkg.dev/launchpad-504012/launchpad-previews/app:latest",
        "manifest": {
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "manifests": [
                {
                    "digest": arm_digest,
                    "platform": {"architecture": "arm64", "os": "linux"},
                },
                {
                    "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "platform": {"architecture": "unknown", "os": "unknown"},
                    "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
                },
            ],
        },
    }
    assert platform_digest_from_inspect(payload, "linux/arm64") == arm_digest
    assert platform_digest_from_inspect(payload, "linux/amd64") is None
    image = "europe-west3-docker.pkg.dev/launchpad-504012/launchpad-previews/app:latest"
    assert registry_repo_from_image(image) == (
        "europe-west3-docker.pkg.dev/launchpad-504012/launchpad-previews/app"
    )
    assert registry_repo_from_image(f"{image.split(':')[0]}@{arm_digest}") == (
        "europe-west3-docker.pkg.dev/launchpad-504012/launchpad-previews/app"
    )

    cmds: list[list[str]] = []

    def fake_run(cmd, *, timeout, check=True, env=None, input_text=None):
        cmds.append(cmd)
        if cmd[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch(
        "app.services.cloud_instance_compute._run_cmd",
        side_effect=fake_run,
    ):
        pinned = pin_registry_image_to_platform(image=image, platform="linux/arm64")
    assert pinned == (
        "europe-west3-docker.pkg.dev/launchpad-504012/launchpad-previews/app"
        f"@{arm_digest}"
    )
    assert any(c[:4] == ["docker", "buildx", "imagetools", "create"] for c in cmds)


def test_remap_digest_image_uses_if_not_present() -> None:
    documents = [
        {
            "kind": "Deployment",
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "app", "image": "app:latest"}],
                    },
                },
            },
        }
    ]
    digest = (
        "europe-west3-docker.pkg.dev/p/launchpad-previews/app"
        "@sha256:369c762eaeee9eeaab05f72d8b6458f92a5da9cbcc58ffdeca739593ec8923bf"
    )
    remap_manifest_image_references(documents, {"app:latest": digest})
    container = documents[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == digest
    assert container["imagePullPolicy"] == "IfNotPresent"

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

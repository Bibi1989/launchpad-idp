"""Cloud Kubernetes (GKE) targeting for promote/provision."""

from __future__ import annotations

from app.schemas.k8s import DeployMode
from app.services.cloud_kubernetes import (
    is_cloud_kubernetes_deploy,
    is_cloud_kubernetes_provider,
    select_gke_cluster,
)


def test_cloud_kubernetes_provider_and_deploy_mode() -> None:
    assert is_cloud_kubernetes_provider("gcp")
    assert is_cloud_kubernetes_provider("aws")
    assert not is_cloud_kubernetes_provider("local")
    assert is_cloud_kubernetes_deploy(
        provider="gcp", deploy_mode=DeployMode.MANIFEST.value
    )
    assert is_cloud_kubernetes_deploy(
        provider="gcp", deploy_mode=DeployMode.PREVIEW.value
    )
    assert not is_cloud_kubernetes_deploy(
        provider="gcp", deploy_mode=DeployMode.ATTACH.value
    )
    assert not is_cloud_kubernetes_deploy(
        provider="local", deploy_mode=DeployMode.MANIFEST.value
    )


def test_select_gke_cluster_prefers_shared_name_then_region() -> None:
    clusters = [
        {"name": "other", "status": "RUNNING", "location": "us-central1"},
        {"name": "launchpad-previews", "status": "RUNNING", "location": "europe-west3"},
        {"name": "stopped", "status": "STOPPING", "location": "europe-west3"},
    ]
    picked = select_gke_cluster(
        clusters, preferred_name="launchpad-previews", region="europe-west3"
    )
    assert picked is not None
    assert picked["name"] == "launchpad-previews"

    regional = select_gke_cluster(
        [
            {"name": "prod", "status": "RUNNING", "location": "europe-west3-a"},
            {"name": "other", "status": "RUNNING", "location": "us-central1"},
        ],
        preferred_name="launchpad-previews",
        region="europe-west3",
    )
    assert regional is not None
    assert regional["name"] == "prod"


def test_select_gke_cluster_empty() -> None:
    assert select_gke_cluster([], preferred_name="launchpad-previews", region="europe-west3") is None


def test_ensure_gke_reuses_existing_cluster(tmp_path, monkeypatch) -> None:
    import json
    import subprocess
    from unittest.mock import patch

    from app.schemas.cloud import CloudCredentials
    from app.services import cloud_kubernetes as ck

    kubeconfig = tmp_path / "gke.yaml"
    kubeconfig.write_text(
        "apiVersion: v1\nkind: Config\ncurrent-context: gke_demo_europe-west3_launchpad-previews\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ck, "kubeconfig_path_for", lambda **kwargs: kubeconfig)

    cmds: list[list[str]] = []

    def fake_run(cmd, *, timeout, check=True, env=None, input_text=None):
        cmds.append(cmd)
        if cmd[:4] == ["gcloud", "container", "clusters", "list"]:
            payload = [
                {
                    "name": "launchpad-previews",
                    "status": "RUNNING",
                    "location": "europe-west3",
                }
            ]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with (
        patch.object(ck, "_run_cmd", side_effect=fake_run),
        patch.object(ck, "_enable_container_api"),
        patch.object(ck, "resolve_gcp_project_id", return_value="demo-proj"),
        patch.object(ck, "_credential_env", return_value={}),
        patch("shutil.which", return_value="/usr/bin/gcloud"),
    ):
        target = ck.ensure_cloud_kubernetes_target(
            provider="gcp",
            credentials=CloudCredentials(),
            region="europe-west3",
            environment_id="env-1",
            create=True,
        )
    assert target.cluster_name == "launchpad-previews"
    assert target.created is False
    assert target.context == "gke_demo_europe-west3_launchpad-previews"
    assert any(c[:4] == ["gcloud", "container", "clusters", "get-credentials"] for c in cmds)
    assert not any("create-auto" in c for c in cmds)

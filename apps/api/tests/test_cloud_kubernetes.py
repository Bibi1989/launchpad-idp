"""Cloud Kubernetes (GKE) targeting for promote/provision."""

from __future__ import annotations

from app.schemas.k8s import DeployMode
from app.services.cloud_kubernetes import (
    gke_cluster_has_public_endpoint,
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
        {"name": "other", "status": "RUNNING", "location": "us-central1", "endpoint": "1.1.1.1"},
        {
            "name": "launchpad-previews",
            "status": "RUNNING",
            "location": "europe-west3",
            "endpoint": "2.2.2.2",
        },
        {"name": "stopped", "status": "STOPPING", "location": "europe-west3", "endpoint": "3.3.3.3"},
    ]
    picked = select_gke_cluster(
        clusters, preferred_name="launchpad-previews", region="europe-west3"
    )
    assert picked is not None
    assert picked["name"] == "launchpad-previews"

    regional = select_gke_cluster(
        [
            {"name": "prod", "status": "RUNNING", "location": "europe-west3-a", "endpoint": "1.1.1.1"},
            {"name": "other", "status": "RUNNING", "location": "us-central1", "endpoint": "2.2.2.2"},
        ],
        preferred_name="launchpad-previews",
        region="europe-west3",
    )
    assert regional is not None
    assert regional["name"] == "prod"


def test_select_gke_cluster_skips_private_endpoint_only() -> None:
    private = {
        "name": "private-only",
        "status": "RUNNING",
        "location": "europe-west3",
        "privateClusterConfig": {
            "enablePrivateEndpoint": True,
            "publicEndpoint": "",
        },
    }
    public = {
        "name": "launchpad-previews",
        "status": "RUNNING",
        "location": "europe-west3",
        "endpoint": "34.1.2.3",
    }
    assert gke_cluster_has_public_endpoint(private) is False
    assert gke_cluster_has_public_endpoint(public) is True
    picked = select_gke_cluster(
        [private, public], preferred_name="launchpad-previews", region="europe-west3"
    )
    assert picked is not None
    assert picked["name"] == "launchpad-previews"


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
        "apiVersion: v1\nkind: Config\ncurrent-context: gke_demo_europe-west3_launchpad-previews\n"
        "clusters:\n- cluster:\n    server: https://34.1.2.3\n  name: gke\n",
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
                    "endpoint": "34.1.2.3",
                }
            ]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with (
        patch.object(ck, "_run_cmd", side_effect=fake_run),
        patch.object(ck, "_enable_container_api"),
        patch.object(ck, "resolve_gcp_project_id", return_value="demo-proj"),
        patch.object(ck, "_credential_env", return_value={}),
        patch.object(ck, "_kubeconfig_api_tcp_ok", return_value=True),
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


def test_ensure_gke_repairs_master_authorized_networks(tmp_path, monkeypatch) -> None:
    import json
    import subprocess
    from unittest.mock import patch

    from app.schemas.cloud import CloudCredentials
    from app.services import cloud_kubernetes as ck
    from app.services.cloud_instance_compute import CloudInstanceComputeError

    kubeconfig = tmp_path / "gke.yaml"
    kubeconfig.write_text(
        "apiVersion: v1\nkind: Config\ncurrent-context: gke_demo\n"
        "clusters:\n- cluster:\n    server: https://34.185.165.205\n  name: gke\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ck, "kubeconfig_path_for", lambda **kwargs: kubeconfig)

    cmds: list[list[str]] = []

    def fake_run(cmd, *, timeout, check=True, env=None, input_text=None):
        cmds.append(list(cmd))
        if cmd[:4] == ["gcloud", "container", "clusters", "list"]:
            payload = [
                {
                    "name": "launchpad-previews",
                    "status": "RUNNING",
                    "location": "europe-west3",
                    "endpoint": "34.185.165.205",
                    "masterAuthorizedNetworksConfig": {"enabled": True, "cidrBlocks": []},
                }
            ]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    probes = iter([False, True])

    with (
        patch.object(ck, "_run_cmd", side_effect=fake_run),
        patch.object(ck, "_enable_container_api"),
        patch.object(ck, "resolve_gcp_project_id", return_value="demo-proj"),
        patch.object(ck, "_credential_env", return_value={}),
        patch.object(ck, "_kubeconfig_api_tcp_ok", side_effect=lambda *_a, **_k: next(probes)),
        patch.object(ck, "time") as mock_time,
        patch("shutil.which", return_value="/usr/bin/gcloud"),
    ):
        mock_time.sleep.return_value = None
        target = ck.ensure_cloud_kubernetes_target(
            provider="gcp",
            credentials=CloudCredentials(),
            region="europe-west3",
            environment_id="env-1",
            create=True,
        )
    assert target.cluster_name == "launchpad-previews"
    assert any(
        c[:5] == ["gcloud", "container", "clusters", "update", "launchpad-previews"]
        and "--no-enable-master-authorized-networks" in c
        for c in cmds
    )


def test_select_eks_cluster_prefers_shared_name() -> None:
    from app.services.cloud_kubernetes import select_eks_cluster

    assert (
        select_eks_cluster(
            ["other", "launchpad-previews", "prod"],
            preferred_name="launchpad-previews",
        )
        == "launchpad-previews"
    )
    assert select_eks_cluster(["alpha", "beta"], preferred_name="launchpad-previews") == "alpha"
    assert select_eks_cluster([], preferred_name="launchpad-previews") is None


def test_ensure_eks_reuses_existing_cluster(tmp_path, monkeypatch) -> None:
    from unittest.mock import patch

    from app.schemas.cloud import CloudCredentials
    from app.services import cloud_kubernetes as ck

    kubeconfig = tmp_path / "eks.yaml"
    monkeypatch.setattr(ck, "kubeconfig_path_for", lambda **kwargs: kubeconfig)

    with (
        patch.object(ck, "_credential_env", return_value={"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S"}),
        patch("app.services.aws_client.sts_account_id", return_value="123456789012"),
        patch(
            "app.services.aws_client.list_eks_cluster_names",
            return_value=["launchpad-previews", "other"],
        ),
        patch("app.services.aws_client.eks_cluster_status", return_value="ACTIVE"),
        patch(
            "app.services.aws_client.write_eks_kubeconfig",
            return_value="arn:aws:eks:us-east-1:123456789012:cluster/launchpad-previews",
        ) as write_kc,
        patch.object(ck, "_kubeconfig_api_tcp_ok", return_value=True),
        patch("app.services.aws_client.create_eks_auto_cluster") as create_cluster,
    ):
        target = ck.ensure_cloud_kubernetes_target(
            provider="aws",
            credentials=CloudCredentials(),
            region="us-east-1",
            environment_id="env-1",
            create=True,
        )
    assert target.cluster_name == "launchpad-previews"
    assert target.created is False
    assert target.provider == "aws"
    write_kc.assert_called_once()
    create_cluster.assert_not_called()


def test_ensure_eks_creates_when_missing(tmp_path, monkeypatch) -> None:
    from unittest.mock import patch

    from app.schemas.cloud import CloudCredentials
    from app.services import cloud_kubernetes as ck

    kubeconfig = tmp_path / "eks.yaml"
    monkeypatch.setattr(ck, "kubeconfig_path_for", lambda **kwargs: kubeconfig)

    with (
        patch.object(ck, "_credential_env", return_value={"AWS_ACCESS_KEY_ID": "A", "AWS_SECRET_ACCESS_KEY": "S"}),
        patch("app.services.aws_client.sts_account_id", return_value="1"),
        patch("app.services.aws_client.list_eks_cluster_names", return_value=[]),
        patch("app.services.aws_client.ensure_eks_preview_subnets", return_value=["subnet-a", "subnet-b"]),
        patch(
            "app.services.aws_client.ensure_eks_auto_roles",
            return_value=("arn:aws:iam::1:role/cluster", "arn:aws:iam::1:role/node"),
        ),
        patch("app.services.aws_client.create_eks_auto_cluster") as create_cluster,
        patch("app.services.aws_client.eks_cluster_status", return_value="ACTIVE"),
        patch(
            "app.services.aws_client.write_eks_kubeconfig",
            return_value="arn:aws:eks:us-east-1:1:cluster/launchpad-previews",
        ),
        patch.object(ck, "_kubeconfig_api_tcp_ok", return_value=True),
    ):
        target = ck.ensure_cloud_kubernetes_target(
            provider="aws",
            credentials=CloudCredentials(),
            region="us-east-1",
            environment_id="env-1",
            create=True,
        )
    assert target.cluster_name == "launchpad-previews"
    assert target.created is True
    create_cluster.assert_called_once()


def test_ensure_eks_missing_credentials_surfaces_sdk_error(tmp_path, monkeypatch) -> None:
    from unittest.mock import patch

    from app.schemas.cloud import CloudCredentials
    from app.services import cloud_kubernetes as ck
    from app.services.aws_client import AwsClientError
    from app.services.cloud_instance_compute import CloudInstanceComputeError

    with (
        patch.object(ck, "_credential_env", return_value={}),
        patch("app.services.aws_client.sts_account_id", return_value=None),
        patch(
            "app.services.aws_client.list_eks_cluster_names",
            side_effect=AwsClientError(
                "AWS credentials are missing for this deploy. Paste access keys in Settings"
            ),
        ),
    ):
        try:
            ck.ensure_cloud_kubernetes_target(
                provider="aws",
                credentials=CloudCredentials(),
                region="us-east-1",
                environment_id="env-1",
                create=True,
            )
            raise AssertionError("expected CloudInstanceComputeError")
        except CloudInstanceComputeError as exc:
            assert "credentials" in str(exc).lower()


def test_write_eks_kubeconfig_writes_python_exec(tmp_path) -> None:
    from unittest.mock import MagicMock, patch

    from app.services import aws_client

    eks = MagicMock()
    eks.describe_cluster.return_value = {
        "cluster": {
            "endpoint": "https://eks.example.com",
            "certificateAuthority": {"data": "Y2E="},
            "arn": "arn:aws:eks:eu-central-1:1:cluster/launchpad-previews",
        }
    }
    path = tmp_path / "kube.yaml"
    with patch.object(aws_client, "_client", return_value=eks):
        ctx = aws_client.write_eks_kubeconfig(
            env={
                "AWS_ACCESS_KEY_ID": "AKIATEST",
                "AWS_SECRET_ACCESS_KEY": "secret",
            },
            region="eu-central-1",
            cluster_name="launchpad-previews",
            kubeconfig_path=str(path),
        )
    assert ctx.endswith("launchpad-previews")
    text = path.read_text(encoding="utf-8")
    assert "app.services.eks_token" in text
    assert "https://eks.example.com" in text
    assert path.with_suffix(".yaml.awscreds").is_file() or list(tmp_path.glob("*.awscreds"))


def test_ensure_gke_unreachable_raises_actionable_error(tmp_path, monkeypatch) -> None:
    import json
    import subprocess
    from unittest.mock import patch

    from app.schemas.cloud import CloudCredentials
    from app.services import cloud_kubernetes as ck
    from app.services.cloud_instance_compute import CloudInstanceComputeError

    kubeconfig = tmp_path / "gke.yaml"
    kubeconfig.write_text(
        "apiVersion: v1\nkind: Config\ncurrent-context: gke_demo\n"
        "clusters:\n- cluster:\n    server: https://34.185.165.205\n  name: gke\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ck, "kubeconfig_path_for", lambda **kwargs: kubeconfig)

    def fake_run(cmd, *, timeout, check=True, env=None, input_text=None):
        if cmd[:4] == ["gcloud", "container", "clusters", "list"]:
            payload = [
                {
                    "name": "launchpad-previews",
                    "status": "RUNNING",
                    "location": "europe-west3",
                    "endpoint": "34.185.165.205",
                }
            ]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        if "--dns-endpoint" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "dns endpoint not enabled")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with (
        patch.object(ck, "_run_cmd", side_effect=fake_run),
        patch.object(ck, "_enable_container_api"),
        patch.object(ck, "resolve_gcp_project_id", return_value="demo-proj"),
        patch.object(ck, "_credential_env", return_value={}),
        patch.object(ck, "_kubeconfig_api_tcp_ok", return_value=False),
        patch.object(ck, "time") as mock_time,
        patch("shutil.which", return_value="/usr/bin/gcloud"),
    ):
        mock_time.sleep.return_value = None
        try:
            ck.ensure_cloud_kubernetes_target(
                provider="gcp",
                credentials=CloudCredentials(),
                region="europe-west3",
                environment_id="env-1",
                create=True,
            )
            raise AssertionError("expected CloudInstanceComputeError")
        except CloudInstanceComputeError as exc:
            assert "Cannot reach GKE API" in str(exc)
            assert "Master Authorized Networks" in str(exc)

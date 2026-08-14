"""Teardown context sealing and GCP credential materialization."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.secrets import encrypt_secret
from app.schemas.cloud import CloudCredentials
from app.services.cloud_instance_compute import _credential_env
from app.services.teardown_context import parse_teardown_context


def test_parse_teardown_context_roundtrip() -> None:
    payload = {
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "encrypted_credentials": "sealed",
        "workspace_provider": "gcp",
        "create_vpc": True,
        "owner_id": "22222222-2222-2222-2222-222222222222",
    }
    raw = encrypt_secret(json.dumps(payload))
    parsed = parse_teardown_context(raw)
    assert parsed is not None
    assert parsed["workspace_provider"] == "gcp"
    assert parsed["create_vpc"] is True
    assert parsed["encrypted_credentials"] == "sealed"


def test_credential_env_writes_gcp_sa_key_file(tmp_path: Path) -> None:
    sa = json.dumps(
        {
            "type": "service_account",
            "project_id": "demo-proj",
            "private_key_id": "x",
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n",
            "client_email": "sa@demo-proj.iam.gserviceaccount.com",
        }
    )
    creds = CloudCredentials(gcp_sa_key_json=sa)
    with patch("tempfile.gettempdir", return_value=str(tmp_path)):
        env = _credential_env(creds, environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    key_path = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    assert key_path
    assert Path(key_path).is_file()
    assert Path(key_path).read_text(encoding="utf-8") == sa
    assert env.get("CLOUDSDK_CORE_PROJECT") == "demo-proj"
    assert env.get("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE") == key_path


def test_credential_env_ignores_stale_ambient_oidc_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_cfg = tmp_path / "gcp_credential_config.json"
    stale_cfg.write_text(
        json.dumps(
            {
                "type": "external_account",
                "credential_source": {"file": "/tmp/launchpad_oidc_token.jwt"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(stale_cfg))
    monkeypatch.setenv("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", str(stale_cfg))

    sa = json.dumps(
        {
            "type": "service_account",
            "project_id": "demo-proj",
            "private_key_id": "x",
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n",
            "client_email": "sa@demo-proj.iam.gserviceaccount.com",
        }
    )
    creds = CloudCredentials(gcp_sa_key_json=sa)
    with patch("tempfile.gettempdir", return_value=str(tmp_path)):
        env = _credential_env(creds, environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    key_path = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    assert key_path
    assert key_path != str(stale_cfg)
    assert Path(key_path).read_text(encoding="utf-8") == sa
    assert env.get("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE") == key_path
    assert "/tmp/launchpad_oidc_token.jwt" not in Path(key_path).read_text(encoding="utf-8")


def test_credential_env_aws_ignores_broken_gcp_oauth(tmp_path: Path) -> None:
    from app.services.cloud_instance_compute import CloudInstanceComputeError

    creds = CloudCredentials(
        gcp_oauth_token_json='{"provider":"gcp","refresh_token":"x"}',
        aws_access_key_id="AKIATEST",
        aws_secret_access_key="secret",
    )
    with patch("tempfile.gettempdir", return_value=str(tmp_path)):
        env = _credential_env(
            creds,
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            provider="aws",
        )
    assert env.get("AWS_ACCESS_KEY_ID") == "AKIATEST"

    with patch("tempfile.gettempdir", return_value=str(tmp_path)):
        with pytest.raises(CloudInstanceComputeError, match="GCP Connect token"):
            _credential_env(
                creds,
                environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                provider="gcp",
            )


def test_teardown_cloud_vm_deletes_network_after_instances() -> None:
    from app.services.cloud_instance_compute import teardown_cloud_vm
    from app.schemas.cloud import RunningInstanceConfig

    cmds: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        cmds.append(list(cmd))
        joined = " ".join(cmd)
        if "instances" in cmd and "list" in cmd and "labels.launchpad-environment-id=" in joined:
            return MagicMock(
                returncode=0,
                stdout=json.dumps(
                    [{"name": "lp-demo", "zone": "projects/p/zones/us-central1-a"}]
                ),
                stderr="",
            )
        if "instances" in cmd and "list" in cmd:
            return MagicMock(returncode=0, stdout="[]", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("app.services.cloud_instance_compute.shutil.which", return_value="/usr/bin/gcloud"),
        patch("app.services.cloud_instance_compute._run_cmd", side_effect=fake_run),
        patch("app.services.cloud_instance_compute.time.sleep"),
    ):
        teardown_cloud_vm(
            running_instance=RunningInstanceConfig(region="us-central1"),
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            environment_name="demo",
            cloud_provider="gcp",
            credentials=CloudCredentials(
                gcp_sa_key_json=json.dumps({"type": "service_account", "project_id": "p"})
            ),
        )

    delete_instance = [c for c in cmds if c[:4] == ["gcloud", "compute", "instances", "delete"]]
    delete_network = [c for c in cmds if c[:4] == ["gcloud", "compute", "networks", "delete"]]
    assert delete_instance
    assert delete_network
    assert cmds.index(delete_instance[0]) < cmds.index(delete_network[0])

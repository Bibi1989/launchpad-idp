from __future__ import annotations

import json
from pathlib import Path

from app.services.sandbox_runner import SandboxRunner, _CONTAINER_OIDC_ROOT


def test_docker_oidc_env_rewrites_paths(tmp_path: Path, monkeypatch) -> None:
    host_root = tmp_path / "launchpad-oidc" / "ws-1"
    host_root.mkdir(parents=True)
    token = host_root / "oidc_token.jwt"
    token.write_text("jwt-token", encoding="utf-8")
    cfg = host_root / "gcp_credential_config.json"
    cfg.write_text(
        json.dumps(
            {
                "type": "external_account",
                "credential_source": {"file": str(token)},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.sandbox_runner.tempfile.gettempdir",
        lambda: str(tmp_path),
    )

    env, volumes = SandboxRunner._docker_oidc_env(
        {
            "GOOGLE_APPLICATION_CREDENTIALS": str(cfg),
            "AWS_WEB_IDENTITY_TOKEN_FILE": str(token),
            "LAUNCHPAD_OIDC_JWT": "jwt-token",
        },
        "ws-1",
    )

    assert volumes[str(host_root.resolve())]["bind"] == _CONTAINER_OIDC_ROOT
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == (
        f"{_CONTAINER_OIDC_ROOT}/gcp_credential_config.docker.json"
    )
    assert env["AWS_WEB_IDENTITY_TOKEN_FILE"] == f"{_CONTAINER_OIDC_ROOT}/oidc_token.jwt"
    docker_cfg = host_root / "gcp_credential_config.docker.json"
    assert docker_cfg.is_file()
    data = json.loads(docker_cfg.read_text(encoding="utf-8"))
    assert data["credential_source"]["file"] == f"{_CONTAINER_OIDC_ROOT}/oidc_token.jwt"

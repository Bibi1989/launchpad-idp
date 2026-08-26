"""Advisory Dockerfile build+run+probe verification (never blocks provisioning)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.services import dockerfile_verify as dv
from app.services.preview_build import PreviewBuildError


def _write_dockerfile(root: Path) -> None:
    (root / "Dockerfile").write_text("FROM node:20-alpine\nEXPOSE 3000\n", encoding="utf-8")


def test_verify_verified_when_build_and_probe_succeed(tmp_path) -> None:
    _write_dockerfile(tmp_path)
    with (
        patch.object(dv, "_docker_available", return_value=True),
        patch("app.services.preview_build._ensure_dockerfile", return_value=None),
        patch("app.services.preview_build._docker_build", return_value=None),
        patch.object(dv, "_run_and_probe", return_value=(True, True, "app listening", None)),
    ):
        result = dv.verify_service_dockerfile(Path(tmp_path), service_name="web")

    assert result.status == dv.STATUS_VERIFIED
    assert result.used_repo_dockerfile is True
    assert result.built is True
    assert result.ran is True
    assert result.probe_ok is True
    assert result.listen_port == 3000  # parsed from EXPOSE


def test_verify_warns_when_build_fails(tmp_path) -> None:
    _write_dockerfile(tmp_path)
    with (
        patch.object(dv, "_docker_available", return_value=True),
        patch("app.services.preview_build._ensure_dockerfile", return_value=None),
        patch("app.services.preview_build._docker_build", side_effect=PreviewBuildError("boom")),
    ):
        result = dv.verify_service_dockerfile(Path(tmp_path), service_name="web")

    assert result.status == dv.STATUS_WARNING
    assert result.built is False
    assert result.warning and "docker build failed" in result.warning


def test_verify_warns_when_container_does_not_serve(tmp_path) -> None:
    _write_dockerfile(tmp_path)
    with (
        patch.object(dv, "_docker_available", return_value=True),
        patch("app.services.preview_build._ensure_dockerfile", return_value=None),
        patch("app.services.preview_build._docker_build", return_value=None),
        patch.object(dv, "_run_and_probe", return_value=(True, False, "crashloop", "probe failed (connection_refused)")),
    ):
        result = dv.verify_service_dockerfile(Path(tmp_path), service_name="web")

    assert result.status == dv.STATUS_WARNING
    assert result.built is True
    assert result.ran is True
    assert result.probe_ok is False


def test_verify_skips_when_docker_unavailable(tmp_path) -> None:
    _write_dockerfile(tmp_path)
    with patch.object(dv, "_docker_available", return_value=False):
        result = dv.verify_service_dockerfile(Path(tmp_path), service_name="web")

    assert result.status == dv.STATUS_SKIPPED
    assert result.warning == "docker_unavailable"


def test_verify_generates_dockerfile_when_missing(tmp_path) -> None:
    # No Dockerfile shipped; a stack is detected and one is generated.
    (tmp_path / "package.json").write_text('{"name": "svc"}', encoding="utf-8")
    with (
        patch.object(dv, "_docker_available", return_value=True),
        patch("app.services.preview_build._ensure_dockerfile", return_value="node"),
        patch("app.services.preview_build._docker_build", return_value=None),
        patch.object(dv, "_run_and_probe", return_value=(True, True, "", None)),
    ):
        result = dv.verify_service_dockerfile(Path(tmp_path), service_name="svc")

    assert result.status == dv.STATUS_VERIFIED
    assert result.used_repo_dockerfile is False
    assert result.generated_stack == "node"


def test_verify_warns_when_stack_undetectable(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("nothing buildable", encoding="utf-8")
    with (
        patch.object(dv, "_docker_available", return_value=True),
        patch(
            "app.services.preview_build._ensure_dockerfile",
            side_effect=PreviewBuildError("stack unknown"),
        ),
    ):
        result = dv.verify_service_dockerfile(Path(tmp_path), service_name="svc")

    assert result.status == dv.STATUS_WARNING
    assert result.built is False
    assert result.warning and "stack undetectable" in result.warning


def test_verify_workspace_batch_uses_service_specs(tmp_path) -> None:
    svc = tmp_path / "apps" / "web"
    svc.mkdir(parents=True)
    _write_dockerfile(svc)
    with (
        patch.object(dv, "_docker_available", return_value=True),
        patch("app.services.preview_build._ensure_dockerfile", return_value=None),
        patch("app.services.preview_build._docker_build", return_value=None),
        patch.object(dv, "_run_and_probe", return_value=(True, True, "", None)),
    ):
        results = dv.verify_workspace_dockerfiles(
            Path(tmp_path),
            services=[{"name": "web", "path": "apps/web", "listen_port": 8081}],
        )

    assert len(results) == 1
    assert results[0].service == "web"
    assert results[0].listen_port == 8081
    assert results[0].status == dv.STATUS_VERIFIED

"""Deploy-time Trivy image scan and container build platform helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.cloud import ImageSecurityScanConfig, ScanFindingAction, ScanSeverityThreshold
from app.services.cloud_instance_compute import (
    host_container_platform,
    normalize_node_architecture,
)
from app.services.image_security_scan import (
    ImageSecurityScanError,
    parse_image_scan_config,
    scan_local_docker_image,
)
from app.services.kubernetes import cluster_container_platform


def test_parse_image_scan_config_defaults() -> None:
    cfg = parse_image_scan_config(None)
    assert cfg.enabled is False
    enabled = parse_image_scan_config(
        {"enabled": True, "severity_threshold": "critical", "on_finding": "warn"}
    )
    assert enabled.enabled is True
    assert enabled.severity_threshold == ScanSeverityThreshold.CRITICAL
    assert enabled.on_finding == ScanFindingAction.WARN


def test_scan_skipped_when_disabled() -> None:
    with patch("app.services.image_security_scan.subprocess.run") as run:
        scan_local_docker_image(image="app:latest", config=ImageSecurityScanConfig())
    run.assert_not_called()


def test_scan_block_raises_on_findings() -> None:
    result = MagicMock(returncode=1, stdout="CRITICAL CVE-2024-1", stderr="")
    with patch("app.services.image_security_scan.subprocess.run", return_value=result):
        with pytest.raises(ImageSecurityScanError, match="blocked"):
            scan_local_docker_image(
                image="app:latest",
                config=ImageSecurityScanConfig(enabled=True),
            )


def test_scan_warn_does_not_raise() -> None:
    result = MagicMock(returncode=1, stdout="HIGH finding", stderr="")
    with patch("app.services.image_security_scan.subprocess.run", return_value=result):
        scan_local_docker_image(
            image="app:latest",
            config=ImageSecurityScanConfig(
                enabled=True,
                on_finding=ScanFindingAction.WARN,
            ),
        )


def test_normalize_node_architecture() -> None:
    assert normalize_node_architecture("arm64") == "linux/arm64"
    assert normalize_node_architecture("amd64") == "linux/amd64"
    assert normalize_node_architecture("riscv") is None


def test_host_container_platform_maps_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    assert host_container_platform() == "linux/arm64"
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    assert host_container_platform() == "linux/amd64"


def test_cluster_container_platform_from_nodes() -> None:
    core = MagicMock()
    core.list_node.return_value = SimpleNamespace(
        items=[
            SimpleNamespace(
                status=SimpleNamespace(
                    node_info=SimpleNamespace(architecture="arm64")
                )
            )
        ]
    )
    assert cluster_container_platform(core) == "linux/arm64"
    assert cluster_container_platform(None) is None

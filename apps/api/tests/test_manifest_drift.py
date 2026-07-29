"""Manifest-mode drift inventory comparison."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.services.drift_scanner import (
    ExpectedDeployment,
    inspect_manifest_inventory,
)


def test_inspect_manifest_inventory_in_sync() -> None:
    expected = [ExpectedDeployment(name="api", image="app:1"), ExpectedDeployment(name="web", image="web:1")]
    live = {
        "api": SimpleNamespace(
            spec=SimpleNamespace(
                template=SimpleNamespace(
                    spec=SimpleNamespace(containers=[SimpleNamespace(image="app:1")])
                )
            )
        ),
        "web": SimpleNamespace(
            spec=SimpleNamespace(
                template=SimpleNamespace(
                    spec=SimpleNamespace(containers=[SimpleNamespace(image="web:1")])
                )
            )
        ),
    }
    assert inspect_manifest_inventory(expected=expected, live_by_name=live) == []


def test_inspect_manifest_inventory_missing_and_image() -> None:
    expected = [
        ExpectedDeployment(name="api", image="app:2"),
        ExpectedDeployment(name="worker", image="worker:1"),
    ]
    live = {
        "api": SimpleNamespace(
            spec=SimpleNamespace(
                template=SimpleNamespace(
                    spec=SimpleNamespace(containers=[SimpleNamespace(image="app:1")])
                )
            )
        ),
    }
    mismatches = inspect_manifest_inventory(expected=expected, live_by_name=live)
    assert any("api" in item and "image expected=" in item for item in mismatches)
    assert any("worker missing" in item for item in mismatches)


def test_inspect_manifest_inventory_empty_expected() -> None:
    assert inspect_manifest_inventory(expected=[], live_by_name={}) == [
        "no Deployment resources found in workspace manifests"
    ]


def test_scan_environment_routes_manifest(monkeypatch) -> None:
    from pathlib import Path
    from unittest.mock import MagicMock

    from app.services import drift_scanner

    environment = MagicMock()
    environment.deploy_mode = "manifest"
    environment.id = uuid4()
    environment.namespace_name = "ns"
    provisioner = MagicMock()
    provisioner._settings.kubernetes_enabled = True

    called = {"manifest": False}

    def fake_manifest(*_args, **_kwargs):
        called["manifest"] = True
        return None

    monkeypatch.setattr(drift_scanner, "_scan_manifest", fake_manifest)
    assert (
        drift_scanner.scan_environment(
            provisioner,
            environment,
            default_image="img:1",
            workspace_root=Path("/tmp"),
        )
        is None
    )
    assert called["manifest"] is True

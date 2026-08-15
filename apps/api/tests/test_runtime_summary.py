"""Runtime summary chips omit placeholder workload images."""

from __future__ import annotations

from app.services.environment import _build_runtime_summary, _is_placeholder_workload_image


def test_placeholder_images() -> None:
    assert _is_placeholder_workload_image(None)
    assert _is_placeholder_workload_image("nginx:1.27-alpine")
    assert _is_placeholder_workload_image(
        "nginx:1.27-alpine",
        default_image="nginx:1.27-alpine",
    )
    assert not _is_placeholder_workload_image("ghcr.io/acme/app:sha-abc")


def test_runtime_summary_omits_placeholder_image() -> None:
    summary = _build_runtime_summary(
        namespace_name="launchpad-env-abc",
        workload_image="nginx:1.27-alpine",
        default_workload_image="nginx:1.27-alpine",
        provider="gcp",
        deploy_mode="attach",
    )
    assert "image=" not in summary
    assert "ns=launchpad-env-abc" in summary
    assert "provider=gcp" in summary
    assert "deploy=attach" in summary


def test_runtime_summary_includes_real_image() -> None:
    summary = _build_runtime_summary(
        namespace_name="ns",
        workload_image="ghcr.io/acme/web:1.2.3",
        default_workload_image="nginx:1.27-alpine",
        deploy_mode="preview",
    )
    assert "image=ghcr.io/acme/web:1.2.3" in summary

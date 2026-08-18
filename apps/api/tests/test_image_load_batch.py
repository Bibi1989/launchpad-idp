"""Linked-repo images are loaded into the local cluster in ONE batched call.

Previously each alias tag (launch-web, web, web-ui, launchpad/web, ...) was imported
separately - a full multi-minute ``k3d image import`` per tag - which made linked-repo
provisions sit at APPLY for 30-50 minutes. One batched import shares layers and
transfers the content once.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import app.services.manifest_deploy as md


def test_batch_load_uses_single_k3d_call_for_all_tags() -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    tags = ["web:latest", "launch-web:latest", "launchpad/web:latest", "web-ui:latest"]
    with (
        patch.object(md, "cluster_has_image", return_value=False),
        patch("shutil.which", return_value="/usr/local/bin/k3d"),
        patch.object(md.subprocess, "run", side_effect=_fake_run),
    ):
        loaded = md.load_images_to_local_cluster_batch(tags, cluster_name="launchpad", engine="k3s")

    assert loaded == set(tags)
    # Exactly ONE k3d import command, carrying all four tags.
    import_calls = [c for c in calls if "import" in c]
    assert len(import_calls) == 1
    for tag in tags:
        assert tag in import_calls[0]


def test_batch_load_skips_tags_already_in_cluster() -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch.object(md, "resolve_kind_cluster_name", return_value="launchpad"),
        patch.object(md, "cluster_has_image", return_value=True),
        patch("shutil.which", return_value="/usr/local/bin/k3d"),
        patch.object(md.subprocess, "run", side_effect=_fake_run),
    ):
        loaded = md.load_images_to_local_cluster_batch(["web:latest"], cluster_name="c", engine="k3s")
    assert loaded == {"web:latest"}
    # Already present -> no image-import command issued.
    assert not [c for c in calls if "import" in c or "load" in c]


def test_batch_load_falls_back_to_per_tag_on_batch_failure() -> None:
    def _fake_run(cmd, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with (
        patch.object(md, "cluster_has_image", return_value=False),
        patch("shutil.which", return_value="/usr/local/bin/k3d"),
        patch.object(md.subprocess, "run", side_effect=_fake_run),
        patch.object(md, "load_image_to_local_cluster_with_retry", return_value=True) as per_tag,
    ):
        loaded = md.load_images_to_local_cluster_batch(
            ["a:latest", "b:latest"], cluster_name="c", engine="k3s"
        )
    assert loaded == {"a:latest", "b:latest"}
    assert per_tag.call_count == 2  # fell back to per-tag retries

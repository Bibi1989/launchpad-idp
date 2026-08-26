"""Tests for safe plugin-bundle extraction (app.services.user_plugins)."""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from app.services.user_plugins import (
    BundleError,
    _check_caps,
    _extract_tar,
    _extract_zip,
)


def _make_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_extract_tar_writes_files(tmp_path):
    data = _make_tar({"main.tf": b"resource {}", "vars.tf": b"variable {}"})
    count = _extract_tar(data, tmp_path)
    assert count == 2
    assert (tmp_path / "main.tf").read_text() == "resource {}"


def test_extract_zip_writes_files(tmp_path):
    data = _make_zip({"index.ts": b"export const x = 1"})
    count = _extract_zip(data, tmp_path)
    assert count == 1
    assert (tmp_path / "index.ts").exists()


def test_extract_zip_blocks_path_traversal(tmp_path):
    data = _make_zip({"../evil.tf": b"pwn"})
    with pytest.raises(BundleError):
        _extract_zip(data, tmp_path)
    assert not (tmp_path.parent / "evil.tf").exists()


def test_extract_tar_blocks_path_traversal(tmp_path):
    # tarfile data filter rejects traversal; extraction raises rather than escaping.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="../evil.tf")
        payload = b"pwn"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(BundleError):
        _extract_tar(buf.getvalue(), tmp_path)
    assert not (tmp_path.parent / "evil.tf").exists()


def test_caps_reject_oversize_and_too_many_files():
    with pytest.raises(BundleError):
        _check_caps(100 * 1024 * 1024, 1)  # > 50 MB
    with pytest.raises(BundleError):
        _check_caps(1, 10_000)  # > file cap
    _check_caps(1024, 5)  # within caps -> ok

"""Framework -> ProjectStack resolution for auto-generated Dockerfiles.

Regression for linked-repo Vite frontends crash-looping: the detector emits
framework ``vite`` (not the enum value ``react_vite``), so the old
``ProjectStack(framework)`` threw and the fallback picked generic ``node`` -> an
``npm start`` Dockerfile that crashes a Vite SPA (only dev/build/preview scripts).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.schemas.dockerfile_schema import ProjectStack
from app.services.dockerfile_scaffold import project_stack_from_framework
from app.services.preview_build import _ensure_dockerfile


@pytest.mark.parametrize(
    ("framework", "expected"),
    [
        ("vite", ProjectStack.REACT_VITE),
        ("react", ProjectStack.REACT_VITE),
        ("astro", ProjectStack.REACT_VITE),
        ("vue", ProjectStack.VUEJS),
        ("nuxt", ProjectStack.NUXTJS),
        ("next", ProjectStack.NEXTJS),
        ("nextjs", ProjectStack.NEXTJS),
        ("svelte", ProjectStack.SVELTE),
        ("angular", ProjectStack.ANGULAR),
        ("fastapi", ProjectStack.FASTAPI),
        ("express", ProjectStack.EXPRESS),
        ("nest", ProjectStack.NESTJS),
        ("gin", ProjectStack.GO),
        ("react_vite", ProjectStack.REACT_VITE),  # already-canonical value
    ],
)
def test_framework_maps_to_stack(framework: str, expected: ProjectStack) -> None:
    assert project_stack_from_framework(framework) == expected


def test_unknown_or_empty_framework_returns_none() -> None:
    assert project_stack_from_framework("") is None
    assert project_stack_from_framework(None) is None
    assert project_stack_from_framework("zzz-unknown") is None


def test_ensure_dockerfile_generates_vite_nginx_not_node(tmp_path) -> None:
    """A Vite SPA (vite.config.js + build/preview scripts, no start) must get the
    multi-stage nginx-static Dockerfile, never the generic node ``npm start`` one."""
    repo = Path(tmp_path) / "web"
    repo.mkdir()
    (repo / "vite.config.js").write_text("export default {}\n", encoding="utf-8")
    (repo / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (repo / "package.json").write_text(
        '{"name":"web","scripts":{"dev":"vite","build":"vite build","preview":"vite preview"},'
        '"dependencies":{"react":"^18","react-dom":"^18"},"devDependencies":{"vite":"^5"}}',
        encoding="utf-8",
    )
    (repo / "src").mkdir()
    (repo / "src" / "main.jsx").write_text("console.log('x')", encoding="utf-8")

    stack = _ensure_dockerfile(repo, dockerfile_rel="Dockerfile", force=False)
    dockerfile = (repo / "Dockerfile").read_text(encoding="utf-8")
    assert stack == "react_vite"
    assert "nginx" in dockerfile.lower()
    assert "npm start" not in dockerfile
    assert "npm run build" in dockerfile or "vite build" in dockerfile.lower()

"""Classify scaffold / workspace services as frontend vs backend."""

from __future__ import annotations

import re

_FRONTEND_TOKENS = frozenset(
    {
        "web",
        "ui",
        "frontend",
        "website",
        "site",
        "marketing",
        "landing",
        "spa",
        "next",
        "nuxt",
        "client",
        "nextjs",
        "nuxtjs",
        "react",
        "vue",
        "svelte",
    }
)
_BACKEND_TOKENS = frozenset(
    {
        "api",
        "backend",
        "server",
        "svc",
        "service",
        "worker",
        "fastapi",
        "express",
        "nest",
        "django",
        "flask",
    }
)
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def service_name_tokens(name: str) -> set[str]:
    return {part for part in _TOKEN_SPLIT.split((name or "").strip().lower()) if part}


def is_frontend_service_name(name: str) -> bool:
    """True when a directory/service name looks like a browser UI.

    Uses whole tokens only. Substring checks like ``\"ui\" in \"api\"`` are wrong.
    """
    parts = service_name_tokens(name)
    if parts & _BACKEND_TOKENS and not (parts & _FRONTEND_TOKENS):
        return False
    if parts & _FRONTEND_TOKENS:
        return True
    joined = (name or "").strip().lower()
    return joined.startswith("web") or "-web-" in f"-{joined}-" or joined.endswith("-web")


def is_frontend_app_kind(app_kind: str | None, *, name: str = "") -> bool:
    kind = (app_kind or "").strip().lower()
    if kind == "frontend":
        return True
    if kind == "backend":
        return False
    return is_frontend_service_name(name)

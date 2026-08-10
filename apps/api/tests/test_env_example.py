"""Tests for .env.example discovery and parsing."""

from __future__ import annotations

from pathlib import Path

from pkg.detector.env_example import (
    collect_env_example_vars,
    parse_env_example_text,
    suggested_datastore_urls,
)


def test_parse_env_example_strips_secrets_and_comments() -> None:
    text = """
# App port
PORT=3000
# Database
DATABASE_URL=postgresql://local:local@localhost:5432/app
API_SECRET=do-not-commit
export REDIS_URL="redis://localhost:6379/0"
"""
    vars_ = parse_env_example_text(text)
    by_key = {v.key: v for v in vars_}
    assert by_key["PORT"].suggested_value == "3000"
    assert by_key["PORT"].comment == "App port"
    assert by_key["DATABASE_URL"].suggested_value.startswith("postgresql://")
    assert by_key["API_SECRET"].is_secret is True
    assert by_key["API_SECRET"].suggested_value == ""
    assert by_key["REDIS_URL"].suggested_value == "redis://localhost:6379/0"


def test_collect_env_example_from_apps(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("ROOT=1\nSECRET_KEY=x\n", encoding="utf-8")
    app = tmp_path / "apps" / "api"
    app.mkdir(parents=True)
    (app / ".env.sample").write_text("API_URL=http://localhost:8000\n", encoding="utf-8")
    vars_ = collect_env_example_vars(tmp_path)
    keys = [v.key for v in vars_]
    assert keys.index("ROOT") < keys.index("API_URL")
    assert "SECRET_KEY" in keys
    secret = next(v for v in vars_ if v.key == "SECRET_KEY")
    assert secret.suggested_value == ""


def test_suggested_datastore_urls() -> None:
    pg = suggested_datastore_urls("postgres", app_name="my-app")
    assert "postgres:5432" in pg["in_cluster"]
    assert "USER:PASSWORD" in pg["external"]
    redis = suggested_datastore_urls("redis")
    assert redis["in_cluster"] == "redis://redis:6379/0"

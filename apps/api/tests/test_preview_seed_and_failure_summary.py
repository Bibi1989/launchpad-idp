"""Tests for preview seed discovery and failure summary helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.preview_seed import (
    discover_seed_artifacts,
    seed_job_manifests,
)


def test_discover_seed_sql_and_shell(tmp_path: Path) -> None:
    (tmp_path / "seed.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "seed.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")

    plan = discover_seed_artifacts(tmp_path)
    assert len(plan.artifacts) == 2
    assert plan.artifacts[0].kind == "sql"
    assert plan.artifacts[0].relative_path == "seed.sql"
    assert plan.artifacts[1].kind == "shell"
    assert plan.artifacts[1].relative_path == "scripts/seed.sh"


def test_discover_seed_prefers_scripts_sql(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "seed.sql").write_text("SELECT 1;\n", encoding="utf-8")
    plan = discover_seed_artifacts(tmp_path)
    assert len(plan.artifacts) == 1
    assert plan.artifacts[0].relative_path == "scripts/seed.sql"


def test_discover_seed_empty(tmp_path: Path) -> None:
    plan = discover_seed_artifacts(tmp_path)
    assert plan.empty


def test_seed_job_manifests_sql() -> None:
    docs = seed_job_manifests(
        namespace="env-demo",
        kind="sql",
        relative_path="seed.sql",
        content="SELECT 1;",
        job_name="launchpad-seed-sql-1",
        config_map_name="launchpad-seed-sql-1-cm",
    )
    assert len(docs) == 2
    assert docs[0]["kind"] == "ConfigMap"
    assert docs[1]["kind"] == "Job"
    job = docs[1]
    assert job["metadata"]["namespace"] == "env-demo"  # type: ignore[index]
    container = job["spec"]["template"]["spec"]["containers"][0]  # type: ignore[index]
    assert "psql" in " ".join(container["command"])  # type: ignore[index]


@pytest.mark.asyncio
async def test_fallback_summary_on_empty_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import environment_failure_summary as mod

    class _FakeAnalyzer:
        gemini_configured = False

        async def analyze(self, bundle, *, correlation_id=None):  # noqa: ANN001
            del bundle, correlation_id
            from app.services.preview_analyzer import PreviewAnalyzerError

            raise PreviewAnalyzerError("No telemetry available to analyze")

    monkeypatch.setattr(mod, "PreviewAnalyzerService", _FakeAnalyzer)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _FakeFactory:
        def __call__(self):
            return _FakeSession()

    # Bypass DB log fetch by making get_by_id path unused; summarize opens session for logs.
    class _FakeLogRepo:
        def __init__(self, session):  # noqa: ANN001
            del session

        async def list_for_environment(self, environment_id, *, limit=80):  # noqa: ANN001
            del environment_id, limit
            return []

    monkeypatch.setattr(mod, "DeploymentLogRepository", _FakeLogRepo)

    summary = await mod.summarize_environment_failure(
        _FakeFactory(),  # type: ignore[arg-type]
        environment_id=__import__("uuid").uuid4(),
        error_text="CrashLoopBackOff: container app failed",
    )
    assert summary is not None
    assert "CrashLoopBackOff" in summary

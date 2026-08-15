"""Auto-generate a short failure summary when preview provision/rebuild fails."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.repositories.environment import DeploymentLogRepository, EnvironmentRepository
from app.services.preview_analyzer import PreviewAnalyzerError, PreviewAnalyzerService
from app.services.security_telemetry import collect_telemetry

logger = get_logger(__name__)

_MAX_SUMMARY_CHARS = 1_200
_MAX_LOG_LINES = 80


async def summarize_environment_failure(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    environment_id: UUID,
    error_text: str,
    correlation_id: str | None = None,
) -> str | None:
    """Best-effort LLM/heuristic summary from recent deployment logs + error text.

    Never raises: analyzer or DB failures return None so the fail path still completes.
    """
    try:
        async with session_factory() as session:
            log_repo = DeploymentLogRepository(session)
            entries = await log_repo.list_for_environment(
                environment_id,
                limit=_MAX_LOG_LINES,
            )
            messages = [entry.message for entry in entries if entry.message]
            if error_text and (not messages or messages[-1] != error_text):
                messages.append(error_text)

        if not messages and not error_text.strip():
            return None

        bundle = collect_telemetry(
            kubernetes_logs="\n".join(messages[-_MAX_LOG_LINES:]),
            environment_log_messages=messages[-_MAX_LOG_LINES:],
        )
        analyzer = PreviewAnalyzerService()
        report = await analyzer.analyze(
            bundle,
            correlation_id=correlation_id or str(environment_id),
        )
        summary = (report.summary or "").strip()
        if not summary:
            return None
        if len(summary) > _MAX_SUMMARY_CHARS:
            summary = summary[: _MAX_SUMMARY_CHARS - 1].rstrip() + "…"
        return summary
    except PreviewAnalyzerError as exc:
        logger.info(
            "failure_summary_analyzer_skipped",
            environment_id=str(environment_id),
            error=str(exc),
        )
        return _fallback_summary(error_text)
    except Exception:
        logger.exception(
            "failure_summary_failed",
            environment_id=str(environment_id),
        )
        return _fallback_summary(error_text)


def _fallback_summary(error_text: str) -> str | None:
    text = " ".join((error_text or "").split())
    if not text:
        return None
    if len(text) > 280:
        text = text[:279].rstrip() + "…"
    return text


async def persist_failure_summary(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    environment_id: UUID,
    summary: str | None,
) -> None:
    """Write failure_summary on the environment row (best-effort)."""
    if not summary:
        return
    try:
        async with session_factory() as session:
            env_repo = EnvironmentRepository(session)
            environment = await env_repo.get_by_id(environment_id)
            if environment is None:
                return
            environment.failure_summary = summary
            await session.commit()
    except Exception:
        logger.exception(
            "failure_summary_persist_failed",
            environment_id=str(environment_id),
        )

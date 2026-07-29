"""Gemini-powered Dockerfile security audit with structured output."""

from __future__ import annotations

import asyncio
import re

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.dockerfile_schema import (
    DOCKERFILE_SECURITY_REPORT_JSON_SCHEMA,
    DockerfileSecurityIssue,
    DockerfileSecurityReport,
    DockerfileSeverity,
    ProjectStack,
)
from app.services.dockerfile_scaffold import scaffold_dockerfile

logger = get_logger(__name__)

_SYSTEM_INSTRUCTION = """You are the Launchpad Dockerfile Security Auditor (2026 standards).
Review the provided Dockerfile and produce a structured security report.

Hard requirements for improvedDockerfile:
- Multi-stage builds (builder + minimal runtime)
- Non-root execution via USER 10001 (or distroless nonroot with explicit note)
- Prefer alpine or distroless base images; never use :latest
- Pin major/minor base tags at minimum (e.g. node:22-alpine, python:3.12-alpine)
- Optimize layer caching (deps before source copy)
- Zero plain-text secrets, tokens, or private keys
- No unnecessary packages in the final stage
- Prefer COPY --chown for non-root ownership

Populate securityIssues with concrete ruleIds such as:
RUN_AS_ROOT, UNPINNED_BASE_IMAGE, LATEST_TAG, LEAKED_SECRET,
MISSING_MULTI_STAGE, PRIVILEGED_PACKAGE, WRITABLE_WORKDIR.

Return a complete improvedDockerfile, not a diff snippet.
Do not invent CVEs. Do not include real credentials in output."""


class DockerfileSecurityError(RuntimeError):
    """Security review failed."""


class DockerfileSecurityService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def gemini_configured(self) -> bool:
        return bool(self._settings.gemini_api_key)

    async def review(
        self,
        dockerfile_content: str,
        *,
        stack: ProjectStack | None = None,
        source_path: str | None = None,
        correlation_id: str | None = None,
    ) -> DockerfileSecurityReport:
        content = dockerfile_content.strip()
        if not content:
            raise DockerfileSecurityError("Dockerfile content is empty")

        if self.gemini_configured:
            try:
                report = await asyncio.to_thread(
                    self._review_with_gemini,
                    content,
                    stack,
                    source_path,
                    correlation_id,
                )
                return report.model_copy(update={"analysisSource": "gemini"})
            except Exception:
                logger.exception(
                    "dockerfile_gemini_review_failed",
                    correlation_id=correlation_id,
                )
                if not self._settings.preview_analyzer_heuristic_fallback:
                    raise DockerfileSecurityError("Gemini Dockerfile review failed") from None

        report = self._heuristic_report(content, stack=stack or ProjectStack.UNKNOWN)
        return report.model_copy(update={"analysisSource": "heuristic"})

    def _review_with_gemini(
        self,
        content: str,
        stack: ProjectStack | None,
        source_path: str | None,
        correlation_id: str | None,
    ) -> DockerfileSecurityReport:
        from google import genai
        from google.genai import types

        api_key = self._settings.gemini_api_key
        if not api_key:
            raise DockerfileSecurityError("GEMINI_API_KEY is not configured")

        client = genai.Client(api_key=api_key)
        model = self._settings.gemini_model

        parts = [
            "Perform a Dockerfile security audit and return a hardened multi-stage rewrite.",
        ]
        if source_path:
            parts.append(f"Source path: {source_path}")
        if stack:
            parts.append(f"Detected stack: {stack.value}")
        clipped = content if len(content) <= 40_000 else content[:40_000] + "\n# …[truncated]"
        parts.append(f"```dockerfile\n{clipped}\n```")

        logger.info(
            "dockerfile_gemini_review_start",
            model=model,
            correlation_id=correlation_id,
            source_path=source_path,
            stack=stack.value if stack else None,
        )

        response = client.models.generate_content(
            model=model,
            contents="\n\n".join(parts),
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                temperature=0.2,
                response_mime_type="application/json",
                response_json_schema=DOCKERFILE_SECURITY_REPORT_JSON_SCHEMA,
            ),
        )

        raw_text = (response.text or "").strip()
        if not raw_text:
            raise DockerfileSecurityError("Gemini returned an empty response")

        report = DockerfileSecurityReport.model_validate_json(raw_text)
        if not report.improvedDockerfile.strip():
            raise DockerfileSecurityError("Gemini returned an empty improvedDockerfile")

        logger.info(
            "dockerfile_gemini_review_ok",
            model=model,
            correlation_id=correlation_id,
            issue_count=len(report.securityIssues),
            has_multi_stage=report.hasMultiStage,
        )
        return report

    def _heuristic_report(
        self,
        content: str,
        *,
        stack: ProjectStack,
    ) -> DockerfileSecurityReport:
        issues: list[DockerfileSecurityIssue] = []
        lines = content.splitlines()
        from_count = 0

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            upper = stripped.upper()

            if upper.startswith("FROM "):
                from_count += 1
                image = stripped[5:].split(" AS ")[0].split(" as ")[0].strip()
                if image.endswith(":latest") or ":" not in image.split("/")[-1]:
                    issues.append(
                        DockerfileSecurityIssue(
                            ruleId="UNPINNED_BASE_IMAGE",
                            severity=DockerfileSeverity.HIGH,
                            description=f"Base image is unpinned or uses :latest ({image})",
                            lineNumber=idx,
                        )
                    )
                if ":latest" in image:
                    issues.append(
                        DockerfileSecurityIssue(
                            ruleId="LATEST_TAG",
                            severity=DockerfileSeverity.HIGH,
                            description="Avoid :latest tags for reproducible builds",
                            lineNumber=idx,
                        )
                    )

            if upper.startswith("USER ") and upper in {"USER ROOT", "USER 0", "USER 0:0"}:
                issues.append(
                    DockerfileSecurityIssue(
                        ruleId="RUN_AS_ROOT",
                        severity=DockerfileSeverity.CRITICAL,
                        description="Container explicitly runs as root",
                        lineNumber=idx,
                    )
                )

            if re.search(
                r"(?i)(api[_-]?key|secret|password|token|private[_-]?key)\s*=\s*\S+",
                stripped,
            ) and not stripped.lstrip().startswith("#"):
                issues.append(
                    DockerfileSecurityIssue(
                        ruleId="LEAKED_SECRET",
                        severity=DockerfileSeverity.CRITICAL,
                        description="Possible plain-text secret in Dockerfile instruction",
                        lineNumber=idx,
                    )
                )

        has_user = any(line.strip().upper().startswith("USER ") for line in lines)
        if not has_user:
            issues.append(
                DockerfileSecurityIssue(
                    ruleId="RUN_AS_ROOT",
                    severity=DockerfileSeverity.CRITICAL,
                    description="No USER directive — image defaults to root",
                    lineNumber=None,
                )
            )
        elif not any(re.search(r"(?i)^USER\s+10001", line.strip()) for line in lines):
            if not any("nonroot" in line.lower() for line in lines if line.strip().upper().startswith("USER ")):
                issues.append(
                    DockerfileSecurityIssue(
                        ruleId="RUN_AS_ROOT",
                        severity=DockerfileSeverity.MEDIUM,
                        description="Prefer numeric non-root USER 10001 for portability",
                        lineNumber=None,
                    )
                )

        has_multi_stage = from_count >= 2
        if not has_multi_stage:
            issues.append(
                DockerfileSecurityIssue(
                    ruleId="MISSING_MULTI_STAGE",
                    severity=DockerfileSeverity.MEDIUM,
                    description="Single-stage Dockerfile increases attack surface",
                    lineNumber=None,
                )
            )

        improved = scaffold_dockerfile(stack, app_name="app", listen_port=8080)
        explanations = [
            "Converted to multi-stage build with minimal runtime image",
            "Enforced non-root USER 10001 (or distroless nonroot)",
            "Pinned alpine/distroless base tags; removed :latest",
            "Removed opportunities for plain-text secrets in image layers",
            "Reordered layers for dependency cache efficiency",
        ]

        critical = sum(1 for i in issues if i.severity == DockerfileSeverity.CRITICAL)
        high = sum(1 for i in issues if i.severity == DockerfileSeverity.HIGH)
        summary = (
            f"Found {len(issues)} issue(s) ({critical} critical, {high} high). "
            "A hardened multi-stage Dockerfile was generated from stack heuristics."
        )

        return DockerfileSecurityReport(
            summary=summary,
            securityIssues=issues,
            hasMultiStage=has_multi_stage,
            improvedDockerfile=improved,
            explanationOfChanges=explanations,
            analysisSource="heuristic",
        )

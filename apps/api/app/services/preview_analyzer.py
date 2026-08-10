"""Gemini-powered Launch Preview Analyzer with structured diagnostic output."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.diagnostic import (
    DIAGNOSTIC_REPORT_JSON_SCHEMA,
    DiagnosticCategory,
    DiagnosticPatch,
    DiagnosticReport,
    DiagnosticSeverity,
    SecurityDetails,
)
from app.services.security_telemetry import SastFinding, TelemetryBundle, TrivyFinding

logger = get_logger(__name__)

_SYSTEM_INSTRUCTION = """You are the Launchpad Launch Preview Analyzer.
Analyze multi-source failure telemetry from CI/CD logs, Kubernetes runtime events,
Trivy container vulnerability SARIF findings, and CodeQL/SAST findings.

Classify the primary failure into exactly one category:
CONTAINER_VULNERABILITY, SAST_CODE_SECURITY, RUNTIME_CRASH, or CONFIGURATION_ERROR.

Prefer security categories when high/critical CVE or SAST findings explain the failure.
For RUNTIME_CRASH use CrashLoopBackOff, OOMKilled, probe failures, and similar signals.
For CONFIGURATION_ERROR use manifest/Helm/env misconfiguration without a crash or CVE root cause.

Populate securityDetails for CONTAINER_VULNERABILITY and SAST_CODE_SECURITY.
When enough context exists, propose a concrete patch (Dockerfile, package.json,
infra/values.yaml, or source file). Keep actionableSteps ordered and specific.
Do not invent CVEs or file paths that are absent from the telemetry."""


class PreviewAnalyzerError(RuntimeError):
    """Analyzer failed to produce a valid diagnostic report."""


class PreviewAnalyzerService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def gemini_configured(self) -> bool:
        return bool(self._settings.gemini_api_key)

    async def analyze(
        self,
        bundle: TelemetryBundle,
        *,
        manifest_snippets: dict[str, str] | None = None,
        correlation_id: str | None = None,
    ) -> DiagnosticReport:
        if not bundle.to_llm_context().strip():
            raise PreviewAnalyzerError("No telemetry available to analyze")

        if self.gemini_configured:
            try:
                report = await asyncio.to_thread(
                    self._analyze_with_gemini,
                    bundle,
                    manifest_snippets or {},
                    correlation_id,
                )
                return report.model_copy(update={"analysisSource": "gemini"})
            except Exception:
                logger.exception(
                    "gemini_analyze_failed",
                    correlation_id=correlation_id,
                )
                if not self._settings.preview_analyzer_heuristic_fallback:
                    raise PreviewAnalyzerError("Gemini analysis failed") from None

        report = self._heuristic_report(bundle, manifest_snippets or {})
        if not self.gemini_configured:
            notice = (
                "Gemini is not configured (set GEMINI_API_KEY on the API). "
                "Showing heuristic analysis only."
            )
            report = report.model_copy(
                update={
                    "analysisSource": "heuristic",
                    "summary": f"{notice} {report.summary}".strip(),
                }
            )
            return report
        return report.model_copy(update={"analysisSource": "heuristic"})

    def _analyze_with_gemini(
        self,
        bundle: TelemetryBundle,
        manifest_snippets: dict[str, str],
        correlation_id: str | None,
    ) -> DiagnosticReport:
        from google import genai
        from google.genai import types

        api_key = self._settings.gemini_api_key
        if not api_key:
            raise PreviewAnalyzerError("GEMINI_API_KEY is not configured")

        client = genai.Client(api_key=api_key)
        model = self._settings.gemini_model

        user_parts = [
            "Analyze the following Launchpad preview failure telemetry and produce a diagnostic report.",
            bundle.to_llm_context(),
        ]
        if manifest_snippets:
            snippet_lines = ["## Manifest / source snippets for patch context"]
            for path, content in list(manifest_snippets.items())[:12]:
                clipped = content if len(content) <= 4_000 else content[:4_000] + "\n…[truncated]"
                snippet_lines.append(f"### {path}\n```\n{clipped}\n```")
            user_parts.append("\n".join(snippet_lines))

        logger.info(
            "gemini_analyze_start",
            model=model,
            correlation_id=correlation_id,
            source_kinds=bundle.source_kinds,
        )

        response = client.models.generate_content(
            model=model,
            contents="\n\n".join(user_parts),
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                temperature=0.2,
                response_mime_type="application/json",
                response_json_schema=DIAGNOSTIC_REPORT_JSON_SCHEMA,
            ),
        )

        raw_text = (response.text or "").strip()
        if not raw_text:
            raise PreviewAnalyzerError("Gemini returned an empty response")

        report = DiagnosticReport.model_validate_json(raw_text)
        logger.info(
            "gemini_analyze_ok",
            model=model,
            correlation_id=correlation_id,
            category=report.category.value,
            severity=report.severity.value,
        )
        return report

    def _heuristic_report(
        self,
        bundle: TelemetryBundle,
        manifest_snippets: dict[str, str],
    ) -> DiagnosticReport:
        if bundle.trivy_findings:
            return self._from_trivy(bundle.trivy_findings[0], bundle)
        if bundle.sast_findings:
            return self._from_sast(bundle.sast_findings[0], bundle)
        if bundle.runtime_signals:
            return self._from_runtime(bundle)
        return DiagnosticReport(
            summary=(
                "The preview pipeline failed, but no high-confidence security or "
                "runtime signal was extracted from the provided telemetry."
            ),
            category=DiagnosticCategory.CONFIGURATION_ERROR,
            severity=DiagnosticSeverity.MEDIUM,
            rootCauseAnalysis=(
                "Log excerpts did not include Trivy CVEs, CodeQL rule hits, or "
                "Kubernetes crash markers. Review CI configuration, manifest "
                "selectors, and image references."
            ),
            actionableSteps=[
                "Inspect the full CI/CD job log for the first failing step.",
                "Verify workload image, ports, and env vars in the preview manifests.",
                "Re-run with Trivy/CodeQL artifacts attached if a security gate failed.",
            ],
            patch=self._dockerfile_hint(manifest_snippets),
        )

    def _from_trivy(self, finding: TrivyFinding, bundle: TelemetryBundle) -> DiagnosticReport:
        sev = _map_severity(finding.severity)
        component = f"{finding.package_name}@{finding.installed_version}"
        upgrade = (
            f"Upgrade {finding.package_name} to {finding.fixed_version}"
            if finding.fixed_version not in {"", "unknown"}
            else f"Upgrade or rebuild the base image to eliminate {finding.cve_id}"
        )
        return DiagnosticReport(
            summary=(
                f"Container security scan failed on {finding.cve_id} "
                f"({finding.severity}) in {component}. "
                f"The Trivy gate blocked the preview pipeline."
            ),
            category=DiagnosticCategory.CONTAINER_VULNERABILITY,
            severity=sev,
            securityDetails=SecurityDetails(
                cveOrRuleId=finding.cve_id,
                affectedComponent=component,
                recommendedUpgrade=upgrade,
            ),
            rootCauseAnalysis=(
                f"{finding.cve_id} affects {component}. "
                f"Fixed version reported: {finding.fixed_version}. "
                f"Additional Trivy findings in this run: {len(bundle.trivy_findings)}. "
                "Shipping this image exposes production workloads to known container CVEs."
            ),
            actionableSteps=[
                upgrade,
                "Rebuild the preview image and re-run the container-security-scan job.",
                "Confirm Trivy severity threshold matches org policy (CRITICAL/HIGH).",
            ],
            patch=DiagnosticPatch(
                targetFile="Dockerfile",
                originalContent="# Vulnerable package present in base/runtime image layers",
                suggestedContent=(
                    f"# Ensure {finding.package_name} >= {finding.fixed_version}\n"
                    "# Prefer a maintained base tag (e.g. node:20-alpine) and rebuild"
                ),
            ),
        )

    def _from_sast(self, finding: SastFinding, bundle: TelemetryBundle) -> DiagnosticReport:
        sev = _map_severity(finding.severity)
        loc = finding.file_path
        if finding.start_line is not None:
            loc = f"{finding.file_path}:{finding.start_line}"
        return DiagnosticReport(
            summary=(
                f"SAST analysis flagged {finding.rule_id} at {loc}. "
                "The code security gate failed before or during the preview pipeline."
            ),
            category=DiagnosticCategory.SAST_CODE_SECURITY,
            severity=sev,
            securityDetails=SecurityDetails(
                cveOrRuleId=finding.rule_id,
                affectedComponent=loc,
                recommendedUpgrade=f"Remediate {finding.rule_id} in {loc}",
            ),
            rootCauseAnalysis=(
                f"{finding.description or finding.rule_id} "
                f"({len(bundle.sast_findings)} SAST finding(s) total). "
                "Merging without a fix risks exploitable code paths in production."
            ),
            actionableSteps=[
                f"Open {loc} and address the {finding.rule_id} finding.",
                "Add / strengthen input validation or parameterized queries as required.",
                "Re-run the sast-code-scan job and confirm the alert is cleared.",
            ],
            patch=DiagnosticPatch(
                targetFile=finding.file_path if finding.file_path != "unknown" else "src/",
                originalContent="# Vulnerable pattern flagged by SAST",
                suggestedContent="# Apply the secure coding fix for this rule and re-scan",
            ),
        )

    def _from_runtime(self, bundle: TelemetryBundle) -> DiagnosticReport:
        top = bundle.runtime_signals[0]
        severity = (
            DiagnosticSeverity.CRITICAL
            if top.marker in {"OOMKilled", "CrashLoopBackOff"}
            else DiagnosticSeverity.HIGH
        )
        steps = [
            "Run kubectl describe pod and kubectl logs on the failing preview pod.",
            "Check resource requests/limits and liveness/readiness probes.",
            "Verify the workload image starts locally before redeploying.",
        ]
        if top.marker == "OOMKilled":
            steps.insert(0, "Raise memory limits or reduce the process memory footprint.")
        return DiagnosticReport(
            summary=(
                f"Preview runtime failed with {top.marker}. "
                "The workload did not become healthy in the preview namespace."
            ),
            category=DiagnosticCategory.RUNTIME_CRASH,
            severity=severity,
            rootCauseAnalysis=(
                f"Kubernetes signal '{top.marker}' was observed: {top.context}. "
                "This typically indicates process crash, probe failure, or resource exhaustion."
            ),
            actionableSteps=steps,
            patch=(
                DiagnosticPatch(
                    targetFile="infra/values.yaml",
                    originalContent="resources:\n  limits:\n    memory: 256Mi",
                    suggestedContent="resources:\n  limits:\n    memory: 512Mi",
                )
                if top.marker == "OOMKilled"
                else None
            ),
        )

    def _dockerfile_hint(self, snippets: dict[str, str]) -> DiagnosticPatch | None:
        for path, content in snippets.items():
            if path.endswith("Dockerfile") or path == "Dockerfile":
                return DiagnosticPatch(
                    targetFile=path,
                    originalContent=content.splitlines()[0] if content else "FROM …",
                    suggestedContent="# Review base image tag and rebuild",
                )
        return None


def _map_severity(raw: str) -> DiagnosticSeverity:
    try:
        return DiagnosticSeverity(raw)
    except ValueError:
        return DiagnosticSeverity.MEDIUM


def build_analyze_context_dict(bundle: TelemetryBundle) -> dict[str, Any]:
    return bundle.to_summary()

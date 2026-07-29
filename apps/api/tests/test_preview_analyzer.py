"""Tests for Trivy / CodeQL SARIF parsers and Launch Preview Analyzer heuristics."""

from __future__ import annotations

import json

import pytest

from app.schemas.diagnostic import (
    DIAGNOSTIC_REPORT_JSON_SCHEMA,
    DiagnosticCategory,
    DiagnosticReport,
    DiagnosticSeverity,
)
from app.services.preview_analyzer import PreviewAnalyzerService
from app.services.security_telemetry import (
    collect_telemetry,
    parse_codeql_sarif,
    parse_sast_text_logs,
    parse_trivy_sarif,
)


TRIVY_SARIF = {
    "version": "2.1.0",
    "runs": [
        {
            "tool": {
                "driver": {
                    "name": "Trivy",
                    "rules": [
                        {
                            "id": "CVE-2023-44487",
                            "shortDescription": {"text": "CVE-2023-44487"},
                            "fullDescription": {
                                "text": (
                                    "Package: openssl\nInstalled Version: 1.1.1t\n"
                                    "Fixed Version: 1.1.1u"
                                )
                            },
                            "properties": {"security-severity": "CRITICAL"},
                        }
                    ],
                }
            },
            "results": [
                {
                    "ruleId": "CVE-2023-44487",
                    "level": "error",
                    "message": {
                        "text": (
                            "Package: openssl\nInstalled Version: 1.1.1t\n"
                            "Fixed Version: 1.1.1u"
                        )
                    },
                    "properties": {"security-severity": "CRITICAL"},
                }
            ],
        }
    ],
}

CODEQL_SARIF = {
    "version": "2.1.0",
    "runs": [
        {
            "tool": {"driver": {"name": "CodeQL", "rules": []}},
            "results": [
                {
                    "ruleId": "js/sql-injection",
                    "level": "error",
                    "message": {"text": "This query depends on a user-provided value."},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": "src/db.ts"},
                                "region": {"startLine": 42},
                            }
                        }
                    ],
                }
            ],
        }
    ],
}


def test_parse_trivy_sarif_extracts_cve_package_versions() -> None:
    findings = parse_trivy_sarif(TRIVY_SARIF)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.cve_id == "CVE-2023-44487"
    assert finding.package_name == "openssl"
    assert finding.installed_version == "1.1.1t"
    assert finding.fixed_version == "1.1.1u"
    assert finding.severity == "CRITICAL"


def test_parse_trivy_sarif_from_json_string() -> None:
    findings = parse_trivy_sarif(json.dumps(TRIVY_SARIF))
    assert findings[0].cve_id == "CVE-2023-44487"


def test_parse_codeql_sarif_extracts_rule_and_location() -> None:
    findings = parse_codeql_sarif(CODEQL_SARIF)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "js/sql-injection"
    assert finding.file_path == "src/db.ts"
    assert finding.start_line == 42
    assert "user-provided" in finding.description


def test_parse_sast_text_logs() -> None:
    text = "src/api/auth.ts:18: error: js/path-injection: Unsanitized path from request"
    findings = parse_sast_text_logs(text)
    assert len(findings) == 1
    assert findings[0].rule_id == "js/path-injection"
    assert findings[0].file_path == "src/api/auth.ts"
    assert findings[0].start_line == 18


def test_collect_telemetry_runtime_markers() -> None:
    bundle = collect_telemetry(
        kubernetes_logs="Pod status: CrashLoopBackOff\nLast State: OOMKilled",
    )
    markers = {s.marker for s in bundle.runtime_signals}
    assert "CrashLoopBackOff" in markers
    assert "OOMKilled" in markers
    assert "kubernetes_runtime" in bundle.source_kinds


@pytest.mark.asyncio
async def test_heuristic_analyzer_prefers_trivy() -> None:
    bundle = collect_telemetry(trivy_sarif=TRIVY_SARIF)
    service = PreviewAnalyzerService()
    report = await service.analyze(bundle)
    assert report.category == DiagnosticCategory.CONTAINER_VULNERABILITY
    assert report.severity == DiagnosticSeverity.CRITICAL
    assert report.securityDetails is not None
    assert report.securityDetails.cveOrRuleId == "CVE-2023-44487"
    assert report.analysisSource == "heuristic"
    assert report.patch is not None
    assert report.patch.targetFile == "Dockerfile"


@pytest.mark.asyncio
async def test_heuristic_analyzer_sast() -> None:
    bundle = collect_telemetry(codeql_sarif=CODEQL_SARIF)
    report = await PreviewAnalyzerService().analyze(bundle)
    assert report.category == DiagnosticCategory.SAST_CODE_SECURITY
    assert report.securityDetails is not None
    assert report.securityDetails.affectedComponent == "src/db.ts:42"


@pytest.mark.asyncio
async def test_heuristic_analyzer_runtime() -> None:
    bundle = collect_telemetry(kubernetes_logs="Back-off restarting failed container: CrashLoopBackOff")
    report = await PreviewAnalyzerService().analyze(bundle)
    assert report.category == DiagnosticCategory.RUNTIME_CRASH


@pytest.mark.asyncio
async def test_analyzer_rejects_empty_telemetry() -> None:
    from app.services.preview_analyzer import PreviewAnalyzerError

    with pytest.raises(PreviewAnalyzerError):
        await PreviewAnalyzerService().analyze(collect_telemetry())


def test_diagnostic_report_schema_required_fields() -> None:
    required = set(DIAGNOSTIC_REPORT_JSON_SCHEMA["required"])
    assert required == {
        "summary",
        "category",
        "severity",
        "rootCauseAnalysis",
        "actionableSteps",
    }
    report = DiagnosticReport(
        summary="Pipeline failed due to a critical CVE in openssl.",
        category=DiagnosticCategory.CONTAINER_VULNERABILITY,
        severity=DiagnosticSeverity.CRITICAL,
        rootCauseAnalysis="CVE-2023-44487 in openssl@1.1.1t.",
        actionableSteps=["Upgrade openssl", "Rebuild image"],
    )
    payload = report.model_dump()
    assert "rootCauseAnalysis" in payload
    assert "actionableSteps" in payload

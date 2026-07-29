"""Launch Preview Analyzer diagnostic report schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DiagnosticCategory(str, Enum):
    CONTAINER_VULNERABILITY = "CONTAINER_VULNERABILITY"
    SAST_CODE_SECURITY = "SAST_CODE_SECURITY"
    RUNTIME_CRASH = "RUNTIME_CRASH"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"


class DiagnosticSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SecurityDetails(BaseModel):
    cveOrRuleId: str = Field(description="e.g., CVE-2024-1234 or js/sql-injection")
    affectedComponent: str = Field(description="Vulnerable package or file:line")
    recommendedUpgrade: str = Field(description="Remediation target version or fix")


class DiagnosticPatch(BaseModel):
    targetFile: str
    originalContent: str
    suggestedContent: str


class DiagnosticReport(BaseModel):
    """Structured Gemini output — camelCase for UI consumption."""

    summary: str
    category: DiagnosticCategory
    severity: DiagnosticSeverity
    securityDetails: SecurityDetails | None = None
    rootCauseAnalysis: str
    actionableSteps: list[str] = Field(min_length=1)
    patch: DiagnosticPatch | None = None
    analysisSource: str = Field(
        default="gemini",
        description="gemini | heuristic",
    )


class AnalyzePreviewRequest(BaseModel):
    """Optional telemetry overrides; environment logs load when environment_id is set."""

    model_config = ConfigDict(populate_by_name=True)

    cicdLogs: str | None = Field(default=None, max_length=500_000)
    kubernetesLogs: str | None = Field(default=None, max_length=500_000)
    trivySarif: dict[str, Any] | str | None = None
    codeqlSarif: dict[str, Any] | str | None = None
    sastLogs: str | None = Field(default=None, max_length=500_000)
    manifestSnippets: dict[str, str] | None = Field(
        default=None,
        description="Optional file path → content map for patch context",
    )
    includeEnvironmentLogs: bool = True


class AnalyzePreviewResponse(BaseModel):
    report: DiagnosticReport
    telemetrySummary: dict[str, Any] = Field(default_factory=dict)


# JSON Schema handed to Gemini (camelCase property names matching UI contract).
DIAGNOSTIC_REPORT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "Concise 2-sentence summary explaining why the pipeline or deployment failed."
            ),
        },
        "category": {
            "type": "string",
            "enum": [c.value for c in DiagnosticCategory],
            "description": "High-level classification of the failure.",
        },
        "severity": {
            "type": "string",
            "enum": [s.value for s in DiagnosticSeverity],
            "description": "Overall impact severity level.",
        },
        "securityDetails": {
            "type": "object",
            "properties": {
                "cveOrRuleId": {
                    "type": "string",
                    "description": "e.g., CVE-2024-1234 or js/sql-injection",
                },
                "affectedComponent": {
                    "type": "string",
                    "description": "Vulnerable package or file line",
                },
                "recommendedUpgrade": {
                    "type": "string",
                    "description": "Remediation target version or fix",
                },
            },
            "required": ["cveOrRuleId", "affectedComponent", "recommendedUpgrade"],
            "description": (
                "Security-specific findings when category is "
                "CONTAINER_VULNERABILITY or SAST_CODE_SECURITY."
            ),
        },
        "rootCauseAnalysis": {
            "type": "string",
            "description": "In-depth technical breakdown of the root cause.",
        },
        "actionableSteps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Ordered list of resolution steps for the user.",
        },
        "patch": {
            "type": "object",
            "properties": {
                "targetFile": {
                    "type": "string",
                    "description": (
                        "Path to file needing updates (e.g. Dockerfile, infra/values.yaml)"
                    ),
                },
                "originalContent": {
                    "type": "string",
                    "description": "Original snippet or block",
                },
                "suggestedContent": {
                    "type": "string",
                    "description": "Corrected snippet or block",
                },
            },
            "required": ["targetFile", "originalContent", "suggestedContent"],
            "description": "Specific file patch to fix the issue.",
        },
    },
    "required": [
        "summary",
        "category",
        "severity",
        "rootCauseAnalysis",
        "actionableSteps",
    ],
}

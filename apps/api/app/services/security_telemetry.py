"""Parse CI/CD, Kubernetes, Trivy SARIF, and CodeQL/SAST telemetry for the analyzer."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.logging import get_logger

logger = get_logger(__name__)

SeverityLevel = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]

_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
_PACKAGE_RE = re.compile(
    r"(?:Package|Pkg(?:Name)?|Component)\s*[:=]\s*([^\s\n,]+)",
    re.IGNORECASE,
)
_INSTALLED_RE = re.compile(
    r"(?:Installed(?:\s+Version)?|Current)\s*[:=]\s*([^\s\n,]+)",
    re.IGNORECASE,
)
_FIXED_RE = re.compile(
    r"(?:Fixed(?:\s+Version)?|Fix(?:ed)?)\s*[:=]\s*([^\s\n,]+)",
    re.IGNORECASE,
)
_CODEQL_RULE_RE = re.compile(
    r"\b((?:js|py|java|go|rb|cs|cpp|c|swift|kotlin)/[\w./-]+)\b",
)
_RUNTIME_MARKERS = (
    "CrashLoopBackOff",
    "OOMKilled",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerConfigError",
    "RunContainerError",
    "FailedScheduling",
    "Liveness probe failed",
    "Readiness probe failed",
    "Back-off restarting failed container",
)


@dataclass(frozen=True, slots=True)
class TrivyFinding:
    cve_id: str
    package_name: str
    installed_version: str
    fixed_version: str
    severity: SeverityLevel
    message: str


@dataclass(frozen=True, slots=True)
class SastFinding:
    rule_id: str
    file_path: str
    start_line: int | None
    description: str
    severity: SeverityLevel


@dataclass(frozen=True, slots=True)
class RuntimeSignal:
    marker: str
    context: str


@dataclass(slots=True)
class TelemetryBundle:
    trivy_findings: list[TrivyFinding] = field(default_factory=list)
    sast_findings: list[SastFinding] = field(default_factory=list)
    runtime_signals: list[RuntimeSignal] = field(default_factory=list)
    cicd_excerpt: str = ""
    kubernetes_excerpt: str = ""
    source_kinds: list[str] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        return {
            "sourceKinds": list(self.source_kinds),
            "trivyCount": len(self.trivy_findings),
            "sastCount": len(self.sast_findings),
            "runtimeSignalCount": len(self.runtime_signals),
            "topTrivy": [
                {
                    "cveId": f.cve_id,
                    "package": f.package_name,
                    "installed": f.installed_version,
                    "fixed": f.fixed_version,
                    "severity": f.severity,
                }
                for f in self.trivy_findings[:5]
            ],
            "topSast": [
                {
                    "ruleId": f.rule_id,
                    "file": f.file_path,
                    "line": f.start_line,
                    "severity": f.severity,
                }
                for f in self.sast_findings[:5]
            ],
            "runtimeMarkers": [s.marker for s in self.runtime_signals[:8]],
        }

    def to_llm_context(self, *, max_chars: int = 48_000) -> str:
        sections: list[str] = []
        if self.trivy_findings:
            lines = ["## Trivy container vulnerabilities (parsed)"]
            for finding in self.trivy_findings[:40]:
                lines.append(
                    f"- {finding.cve_id} | pkg={finding.package_name} "
                    f"installed={finding.installed_version} fixed={finding.fixed_version} "
                    f"severity={finding.severity}"
                )
                if finding.message:
                    lines.append(f"  note: {finding.message[:300]}")
            sections.append("\n".join(lines))
        if self.sast_findings:
            lines = ["## CodeQL / SAST findings (parsed)"]
            for finding in self.sast_findings[:40]:
                loc = finding.file_path
                if finding.start_line is not None:
                    loc = f"{loc}:{finding.start_line}"
                lines.append(
                    f"- {finding.rule_id} | {loc} | severity={finding.severity}"
                )
                if finding.description:
                    lines.append(f"  {finding.description[:400]}")
            sections.append("\n".join(lines))
        if self.runtime_signals:
            lines = ["## Kubernetes runtime signals"]
            for signal in self.runtime_signals[:20]:
                lines.append(f"- {signal.marker}: {signal.context[:400]}")
            sections.append("\n".join(lines))
        if self.cicd_excerpt.strip():
            sections.append(f"## CI/CD logs\n{self.cicd_excerpt.strip()}")
        if self.kubernetes_excerpt.strip():
            sections.append(f"## Kubernetes logs / describe\n{self.kubernetes_excerpt.strip()}")
        text = "\n\n".join(sections)
        if len(text) > max_chars:
            return text[: max_chars - 20] + "\n…[truncated]"
        return text


def _as_dict(payload: dict[str, Any] | str | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload
    text = payload.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("sarif_json_parse_failed", length=len(text))
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _normalize_severity(raw: str | None) -> SeverityLevel:
    if not raw:
        return "UNKNOWN"
    value = raw.strip().upper()
    if value in {"CRITICAL", "ERROR", "FATAL"}:
        return "CRITICAL" if value == "CRITICAL" else "HIGH"
    if value in {"HIGH", "MEDIUM", "LOW", "WARNING", "NOTE", "INFO"}:
        if value == "WARNING":
            return "MEDIUM"
        if value in {"NOTE", "INFO"}:
            return "LOW"
        return value  # type: ignore[return-value]
    return "UNKNOWN"


def _message_text(result: dict[str, Any]) -> str:
    message = result.get("message")
    if isinstance(message, dict):
        text = message.get("text")
        return str(text) if text is not None else ""
    if isinstance(message, str):
        return message
    return ""


def _rule_lookup(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tool = run.get("tool")
    if not isinstance(tool, dict):
        return {}
    driver = tool.get("driver")
    if not isinstance(driver, dict):
        return {}
    rules = driver.get("rules")
    if not isinstance(rules, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for rule in rules:
        if isinstance(rule, dict) and isinstance(rule.get("id"), str):
            out[rule["id"]] = rule
    return out


def _severity_from_result(
    result: dict[str, Any],
    rule: dict[str, Any] | None,
) -> SeverityLevel:
    props = result.get("properties")
    if isinstance(props, dict):
        for key in ("security-severity", "security_severity", "severity"):
            if key in props:
                return _normalize_severity(str(props[key]))
    if rule:
        rule_props = rule.get("properties")
        if isinstance(rule_props, dict):
            for key in ("security-severity", "security_severity", "severity", "problem.severity"):
                if key in rule_props:
                    return _normalize_severity(str(rule_props[key]))
        default_config = rule.get("defaultConfiguration")
        if isinstance(default_config, dict) and "level" in default_config:
            return _normalize_severity(str(default_config["level"]))
    level = result.get("level")
    if isinstance(level, str):
        return _normalize_severity(level)
    return "UNKNOWN"


def _extract_pkg_versions(text: str) -> tuple[str, str, str]:
    pkg = ""
    installed = ""
    fixed = ""
    pkg_m = _PACKAGE_RE.search(text)
    if pkg_m:
        pkg = pkg_m.group(1).strip()
    inst_m = _INSTALLED_RE.search(text)
    if inst_m:
        installed = inst_m.group(1).strip()
    fix_m = _FIXED_RE.search(text)
    if fix_m:
        fixed = fix_m.group(1).strip()
    return pkg, installed, fixed


def parse_trivy_sarif(payload: dict[str, Any] | str | None) -> list[TrivyFinding]:
    """Extract CVE / package / version / severity from Trivy SARIF JSON."""
    data = _as_dict(payload)
    if data is None:
        return []
    runs = data.get("runs")
    if not isinstance(runs, list):
        return []

    findings: list[TrivyFinding] = []
    seen: set[tuple[str, str, str]] = set()

    for run in runs:
        if not isinstance(run, dict):
            continue
        rules = _rule_lookup(run)
        results = run.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            rule_id = str(result.get("ruleId") or "")
            message = _message_text(result)
            rule = rules.get(rule_id)
            cve_match = _CVE_RE.search(rule_id) or _CVE_RE.search(message)
            if rule and not cve_match:
                short = rule.get("shortDescription")
                if isinstance(short, dict) and isinstance(short.get("text"), str):
                    cve_match = _CVE_RE.search(short["text"])
            cve_id = (cve_match.group(0) if cve_match else rule_id or "UNKNOWN").upper()
            if cve_id.startswith("cve-"):
                cve_id = cve_id.upper()

            pkg, installed, fixed = _extract_pkg_versions(message)
            if not pkg and rule:
                full = rule.get("fullDescription")
                if isinstance(full, dict) and isinstance(full.get("text"), str):
                    pkg2, inst2, fix2 = _extract_pkg_versions(full["text"])
                    pkg = pkg or pkg2
                    installed = installed or inst2
                    fixed = fixed or fix2

            # Trivy often encodes package in artifact URI or partialFingerprints
            if not pkg:
                locations = result.get("locations")
                if isinstance(locations, list) and locations:
                    loc0 = locations[0]
                    if isinstance(loc0, dict):
                        phys = loc0.get("physicalLocation")
                        if isinstance(phys, dict):
                            art = phys.get("artifactLocation")
                            if isinstance(art, dict) and isinstance(art.get("uri"), str):
                                uri = art["uri"]
                                # e.g. "library/openssl" or "openssl"
                                pkg = uri.rsplit("/", 1)[-1]

            severity = _severity_from_result(result, rule)
            key = (cve_id, pkg, installed)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                TrivyFinding(
                    cve_id=cve_id,
                    package_name=pkg or "unknown",
                    installed_version=installed or "unknown",
                    fixed_version=fixed or "unknown",
                    severity=severity,
                    message=message[:500],
                )
            )

    findings.sort(
        key=lambda f: (
            {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}[f.severity],
            f.cve_id,
        )
    )
    return findings


def _location_path_line(result: dict[str, Any]) -> tuple[str, int | None]:
    locations = result.get("locations")
    if not isinstance(locations, list) or not locations:
        return "", None
    loc0 = locations[0]
    if not isinstance(loc0, dict):
        return "", None
    phys = loc0.get("physicalLocation")
    if not isinstance(phys, dict):
        return "", None
    path = ""
    art = phys.get("artifactLocation")
    if isinstance(art, dict) and isinstance(art.get("uri"), str):
        path = art["uri"]
    line: int | None = None
    region = phys.get("region")
    if isinstance(region, dict) and isinstance(region.get("startLine"), int):
        line = region["startLine"]
    return path, line


def parse_codeql_sarif(payload: dict[str, Any] | str | None) -> list[SastFinding]:
    """Extract rule IDs, file paths, lines, and descriptions from CodeQL/SAST SARIF."""
    data = _as_dict(payload)
    if data is None:
        return []
    runs = data.get("runs")
    if not isinstance(runs, list):
        return []

    findings: list[SastFinding] = []
    seen: set[tuple[str, str, int | None]] = set()

    for run in runs:
        if not isinstance(run, dict):
            continue
        rules = _rule_lookup(run)
        results = run.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            rule_id = str(result.get("ruleId") or "unknown")
            message = _message_text(result)
            rule = rules.get(rule_id)
            description = message
            if rule:
                short = rule.get("shortDescription")
                if isinstance(short, dict) and isinstance(short.get("text"), str):
                    description = short["text"] or description
                full = rule.get("fullDescription")
                if isinstance(full, dict) and isinstance(full.get("text"), str) and not description:
                    description = full["text"]
            path, line = _location_path_line(result)
            severity = _severity_from_result(result, rule)
            key = (rule_id, path, line)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                SastFinding(
                    rule_id=rule_id,
                    file_path=path or "unknown",
                    start_line=line,
                    description=description[:800],
                    severity=severity,
                )
            )
    return findings


def parse_sast_text_logs(text: str | None) -> list[SastFinding]:
    """Best-effort parse of CodeQL / Semgrep text alert dumps."""
    if not text or not text.strip():
        return []
    findings: list[SastFinding] = []
    seen: set[tuple[str, str, int | None]] = set()

    # Patterns like: src/db.ts:42: error: js/sql-injection: ...
    line_re = re.compile(
        r"(?P<path>[\w./\\-]+\.\w+):(?P<line>\d+)(?::\d+)?\s*[:\-]?\s*"
        r"(?:error|warning|note)?\s*[:\-]?\s*"
        r"(?P<rule>(?:js|py|java|go|rb|cs|cpp|c|swift|kotlin)/[\w./-]+|"
        r"[\w.-]+(?:\.[\w.-]+)+)\s*[:\-]?\s*(?P<desc>.*)",
        re.IGNORECASE,
    )
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = line_re.search(stripped)
        if match:
            rule_id = match.group("rule")
            path = match.group("path")
            line = int(match.group("line"))
            desc = match.group("desc").strip()
        else:
            rule_m = _CODEQL_RULE_RE.search(stripped)
            if not rule_m:
                continue
            rule_id = rule_m.group(1)
            path = "unknown"
            line = None
            desc = stripped
        key = (rule_id, path, line)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            SastFinding(
                rule_id=rule_id,
                file_path=path,
                start_line=line,
                description=desc[:800],
                severity="HIGH",
            )
        )
    return findings


def extract_runtime_signals(*texts: str | None) -> list[RuntimeSignal]:
    signals: list[RuntimeSignal] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for marker in _RUNTIME_MARKERS:
            if marker not in text:
                continue
            if marker in seen:
                continue
            idx = text.find(marker)
            start = max(0, idx - 80)
            end = min(len(text), idx + len(marker) + 200)
            context = " ".join(text[start:end].split())
            seen.add(marker)
            signals.append(RuntimeSignal(marker=marker, context=context))
    return signals


def _excerpt(text: str | None, *, limit: int = 12_000) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[-limit:]


def collect_telemetry(
    *,
    cicd_logs: str | None = None,
    kubernetes_logs: str | None = None,
    trivy_sarif: dict[str, Any] | str | None = None,
    codeql_sarif: dict[str, Any] | str | None = None,
    sast_logs: str | None = None,
    environment_log_messages: list[str] | None = None,
) -> TelemetryBundle:
    """Merge multi-source telemetry into a single analyzer-ready bundle."""
    env_blob = "\n".join(environment_log_messages or [])
    combined_logs = "\n".join(
        part for part in (cicd_logs, kubernetes_logs, env_blob, sast_logs) if part
    )

    # Auto-detect embedded SARIF JSON blobs inside log streams
    detected_trivy = trivy_sarif
    detected_codeql = codeql_sarif
    if detected_trivy is None and "trivy-results.sarif" in combined_logs.lower():
        embedded = _extract_embedded_json(combined_logs, marker="runs")
        if embedded is not None:
            detected_trivy = embedded
    if detected_codeql is None and (
        "codeql" in combined_logs.lower() or '"$schema"' in combined_logs
    ):
        # Prefer explicit SARIF with codeql tool name
        for candidate in _iter_embedded_json_objects(combined_logs):
            tool_blob = json.dumps(candidate.get("runs", [])[:1]).lower()
            if "codeql" in tool_blob or "semgrep" in tool_blob:
                detected_codeql = candidate
                break

    trivy = parse_trivy_sarif(detected_trivy)
    sast = parse_codeql_sarif(detected_codeql)
    sast.extend(parse_sast_text_logs(sast_logs))
    # Deduplicate SAST by rule+path+line
    sast_dedup: dict[tuple[str, str, int | None], SastFinding] = {
        (f.rule_id, f.file_path, f.start_line): f for f in sast
    }
    sast = list(sast_dedup.values())

    runtime = extract_runtime_signals(kubernetes_logs, cicd_logs, env_blob)
    kinds: list[str] = []
    if trivy:
        kinds.append("trivy_sarif")
    if sast:
        kinds.append("codeql_sast")
    if runtime:
        kinds.append("kubernetes_runtime")
    if cicd_logs or env_blob:
        kinds.append("cicd_logs")

    return TelemetryBundle(
        trivy_findings=trivy,
        sast_findings=sast,
        runtime_signals=runtime,
        cicd_excerpt=_excerpt(cicd_logs or env_blob),
        kubernetes_excerpt=_excerpt(kubernetes_logs),
        source_kinds=kinds,
    )


def _extract_embedded_json(text: str, *, marker: str) -> dict[str, Any] | None:
    for candidate in _iter_embedded_json_objects(text):
        if marker in candidate:
            return candidate
    return None


def _iter_embedded_json_objects(text: str) -> list[dict[str, Any]]:
    """Scan for top-level JSON objects (best-effort, bounded)."""
    found: list[dict[str, Any]] = []
    starts = [m.start() for m in re.finditer(r"\{", text)]
    for start in starts[:30]:
        depth = 0
        for idx in range(start, min(len(text), start + 2_000_000)):
            ch = text[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start : idx + 1]
                    try:
                        parsed = json.loads(chunk)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        found.append(parsed)
                    break
    return found

"""Analyze workspace CI/CD, Docker, IaC, and Kubernetes files with Gemini + heuristics."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

WorkspaceAnalysisKind = Literal["cicd", "docker", "iac", "kubernetes"]


class WorkspaceFileIssue(BaseModel):
    title: str
    description: str
    severity: Literal["info", "warning", "critical"] = "warning"
    ruleId: str | None = None


class WorkspaceFileAnalyzeRequest(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1, max_length=400_000)
    kind: WorkspaceAnalysisKind | Literal["auto"] = "auto"
    error_context: str | None = Field(
        default=None,
        max_length=20_000,
        description="Optional sandbox/CLI error output to guide a targeted fix",
    )


class WorkspaceFileAnalyzeResponse(BaseModel):
    kind: WorkspaceAnalysisKind
    summary: str
    issues: list[WorkspaceFileIssue] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    improvedContent: str | None = None
    analysisSource: Literal["gemini", "heuristic"] = "heuristic"


# Gemini Schema rejects OpenAPI union types like ["string","null"]; use nullable.
_REPORT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["cicd", "docker", "iac", "kubernetes"]},
        "summary": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                    "ruleId": {"type": "string", "nullable": True},
                },
                "required": ["title", "description", "severity"],
            },
        },
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "improvedContent": {"type": "string", "nullable": True},
    },
    "required": ["kind", "summary", "issues", "suggestions", "improvedContent"],
}

_SYSTEM_INSTRUCTION = """You are the Launchpad Workspace File Analyzer.
Review the provided infrastructure / delivery file and return a structured report.

Domains:
- cicd: GitHub Actions / GitLab CI pipelines (pin actions, least privilege, scans, secrets)
- docker: Dockerfiles / compose (non-root, pinned tags, multi-stage, healthchecks)
- iac: Terraform / OpenTofu / Pulumi (state, least privilege, tags, secrets handling)
- kubernetes: Deployment/Service/Ingress/Helm values (probes, resources, securityContext)

Rules:
- Prefer concrete, actionable suggestions.
- When proposing improvedContent, return a full revised file (not a diff).
- Do not invent CVEs or secrets. Do not include real credentials.
- Keep improvedContent null when the file is already solid and only suggestions apply.
- If a sandbox/CLI error_context is provided, prioritize diagnosing and fixing that failure
  (syntax, missing providers, invalid arguments, auth/config issues reflected in the file).
  Put the root cause in summary/issues and return fixed improvedContent when a file change helps.
- For Launchpad Terraform roots: providers.tf is the canonical place for provider
  "google" / "kubernetes" / "aws" / "azurerm" blocks and terraform.required_providers.
  Never duplicate those blocks into main.tf. If main.tf has provider blocks, remove them
  from main.tf (keep modules/resources only).
"""


class WorkspaceFileAnalyzerError(RuntimeError):
    """Workspace file analysis failed."""


def _inject_dockerfile_nonroot_user(content: str) -> str | None:
    """Append USER 10001 before final CMD/ENTRYPOINT when no USER is present."""
    if re.search(r"(?im)^\s*USER\s+", content):
        return None
    lines = content.splitlines(keepends=True)
    insert_at = len(lines)
    for idx in range(len(lines) - 1, -1, -1):
        stripped = lines[idx].strip().upper()
        if stripped.startswith("CMD ") or stripped.startswith("ENTRYPOINT ") or stripped.startswith("HEALTHCHECK "):
            insert_at = idx
            break
    block = "USER 10001\n"
    if insert_at > 0 and lines[insert_at - 1].strip():
        block = "\n" + block
    lines.insert(insert_at, block)
    fixed = "".join(lines)
    if not fixed.endswith("\n"):
        fixed += "\n"
    return fixed if fixed != content else None


def _inject_github_workflow_permissions(content: str) -> str | None:
    """Insert least-privilege top-level permissions before jobs: when missing."""
    if re.search(r"(?m)^permissions\s*:", content):
        return None
    jobs_match = re.search(r"(?m)^jobs\s*:", content)
    if not jobs_match:
        return None
    block = "permissions:\n  contents: read\n\n"
    fixed = content[: jobs_match.start()] + block + content[jobs_match.start() :]
    return fixed if fixed != content else None


def _postgres_needs_pgdata_fix(lower: str) -> bool:
    """True for a non-root postgres Deployment with a data volume but no PGDATA env.

    Checks specifically for the ``PGDATA`` env var (``name: pgdata``) rather than
    the bare substring "pgdata", which also appears in ``subPath: pgdata``.
    """
    return (
        "kind: deployment" in lower
        and "/var/lib/postgresql/data" in lower
        and ("image: postgres" in lower or "name: postgres" in lower)
        and "runasuser:" in lower
        and "runasuser: 0" not in lower
        and "name: pgdata" not in lower
    )


def _inject_pgdata_env(content: str) -> str | None:
    """Insert a PGDATA sub-directory env into the postgres container's env block.

    Returns the patched content, or None when no suitable postgres ``env:`` block
    is found. Indentation is inferred from the first env item so the result stays
    valid YAML regardless of the manifest's indent width.
    """
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip() != "env:":
            continue
        env_indent = len(line) - len(line.lstrip())
        # Inspect the block that follows to confirm it is the postgres env and to
        # learn the item indentation.
        for j in range(i + 1, min(i + 10, len(lines))):
            item = lines[j]
            stripped = item.strip()
            if not stripped:
                continue
            item_indent = len(item) - len(item.lstrip())
            # Dedented past the env block without finding an item -> not it.
            if item_indent <= env_indent and not stripped.startswith("-"):
                break
            if stripped.startswith("- name:"):
                block_text = "\n".join(lines[i : i + 14]).upper()
                if "POSTGRES_" not in block_text:
                    break
                pad = " " * item_indent
                vpad = " " * (item_indent + 2)
                insert = [
                    f"{pad}- name: PGDATA",
                    f"{vpad}value: /var/lib/postgresql/data/pgdata",
                ]
                return "\n".join(lines[: i + 1] + insert + lines[i + 1 :])
    return None


def detect_kind_from_path(path: str) -> WorkspaceAnalysisKind:
    normalized = path.replace("\\", "/").lower().lstrip("./")
    base = normalized.rsplit("/", 1)[-1]
    if (
        base == "dockerfile"
        or base.startswith("dockerfile.")
        or "/dockers/" in f"/{normalized}"
        or normalized.startswith("dockers/")
        or base.endswith("docker-compose.yml")
        or base.endswith("docker-compose.yaml")
        or base in {"compose.yml", "compose.yaml"}
    ):
        return "docker"
    if (
        ".github/workflows/" in normalized
        or "ci/github/" in normalized
        or "ci/gitlab/" in normalized
        or base == ".gitlab-ci.yml"
        or base.endswith(".gitlab-ci.yml")
    ):
        return "cicd"
    if (
        "infra/k8s/" in normalized
        or "infra/helm/" in normalized
        or "/manifests/" in f"/{normalized}"
        or re.search(r"(^|/)(deployment|service|ingress|hpa|namespace)\.ya?ml$", normalized)
    ):
        return "kubernetes"
    if (
        "infra/terraform/" in normalized
        or "infra/pulumi/" in normalized
        or "/terraform/" in f"/{normalized}"
        or "/pulumi/" in f"/{normalized}"
        or base.endswith(".tf")
        or base.endswith(".tfvars")
        or base in {"pulumi.yaml", "pulumi.yml"}
    ):
        return "iac"
    if base.endswith(".tf") or base.endswith(".tfvars"):
        return "iac"
    if base.endswith(".yml") or base.endswith(".yaml"):
        return "kubernetes"
    return "iac"


class WorkspaceFileAnalyzerService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def gemini_configured(self) -> bool:
        return bool(self._settings.gemini_api_key)

    async def analyze(
        self,
        *,
        path: str,
        content: str,
        kind: WorkspaceAnalysisKind | Literal["auto"] = "auto",
        error_context: str | None = None,
        correlation_id: str | None = None,
    ) -> WorkspaceFileAnalyzeResponse:
        text = content.strip()
        if not text:
            raise WorkspaceFileAnalyzerError("File content is empty")

        resolved: WorkspaceAnalysisKind = (
            detect_kind_from_path(path) if kind == "auto" else kind  # type: ignore[assignment]
        )
        err_ctx = (error_context or "").strip() or None

        if self.gemini_configured:
            try:
                report = await asyncio.to_thread(
                    self._analyze_with_gemini,
                    path,
                    text,
                    resolved,
                    err_ctx,
                    correlation_id,
                )
                return report.model_copy(update={"analysisSource": "gemini", "kind": resolved})
            except Exception:
                logger.exception(
                    "workspace_file_gemini_analyze_failed",
                    correlation_id=correlation_id,
                    path=path,
                )
                if not self._settings.preview_analyzer_heuristic_fallback:
                    raise WorkspaceFileAnalyzerError("Gemini analysis failed") from None

        report = self._heuristic_report(path, text, resolved, err_ctx)
        if not self.gemini_configured:
            notice = (
                "Gemini is not configured (set GEMINI_API_KEY). "
                "Showing heuristic analysis only."
            )
            summary = f"{notice} {report.summary}".strip()
            return report.model_copy(
                update={"analysisSource": "heuristic", "summary": summary, "kind": resolved}
            )
        return report.model_copy(update={"analysisSource": "heuristic", "kind": resolved})

    def _analyze_with_gemini(
        self,
        path: str,
        content: str,
        kind: WorkspaceAnalysisKind,
        error_context: str | None,
        correlation_id: str | None,
    ) -> WorkspaceFileAnalyzeResponse:
        from google import genai
        from google.genai import types

        api_key = self._settings.gemini_api_key
        if not api_key:
            raise WorkspaceFileAnalyzerError("GEMINI_API_KEY is not configured")

        client = genai.Client(api_key=api_key)
        error_block = ""
        if error_context:
            error_block = (
                f"\n----- SANDBOX / CLI ERROR CONTEXT -----\n"
                f"{error_context[:12_000]}\n"
                f"----- END ERROR CONTEXT -----\n"
            )
        prompt = (
            f"Analyze this Launchpad workspace file.\n"
            f"path: {path}\n"
            f"kind: {kind}\n"
            f"correlation_id: {correlation_id or 'n/a'}\n"
            f"{error_block}\n"
            f"----- FILE START -----\n{content}\n----- FILE END -----\n"
        )
        response = client.models.generate_content(
            model=self._settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                temperature=0.2,
                response_mime_type="application/json",
                response_json_schema=_REPORT_JSON_SCHEMA,
            ),
        )
        raw = (getattr(response, "text", None) or "").strip()
        if not raw:
            raise WorkspaceFileAnalyzerError("Gemini returned an empty response")
        payload = json.loads(raw)
        return WorkspaceFileAnalyzeResponse.model_validate(payload)

    def _heuristic_report(
        self,
        path: str,
        content: str,
        kind: WorkspaceAnalysisKind,
        error_context: str | None = None,
    ) -> WorkspaceFileAnalyzeResponse:
        if kind == "docker":
            return self._heuristic_docker(path, content)
        if kind == "cicd":
            return self._heuristic_cicd(path, content)
        if kind == "kubernetes":
            return self._heuristic_kubernetes(path, content)
        return self._heuristic_iac(path, content, error_context)

    def _heuristic_docker(self, path: str, content: str) -> WorkspaceFileAnalyzeResponse:
        issues: list[WorkspaceFileIssue] = []
        suggestions: list[str] = []
        improved: str | None = None
        lower = content.lower()
        if ":latest" in lower:
            issues.append(
                WorkspaceFileIssue(
                    title="Unpinned latest tag",
                    description="Avoid :latest base images; pin major/minor tags for reproducible builds.",
                    severity="critical",
                    ruleId="LATEST_TAG",
                )
            )
            suggestions.append("Pin base images (e.g. node:22-alpine, python:3.12-alpine).")
        if "user " not in lower:
            issues.append(
                WorkspaceFileIssue(
                    title="Missing non-root USER",
                    description="Containers should run as a non-root user (e.g. USER 10001).",
                    severity="warning",
                    ruleId="RUN_AS_ROOT",
                )
            )
            suggestions.append("Add USER 10001 (or distroless nonroot) in the final stage.")
            fixed = _inject_dockerfile_nonroot_user(content)
            if fixed and fixed != content:
                improved = fixed
        if "as " not in lower and "from " in lower:
            issues.append(
                WorkspaceFileIssue(
                    title="Single-stage build",
                    description="Prefer multi-stage builds to keep runtime images small.",
                    severity="info",
                    ruleId="MISSING_MULTI_STAGE",
                )
            )
            suggestions.append("Split builder and runtime stages.")
        if "healthcheck" not in lower and "docker-compose" not in path.lower():
            suggestions.append("Add a HEALTHCHECK for readiness at the listen port.")
        if not issues:
            suggestions.append("Dockerfile looks reasonably hardened; keep tags pinned and non-root.")
        return WorkspaceFileAnalyzeResponse(
            kind="docker",
            summary=(
                "Heuristic Docker review proposed a non-root USER fix."
                if improved
                else "Heuristic Docker review completed."
            ),
            issues=issues,
            suggestions=suggestions,
            improvedContent=improved,
        )

    def _heuristic_cicd(self, path: str, content: str) -> WorkspaceFileAnalyzeResponse:
        issues: list[WorkspaceFileIssue] = []
        suggestions: list[str] = []
        improved: str | None = None
        if re.search(r"uses:\s*[^\s@]+@v\d", content):
            issues.append(
                WorkspaceFileIssue(
                    title="Unpinned GitHub Action tag",
                    description="Prefer commit SHA pins for supply-chain safety.",
                    severity="warning",
                    ruleId="UNPINNED_ACTION",
                )
            )
            suggestions.append("Pin actions to full commit SHAs (with version comments).")
        if "trivy" not in content.lower() and "container" in content.lower():
            suggestions.append("Add a container vulnerability scan stage (Trivy) before deploy.")
        if "permissions:" not in content and "github" in path.lower():
            suggestions.append("Declare least-privilege `permissions:` for the workflow.")
            fixed = _inject_github_workflow_permissions(content)
            if fixed and fixed != content:
                improved = fixed
                issues.append(
                    WorkspaceFileIssue(
                        title="Missing workflow permissions",
                        description="GitHub Actions workflows should declare least-privilege permissions.",
                        severity="warning",
                        ruleId="MISSING_PERMISSIONS",
                    )
                )
        if "secrets." in content.lower() and "environment:" not in content.lower():
            suggestions.append("Consider GitHub Environments for production secret gating.")
        if not issues and not suggestions:
            suggestions.append("Pipeline looks structured; keep scanners and least privilege enabled.")
        return WorkspaceFileAnalyzeResponse(
            kind="cicd",
            summary=(
                "Heuristic CI/CD review proposed least-privilege permissions."
                if improved
                else "Heuristic CI/CD review completed."
            ),
            issues=issues,
            suggestions=suggestions or ["Enable SAST + container scanning on main branch pushes."],
            improvedContent=improved,
        )

    def _heuristic_kubernetes(self, path: str, content: str) -> WorkspaceFileAnalyzeResponse:
        issues: list[WorkspaceFileIssue] = []
        suggestions: list[str] = []
        improved: str | None = None
        lower = content.lower()

        # Auto-fix: postgres running as a non-root UID with a mounted data volume
        # but no PGDATA sub-directory. initdb cannot chmod the volume mount point
        # it does not own, so the pod crashes ("could not change permissions of
        # directory ... Operation not permitted"). Point PGDATA at a sub-dir.
        if _postgres_needs_pgdata_fix(lower):
            issues.append(
                WorkspaceFileIssue(
                    title="Postgres crashes as non-root without PGDATA sub-directory",
                    description=(
                        "This postgres Deployment runs as a non-root UID with a volume "
                        "mounted at /var/lib/postgresql/data but no PGDATA env. initdb "
                        "cannot chmod the mount point it does not own, so the pod fails "
                        "with 'could not change permissions of directory ... Operation "
                        "not permitted'. Set PGDATA to a sub-directory of the mount."
                    ),
                    severity="critical",
                    ruleId="POSTGRES_PGDATA_SUBDIR",
                )
            )
            fixed = _inject_pgdata_env(content)
            if fixed and fixed != content:
                improved = fixed
                suggestions.append(
                    "Set PGDATA=/var/lib/postgresql/data/pgdata on the postgres container."
                )

        if "kind: deployment" in lower:
            if "readinessprobe" not in lower:
                issues.append(
                    WorkspaceFileIssue(
                        title="Missing readinessProbe",
                        description="Deployments should expose readiness probes so traffic waits for healthy pods.",
                        severity="warning",
                        ruleId="MISSING_READINESS",
                    )
                )
            if "resources:" not in lower:
                issues.append(
                    WorkspaceFileIssue(
                        title="Missing resource requests/limits",
                        description="Set CPU/memory requests and limits for scheduling and noisy-neighbor protection.",
                        severity="warning",
                        ruleId="MISSING_RESOURCES",
                    )
                )
            if "securitycontext" not in lower:
                suggestions.append("Add pod/container securityContext (runAsNonRoot, drop ALL caps).")
            if "imagepullpolicy: always" in lower:
                suggestions.append("Prefer IfNotPresent for tagged immutable images in previews.")
        if not issues:
            suggestions.append("Manifest looks workable; verify probes match the container listen port.")
        return WorkspaceFileAnalyzeResponse(
            kind="kubernetes",
            summary=(
                "Heuristic Kubernetes review found a pod-crashing issue and proposed a fix."
                if improved
                else "Heuristic Kubernetes review completed."
            ),
            issues=issues,
            suggestions=suggestions,
            improvedContent=improved,
        )

    def _heuristic_iac(
        self,
        path: str,
        content: str,
        error_context: str | None = None,
    ) -> WorkspaceFileAnalyzeResponse:
        issues: list[WorkspaceFileIssue] = []
        suggestions: list[str] = []
        improved: str | None = None
        lower = content.lower()
        err = (error_context or "").strip()
        if err:
            snippet = err[-500:].replace("\n", " ")
            issues.append(
                WorkspaceFileIssue(
                    title="Sandbox command failed",
                    description=(
                        "A guided IaC step exited non-zero. Review the error and fix providers, "
                        f"syntax, or required variables. Last output: {snippet}"
                    ),
                    severity="critical",
                    ruleId="SANDBOX_STEP_FAILED",
                )
            )
            suggestions.append(
                "Fix the IaC file (or credentials) based on the sandbox error, then retry the step."
            )
            if "no such file" in err.lower() or "directory not found" in err.lower():
                suggestions.append("Confirm terraform/pulumi files exist under infra/ and paths match.")
            if "authentication" in err.lower() or "credentials" in err.lower() or "unauthorized" in err.lower():
                suggestions.append("Re-enter cloud credentials in the guided wizard, then retry.")
            if (
                "accessnotconfigured" in err.lower()
                or "has not been used" in err.lower()
                or "api has not been enabled" in err.lower()
                or ("is disabled" in err.lower() and "googleapis.com" in err.lower())
            ):
                issues.append(
                    WorkspaceFileIssue(
                        title="Required Google API not enabled",
                        description=(
                            "Terraform cannot manage google_project_service or call Google APIs "
                            "until Cloud Resource Manager + Service Usage are enabled on the "
                            "project. The failing API (often container.googleapis.com) is a "
                            "downstream symptom of cloudresourcemanager.googleapis.com being off."
                        ),
                        severity="critical",
                        ruleId="GCP_API_NOT_ENABLED",
                    )
                )
                suggestions.append(
                    "In the workspace Provision wizard, run the 'enable APIs' step first "
                    "(before terraform apply). Or open Google Cloud Console and enable "
                    "Cloud Resource Manager API, then Service Usage, Compute, and "
                    "Kubernetes Engine APIs for this project."
                )
                suggestions.append(
                    "CLI: gcloud services enable cloudresourcemanager.googleapis.com "
                    "serviceusage.googleapis.com compute.googleapis.com "
                    "container.googleapis.com --project=<your-project-id>"
                )
            if (
                "already exists" in err.lower()
                and ("secret" in err.lower() or "kubernetes_secret" in err.lower())
            ):
                issues.append(
                    WorkspaceFileIssue(
                        title="Kubernetes secret already exists",
                        description=(
                            "A kubernetes_secret create failed because the name already exists in the "
                            "target cluster (often local kind via ~/.kube/config). With GKE enabled, "
                            "regenerate so the kubernetes provider targets the GKE cluster instead."
                        ),
                        severity="critical",
                        ruleId="K8S_SECRET_ALREADY_EXISTS",
                    )
                )
                suggestions.append(
                    "Either: Update workspace (native K8s + GKE wires the provider to GKE), "
                    "delete the leftover secret "
                    "(kubectl delete secret lp-<env>-secrets -n default), "
                    "or import it into state."
                )
            if (
                "badrequest" in err.lower()
                and "international characters are allowed" in err.lower()
                and (
                    "google_container_cluster" in err.lower()
                    or "resource_labels" in err.lower()
                    or "labels" in err.lower()
                )
            ):
                issues.append(
                    WorkspaceFileIssue(
                        title="Invalid GCP label keys on GKE cluster",
                        description=(
                            "GCP resource_labels keys must be lowercase "
                            "([a-z][a-z0-9_-]*). PascalCase keys like EnvironmentId / "
                            "TTL_Expiration are rejected by the GKE API even when the "
                            "cluster name itself is valid."
                        ),
                        severity="critical",
                        ruleId="GCP_LABEL_KEY_CASE",
                    )
                )
                suggestions.append(
                    "Update/regenerate the workspace so resource_labels use "
                    "environment_id, owner, created_by, ttl_expiration (lowercase), "
                    "then re-run terraform apply."
                )
            if (
                "unsupported argument" in err.lower()
                and "labels" in err.lower()
                and (
                    "google_compute_network" in err.lower()
                    or "google_compute_subnetwork" in err.lower()
                )
            ):
                issues.append(
                    WorkspaceFileIssue(
                        title="Unsupported labels on VPC compute resources",
                        description=(
                            "google_compute_network and google_compute_subnetwork do not accept "
                            "labels. Remove labels blocks from those resources."
                        ),
                        severity="critical",
                        ruleId="GCP_VPC_UNSUPPORTED_LABELS",
                    )
                )
        vpc_fix = _strip_gcp_vpc_unsupported_labels(content)
        if vpc_fix is not None:
            improved = vpc_fix
            if not any(i.ruleId == "GCP_VPC_UNSUPPORTED_LABELS" for i in issues):
                issues.append(
                    WorkspaceFileIssue(
                        title="Unsupported labels on VPC compute resources",
                        description=(
                            "google_compute_network and google_compute_subnetwork do not accept "
                            "labels in the GCP provider. Remove those labels blocks."
                        ),
                        severity="critical",
                        ruleId="GCP_VPC_UNSUPPORTED_LABELS",
                    )
                )
            suggestions.append(
                "Remove labels from VPC network/subnet resources; keep governance labels on "
                "GKE, Cloud Run, Artifact Registry, and other label-capable resources."
            )

        providers_fix = _strip_duplicate_root_provider_blocks(path, content if improved is None else improved)
        if providers_fix is not None:
            improved = providers_fix
            if not any(i.ruleId == "DUPLICATE_TF_PROVIDERS" for i in issues):
                issues.append(
                    WorkspaceFileIssue(
                        title="Duplicate Terraform provider configurations",
                        description=(
                            "provider blocks belong in providers.tf only. Duplicate google/"
                            "kubernetes (or other) providers in main.tf cause Terraform CLI errors."
                        ),
                        severity="critical",
                        ruleId="DUPLICATE_TF_PROVIDERS",
                    )
                )
            suggestions.append(
                "Keep provider and required_providers blocks in providers.tf; main.tf should "
                "only declare modules and resources."
            )
        elif err and "duplicate provider configuration" in err.lower():
            if not any(i.ruleId == "DUPLICATE_TF_PROVIDERS" for i in issues):
                issues.append(
                    WorkspaceFileIssue(
                        title="Duplicate Terraform provider configurations",
                        description=(
                            "Terraform reported duplicate provider configurations. Remove "
                            "provider blocks from main.tf and keep providers.tf as the source."
                        ),
                        severity="critical",
                        ruleId="DUPLICATE_TF_PROVIDERS",
                    )
                )
            suggestions.append(
                "Open infra/terraform/main.tf and delete any provider \"google\" / "
                "provider \"kubernetes\" blocks (and duplicate terraform.required_providers)."
            )

        if "password" in lower or "secret" in lower:
            if "var." not in lower and "config.get" not in lower and "os.getenv" not in lower:
                issues.append(
                    WorkspaceFileIssue(
                        title="Possible hard-coded secret",
                        description="Avoid embedding secrets in IaC; inject via variables/secret managers.",
                        severity="critical",
                        ruleId="HARDCODED_SECRET",
                    )
                )
        if path.endswith(".tf") and "backend " not in lower and "terraform {" in lower:
            suggestions.append("Configure a remote state backend for team collaboration.")
        if "tags" not in lower and "labels" not in lower and improved is None:
            suggestions.append("Stamp EnvironmentId / Owner / TTL tags for governance.")
        if not issues and not suggestions:
            suggestions.append("IaC looks clean; keep credentials out of VCS and use remote state.")
        summary = (
            "Heuristic IaC review with sandbox error context."
            if err
            else "Heuristic IaC review completed."
        )
        if improved is not None:
            if "DUPLICATE_TF_PROVIDERS" in {i.ruleId for i in issues}:
                summary = (
                    "Removed duplicate provider blocks from main.tf (providers.tf is canonical). "
                    + summary
                )
            elif "GCP_VPC_UNSUPPORTED_LABELS" in {i.ruleId for i in issues}:
                summary = (
                    "Removed unsupported labels from VPC network/subnet resources. "
                    + summary
                )
        return WorkspaceFileAnalyzeResponse(
            kind="iac",
            summary=summary,
            issues=issues,
            suggestions=suggestions,
            improvedContent=improved,
        )


_GCP_VPC_RESOURCES_WITHOUT_LABELS = (
    "google_compute_network",
    "google_compute_subnetwork",
)
_LABELS_BLOCK_RE = re.compile(
    r'\n[ \t]*labels\s*=\s*\{[^{}]*\}',
    re.DOTALL,
)

_PROVIDER_BLOCK_RE = re.compile(
    r'(?m)^[ \t]*provider\s+"(?:google|kubernetes|aws|azurerm|cloudflare)"\s*\{',
)
_TERRAFORM_REQUIRED_PROVIDERS_RE = re.compile(
    r'(?ms)^[ \t]*terraform\s*\{[^{}]*required_providers\s*\{.*?^[ \t]*\}[ \t]*\n[ \t]*\}[ \t]*\n?',
)
_GOOGLE_CLIENT_CONFIG_DATA_RE = re.compile(
    r'(?ms)^[ \t]*data\s+"google_client_config"\s+"[^"]+"\s*\{[^{}]*\}[ \t]*\n?',
)


def _extract_hcl_brace_block(content: str, start: int) -> tuple[str, int] | None:
    """Return (block_text, end_index) for an HCL `{...}` starting at `start` (`{` index)."""
    if start < 0 or start >= len(content) or content[start] != "{":
        return None
    depth = 0
    i = start
    while i < len(content):
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1], i + 1
        i += 1
    return None


def _strip_duplicate_root_provider_blocks(path: str, content: str) -> str | None:
    """Remove provider/required_providers from root main.tf (providers.tf owns them)."""
    normalized = path.replace("\\", "/").lower()
    if not normalized.endswith("/main.tf") and not normalized.endswith("main.tf"):
        return None
    # Only rewrite Launchpad root main (modules live under modules/*/main.tf).
    if "/modules/" in normalized:
        return None
    if 'provider "' not in content and "required_providers" not in content:
        return None
    if 'module "' not in content and 'resource "' not in content:
        return None

    fixed = content
    # Drop standalone terraform { required_providers { ... } } blocks (providers.tf has these).
    fixed2, n_tf = _TERRAFORM_REQUIRED_PROVIDERS_RE.subn("\n", fixed)
    fixed = fixed2

    # Drop provider "…" { … } with brace matching.
    removed_providers = 0
    while True:
        match = _PROVIDER_BLOCK_RE.search(fixed)
        if not match:
            break
        brace_at = fixed.find("{", match.start())
        extracted = _extract_hcl_brace_block(fixed, brace_at)
        if extracted is None:
            break
        _, end = extracted
        # Include leading whitespace/newlines before provider keyword.
        line_start = fixed.rfind("\n", 0, match.start()) + 1
        fixed = fixed[:line_start] + fixed[end:].lstrip("\n")
        removed_providers += 1

    fixed2, n_data = _GOOGLE_CLIENT_CONFIG_DATA_RE.subn("", fixed)
    fixed = fixed2

    # Collapse excessive blank lines.
    fixed = re.sub(r"\n{3,}", "\n\n", fixed).strip() + "\n"

    if n_tf == 0 and removed_providers == 0 and n_data == 0:
        return None
    if fixed == content:
        return None
    return fixed


def _strip_gcp_vpc_unsupported_labels(content: str) -> str | None:
    """Remove labels from VPC resources that do not support them; else None."""
    if "labels" not in content:
        return None
    if not any(f'resource "{name}"' in content for name in _GCP_VPC_RESOURCES_WITHOUT_LABELS):
        return None

    changed = False
    fixed = content
    for resource_type in _GCP_VPC_RESOURCES_WITHOUT_LABELS:
        pattern = re.compile(
            rf'(resource\s+"{resource_type}"\s+"[^"]+"\s*\{{)'
            r'(.*?)'
            r'(\n\})',
            re.DOTALL,
        )
        flag = [False]

        def _fix_resource(match: re.Match[str], *, _flag: list[bool] = flag) -> str:
            header, body, closing = match.group(1), match.group(2), match.group(3)
            new_body, n = _LABELS_BLOCK_RE.subn("", body, count=1)
            if n:
                _flag[0] = True
            return header + new_body + closing

        fixed = pattern.sub(_fix_resource, fixed)
        if flag[0]:
            changed = True

    if not changed:
        return None
    return fixed


# Back-compat alias used by tests / older call sites.
def _strip_google_compute_network_labels(content: str) -> str | None:
    return _strip_gcp_vpc_unsupported_labels(content)

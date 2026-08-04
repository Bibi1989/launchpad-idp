"""Golden-path scorecard for catalog services (Dockerfile / CI / K8s resources)."""

from __future__ import annotations

from pathlib import Path

from app.schemas.catalog import ScorecardItem, ServiceScorecard


def compute_workspace_scorecard(workspace_root: str | Path) -> ServiceScorecard:
    root = Path(workspace_root)
    items: list[ScorecardItem] = []

    dockerfiles = list(root.glob("dockers/Dockerfile*")) + list(root.glob("**/Dockerfile"))
    dockerfile_text = ""
    if dockerfiles:
        dockerfile_text = dockerfiles[0].read_text(encoding="utf-8", errors="ignore")
    non_root = "USER " in dockerfile_text and "USER root" not in dockerfile_text
    slim = any(token in dockerfile_text.lower() for token in ("-slim", "alpine", "distroless"))
    docker_ok = bool(dockerfiles) and non_root and slim
    items.append(
        ScorecardItem(
            id="dockerfile_hardened",
            title="Dockerfile non-root + slim/alpine base",
            passed=docker_ok,
            points=30 if docker_ok else 0,
            max_points=30,
            detail="Found hardened Dockerfile" if docker_ok else "Missing USER non-root or slim base",
        )
    )

    ci_files = list(root.glob("ci/**/*.yml")) + list(root.glob("ci/**/*.yaml"))
    ci_files += list(root.glob(".github/workflows/*.yml"))
    ci_files += list(root.glob(".gitlab-ci.yml"))
    ci_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in ci_files[:8])
    has_scan = any(token in ci_text.lower() for token in ("trivy", "semgrep", "codeql", "container-security-scan"))
    items.append(
        ScorecardItem(
            id="ci_security",
            title="CI includes Trivy/SAST security scanning",
            passed=has_scan,
            points=30 if has_scan else 0,
            max_points=30,
            detail="Security stages present" if has_scan else "No Trivy/SAST/CodeQL detected in CI",
        )
    )

    k8s_files = list(root.glob("infra/k8s/**/*.yaml")) + list(root.glob("infra/helm/**/*.yaml"))
    k8s_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in k8s_files[:20])
    has_requests = "requests:" in k8s_text and "cpu:" in k8s_text
    has_limits = "limits:" in k8s_text and "memory:" in k8s_text
    k8s_ok = bool(k8s_files) and has_requests and has_limits
    items.append(
        ScorecardItem(
            id="k8s_resources",
            title="Kubernetes requests + limits set",
            passed=k8s_ok,
            points=40 if k8s_ok else 0,
            max_points=40,
            detail="Resource requests/limits present" if k8s_ok else "Missing requests/limits on workloads",
        )
    )

    score = sum(item.points for item in items)
    gate = 70
    return ServiceScorecard(score=score, gate=gate, passed=score >= gate, items=items)

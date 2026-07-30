from app.services.workspace_file_analyzer import (
    WorkspaceFileAnalyzerService,
    detect_kind_from_path,
)


def test_detect_kind_from_path() -> None:
    assert detect_kind_from_path("dockers/fastapi/Dockerfile") == "docker"
    assert detect_kind_from_path("ci/github/workflows/deploy.yml") == "cicd"
    assert detect_kind_from_path("infra/k8s/manifests/service.yaml") == "kubernetes"
    assert detect_kind_from_path("infra/terraform/main.tf") == "iac"


def test_heuristic_docker_flags_latest() -> None:
    service = WorkspaceFileAnalyzerService()
    report = service._heuristic_docker(
        "dockers/Dockerfile",
        "FROM node:latest\nCOPY . .\nCMD [\"node\",\"index.js\"]\n",
    )
    assert report.kind == "docker"
    assert any(issue.ruleId == "LATEST_TAG" for issue in report.issues)
    assert report.suggestions

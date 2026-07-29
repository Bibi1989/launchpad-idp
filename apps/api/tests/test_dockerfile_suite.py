"""Tests for Dockerfile scaffold, heuristic security review, and registry refs."""

from __future__ import annotations

import pytest

from app.schemas.dockerfile_schema import (
    DOCKERFILE_SECURITY_REPORT_JSON_SCHEMA,
    AwsEcrCredentials,
    DockerHubCredentials,
    DockerfileSecurityReport,
    GcpArtifactRegistryCredentials,
    ProjectStack,
    RegistryProvider,
    RegistryTarget,
)
from app.services.dockerfile_manager import DockerfileManagerError, _normalize_dockers_path
from app.services.dockerfile_registry import resolve_image_refs
from app.services.dockerfile_scaffold import detect_stack, scaffold_docker_compose, scaffold_dockerfile
from app.services.dockerfile_security import DockerfileSecurityService


def test_scaffold_docker_compose() -> None:
    compose = scaffold_docker_compose(app_name="my-service", listen_port=9090)
    assert "services:" in compose
    assert "my-service:" in compose
    assert 'ports:\n      - "9090:9090"' in compose
    assert "healthcheck:" in compose


def test_detect_stack_node() -> None:
    stack, markers = detect_stack(["package.json", "src/", "README.md"])
    assert stack == ProjectStack.NODE
    assert "package.json" in markers


def test_detect_stack_python() -> None:
    stack, markers = detect_stack(["pyproject.toml", "app/"])
    assert stack == ProjectStack.PYTHON
    assert "pyproject.toml" in markers


def test_detect_stack_unknown() -> None:
    stack, markers = detect_stack(["README.md", "docs/"])
    assert stack == ProjectStack.UNKNOWN
    assert markers == []


@pytest.mark.parametrize(
    "stack",
    [
        ProjectStack.NODE,
        ProjectStack.PYTHON,
        ProjectStack.GO,
        ProjectStack.JAVA,
        ProjectStack.RUST,
        ProjectStack.UNKNOWN,
    ],
)
def test_scaffold_is_multistage_and_nonroot(stack: ProjectStack) -> None:
    content = scaffold_dockerfile(stack, app_name="demo", listen_port=8080)
    assert content.count("FROM ") >= 2
    assert "USER 10001" in content or "USER nonroot" in content
    assert ":latest" not in content
    assert "syntax=docker/dockerfile" in content


def test_heuristic_flags_root_and_latest() -> None:
    bad = """\
FROM ubuntu:latest
RUN apt-get update
COPY . /app
CMD ["./app"]
"""
    service = DockerfileSecurityService()
    report = service._heuristic_report(bad, stack=ProjectStack.UNKNOWN)
    assert isinstance(report, DockerfileSecurityReport)
    rule_ids = {i.ruleId for i in report.securityIssues}
    assert "RUN_AS_ROOT" in rule_ids
    assert "UNPINNED_BASE_IMAGE" in rule_ids or "LATEST_TAG" in rule_ids
    assert report.hasMultiStage is False
    assert "FROM " in report.improvedDockerfile
    assert report.improvedDockerfile.count("FROM ") >= 2


def test_json_schema_required_fields() -> None:
    required = set(DOCKERFILE_SECURITY_REPORT_JSON_SCHEMA["required"])
    assert required == {
        "summary",
        "securityIssues",
        "hasMultiStage",
        "improvedDockerfile",
        "explanationOfChanges",
    }


def test_normalize_dockers_path() -> None:
    assert _normalize_dockers_path("Dockerfile") == "dockers/Dockerfile"
    assert _normalize_dockers_path("dockers/Dockerfile.prod") == "dockers/Dockerfile.prod"
    with pytest.raises(DockerfileManagerError):
        _normalize_dockers_path("../etc/passwd")


def test_resolve_docker_hub_refs() -> None:
    refs = resolve_image_refs(
        RegistryTarget(
            provider=RegistryProvider.DOCKER_HUB,
            docker_hub=DockerHubCredentials(
                username="alice",
                password_or_token="token",
                repository="alice/demo",
            ),
        ),
        ["latest", "v1.0.0"],
    )
    assert refs == ["alice/demo:latest", "alice/demo:v1.0.0"]


def test_resolve_ecr_refs() -> None:
    refs = resolve_image_refs(
        RegistryTarget(
            provider=RegistryProvider.AWS_ECR,
            aws_ecr=AwsEcrCredentials(
                access_key_id="AKIA",
                secret_access_key="secret",
                region="us-east-1",
                account_id="123456789012",
                repository="launchpad/demo",
            ),
        ),
        ["sha-abc"],
    )
    assert refs == ["123456789012.dkr.ecr.us-east-1.amazonaws.com/launchpad/demo:sha-abc"]


def test_resolve_gar_refs() -> None:
    refs = resolve_image_refs(
        RegistryTarget(
            provider=RegistryProvider.GCP_ARTIFACT_REGISTRY,
            gcp_artifact_registry=GcpArtifactRegistryCredentials(
                service_account_json='{"client_email":"x@y.iam.gserviceaccount.com"}',
                project_id="my-proj",
                location="us-central1",
                repository="apps",
                image_name="demo",
            ),
        ),
        ["v2"],
    )
    assert refs == ["us-central1-docker.pkg.dev/my-proj/apps/demo:v2"]

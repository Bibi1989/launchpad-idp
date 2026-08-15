from google.genai import types

from app.core.secrets import credentials_to_env, project_id_from_gcp_sa_json
from app.services.workspace_file_analyzer import (
    WorkspaceFileAnalyzerService,
    _REPORT_JSON_SCHEMA,
    _inject_dockerfile_nonroot_user,
    _inject_github_workflow_permissions,
    _inject_pgdata_env,
    _postgres_needs_pgdata_fix,
    _strip_duplicate_root_provider_blocks,
    detect_kind_from_path,
)


def _stale_postgres_manifest() -> str:
    from app.services.workload_dependencies import _postgres_deployment_yaml

    fixed = _postgres_deployment_yaml("lp-demo", "demo")
    return "\n".join(
        line
        for line in fixed.split("\n")
        if line.strip() not in ("- name: PGDATA", "value: /var/lib/postgresql/data/pgdata")
    )


def test_postgres_pgdata_detector() -> None:
    stale = _stale_postgres_manifest()
    assert "- name: PGDATA" not in stale  # env var removed (comment may still mention it)
    assert _postgres_needs_pgdata_fix(stale.lower()) is True
    # Already-fixed manifest is not flagged.
    from app.services.workload_dependencies import _postgres_deployment_yaml

    assert _postgres_needs_pgdata_fix(_postgres_deployment_yaml("lp-demo", "demo").lower()) is False


def test_analyzer_autofixes_postgres_pgdata() -> None:
    import yaml
    from app.services.workload_dependencies import _postgres_deployment_yaml

    stale = _stale_postgres_manifest()
    service = WorkspaceFileAnalyzerService.__new__(WorkspaceFileAnalyzerService)
    resp = service._heuristic_kubernetes(
        "infra/k8s/manifests/postgres-deployment.yaml", stale
    )
    assert any(i.ruleId == "POSTGRES_PGDATA_SUBDIR" and i.severity == "critical" for i in resp.issues)
    assert resp.improvedContent is not None
    doc = yaml.safe_load(resp.improvedContent)
    env = {
        e["name"]: e.get("value")
        for e in doc["spec"]["template"]["spec"]["containers"][0]["env"]
        if "value" in e
    }
    assert env["PGDATA"] == "/var/lib/postgresql/data/pgdata"
    # Auto-fix reproduces the corrected generator output exactly.
    assert resp.improvedContent.strip() == _postgres_deployment_yaml("lp-demo", "demo").strip()


def test_inject_pgdata_is_noop_without_env_block() -> None:
    assert _inject_pgdata_env("kind: Deployment\nmetadata:\n  name: postgres\n") is None


def test_report_json_schema_is_gemini_compatible() -> None:
    """Union null types caused silent heuristic fallback via Schema ValidationError."""
    schema = types.Schema.model_validate(_REPORT_JSON_SCHEMA)
    assert schema.properties is not None
    assert schema.properties["improvedContent"].nullable is True
    assert schema.properties["issues"].items.properties["ruleId"].nullable is True


def test_project_id_from_gcp_sa_json_sets_tf_var() -> None:
    sa = '{"type":"service_account","project_id":"real-gcp-proj","client_email":"a@b.iam.gserviceaccount.com"}'
    assert project_id_from_gcp_sa_json(sa) == "real-gcp-proj"
    env = credentials_to_env({"GCP_SA_KEY": sa})
    assert env["TF_VAR_project_id"] == "real-gcp-proj"
    assert env["GOOGLE_CLOUD_PROJECT"] == "real-gcp-proj"


def test_strip_duplicate_root_provider_blocks() -> None:
    content = """\
# root
terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 4.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "kubernetes" {
  config_path = "~/.kube/config"
}

data "google_client_config" "default" {}

module "vpc" {
  source = "./modules/vpc"
  project_id = var.project_id
}
"""
    fixed = _strip_duplicate_root_provider_blocks("infra/terraform/main.tf", content)
    assert fixed is not None
    assert 'provider "google"' not in fixed
    assert 'provider "kubernetes"' not in fixed
    assert "required_providers" not in fixed
    assert 'module "vpc"' in fixed
    # module mains must not be rewritten
    assert (
        _strip_duplicate_root_provider_blocks(
            "infra/terraform/modules/vpc/main.tf",
            'provider "google" {}\nresource "null_resource" "x" {}',
        )
        is None
    )


def test_heuristic_iac_strips_duplicate_providers() -> None:
    service = WorkspaceFileAnalyzerService()
    content = """\
provider "google" {
  project = var.project_id
}

module "vpc" {
  source = "./modules/vpc"
}
"""
    report = service._heuristic_iac(
        "infra/terraform/main.tf",
        content,
        error_context="Error: Duplicate provider configuration for google",
    )
    assert any(i.ruleId == "DUPLICATE_TF_PROVIDERS" for i in report.issues)
    assert report.improvedContent is not None
    assert 'provider "google"' not in report.improvedContent
    assert 'module "vpc"' in report.improvedContent


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


def test_heuristic_iac_strips_google_compute_network_labels() -> None:
    from app.services.workspace_file_analyzer import _strip_gcp_vpc_unsupported_labels

    content = """\
resource "google_compute_network" "vpc" {
  name                    = "lp-demo-vpc"
  project                 = var.project_id
  auto_create_subnetworks = false

  labels = {
    EnvironmentId  = var.environment_id
    Owner          = var.owner
  }
}

resource "google_compute_subnetwork" "subnet" {
  name   = "lp-demo-subnet"
  labels = {
    EnvironmentId = var.environment_id
  }
}
"""
    fixed = _strip_gcp_vpc_unsupported_labels(content)
    assert fixed is not None
    assert 'resource "google_compute_network" "vpc"' in fixed
    assert "auto_create_subnetworks = false" in fixed
    assert "labels" not in fixed

    service = WorkspaceFileAnalyzerService()
    report = service._heuristic_iac(
        "infra/terraform/modules/vpc/main.tf",
        content,
        error_context=(
            'Error: Unsupported argument\n'
            'on modules/vpc/main.tf line 15, in resource "google_compute_subnetwork" "subnet":\n'
            "  labels = {\n"
            'An argument named "labels" is not expected here.\n'
        ),
    )
    assert any(i.ruleId == "GCP_VPC_UNSUPPORTED_LABELS" for i in report.issues)
    assert report.improvedContent is not None
    assert "labels" not in report.improvedContent


def test_heuristic_docker_injects_nonroot_user() -> None:
    service = WorkspaceFileAnalyzerService.__new__(WorkspaceFileAnalyzerService)
    raw = "FROM python:3.12-alpine\nWORKDIR /app\nCMD [\"python\", \"main.py\"]\n"
    resp = service._heuristic_docker("dockers/Dockerfile.app", raw)
    assert any(i.ruleId == "RUN_AS_ROOT" for i in resp.issues)
    assert resp.improvedContent is not None
    assert "USER 10001" in resp.improvedContent
    assert resp.improvedContent.index("USER 10001") < resp.improvedContent.upper().index("CMD ")


def test_inject_dockerfile_nonroot_user_noop_when_present() -> None:
    content = "FROM alpine:3.20\nUSER 10001\nCMD [\"sleep\", \"infinity\"]\n"
    assert _inject_dockerfile_nonroot_user(content) is None


def test_heuristic_cicd_injects_permissions() -> None:
    service = WorkspaceFileAnalyzerService.__new__(WorkspaceFileAnalyzerService)
    raw = (
        "name: ci\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
    )
    resp = service._heuristic_cicd("ci/github/workflows/app.yml", raw)
    assert resp.improvedContent is not None
    assert "permissions:" in resp.improvedContent
    assert "contents: read" in resp.improvedContent
    assert resp.improvedContent.index("permissions:") < resp.improvedContent.index("jobs:")


def test_inject_github_workflow_permissions_noop_when_present() -> None:
    content = "name: ci\npermissions:\n  contents: read\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
    assert _inject_github_workflow_permissions(content) is None

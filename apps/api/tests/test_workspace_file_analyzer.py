from google.genai import types

from app.core.secrets import credentials_to_env, project_id_from_gcp_sa_json
from app.services.workspace_file_analyzer import (
    WorkspaceFileAnalyzerService,
    _REPORT_JSON_SCHEMA,
    _strip_duplicate_root_provider_blocks,
    detect_kind_from_path,
)


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

"""Tests for workload dependency scaffolding."""

from __future__ import annotations

import pytest

from app.schemas.cloud import (
    AwsCloudConfig,
    AwsResources,
    DependencyPlacement,
    GcpCloudConfig,
    GcpResources,
    ProvisioningWizardRequest,
    WorkloadDependenciesConfig,
    DataStoreDependency,
    WorkspaceArtifactsMode,
    KubernetesPackaging,
)
from app.services.workload_dependencies import (
    DataStoreKind,
    dependency_secret_string_data,
    in_cluster_manifest_files,
    validate_managed_dependencies,
)


def test_in_cluster_secret_urls() -> None:
    deps = WorkloadDependenciesConfig(
        postgres=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
        redis=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
    )
    data = dependency_secret_string_data(deps, name="demo-app")
    assert "postgresql://launchpad:changeme@postgres:5432/demo_app" in data["DATABASE_URL"]
    assert data["REDIS_URL"] == "redis://redis:6379/0"


def test_managed_postgres_requires_cloud_sql() -> None:
    cloud = GcpCloudConfig(
        resources=GcpResources(project_id="my-project", cloud_sql=False),
    )
    deps = WorkloadDependenciesConfig(
        postgres=DataStoreDependency(enabled=True, placement=DependencyPlacement.MANAGED),
    )
    with pytest.raises(ValueError, match="Cloud SQL"):
        validate_managed_dependencies(cloud, deps)


def test_managed_postgres_wizard_validation() -> None:
    cloud = GcpCloudConfig(
        resources=GcpResources(project_id="my-project", cloud_sql=True, gke=True),
    )
    request = ProvisioningWizardRequest(
        name="demo",
        cloud=cloud,
        artifact_mode=WorkspaceArtifactsMode.BOTH,
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
        dependencies=WorkloadDependenciesConfig(
            postgres=DataStoreDependency(enabled=True, placement=DependencyPlacement.MANAGED),
        ),
    )
    assert request.dependencies.postgres.placement == DependencyPlacement.MANAGED


def test_in_cluster_manifest_files_include_postgres() -> None:
    files = in_cluster_manifest_files(
        ns="lp-demo",
        name="demo",
        kinds=[DataStoreKind.POSTGRES],
    )
    assert "postgres-deployment.yaml" in files
    assert "postgres-service.yaml" in files
    assert "postgres:16-alpine" in files["postgres-deployment.yaml"]


def test_managed_redis_requires_elasticache() -> None:
    cloud = AwsCloudConfig(resources=AwsResources(elasticache=False))
    deps = WorkloadDependenciesConfig(
        redis=DataStoreDependency(enabled=True, placement=DependencyPlacement.MANAGED),
    )
    with pytest.raises(ValueError, match="ElastiCache"):
        validate_managed_dependencies(cloud, deps)

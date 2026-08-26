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


def test_managed_mysql_requires_matching_engine() -> None:
    cloud = GcpCloudConfig(
        resources=GcpResources(
            project_id="my-project",
            cloud_sql=True,
            cloud_sql_engine="postgres",
            gke=True,
        ),
    )
    deps = WorkloadDependenciesConfig(
        mysql=DataStoreDependency(enabled=True, placement=DependencyPlacement.MANAGED),
    )
    with pytest.raises(ValueError, match="mysql"):
        validate_managed_dependencies(cloud, deps)


def test_managed_postgres_ok_with_matching_engine() -> None:
    cloud = GcpCloudConfig(
        resources=GcpResources(project_id="my-project", cloud_sql=True, gke=True),
    )
    deps = WorkloadDependenciesConfig(
        postgres=DataStoreDependency(enabled=True, placement=DependencyPlacement.MANAGED),
    )
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


def test_external_secret_ref_is_not_inlined() -> None:
    from app.services.workload_dependencies import external_secret_names

    deps = WorkloadDependenciesConfig(
        postgres=DataStoreDependency(
            enabled=True,
            placement=DependencyPlacement.EXTERNAL,
            secret_ref="my-db-secret",
        ),
        redis=DataStoreDependency(
            enabled=True,
            placement=DependencyPlacement.EXTERNAL,
            connection_url="redis://cache:6379/0",
        ),
    )
    data = dependency_secret_string_data(deps, name="demo")
    # secret_ref datastore is injected via envFrom, so it is NOT inlined here.
    assert "DATABASE_URL" not in data
    # A plain external URL is still inlined.
    assert data["REDIS_URL"] == "redis://cache:6379/0"
    # The referenced secret name is surfaced for envFrom wiring.
    assert external_secret_names(deps) == ["my-db-secret"]


def test_external_connection_url_still_inlined_without_secret_ref() -> None:
    deps = WorkloadDependenciesConfig(
        postgres=DataStoreDependency(
            enabled=True,
            placement=DependencyPlacement.EXTERNAL,
            connection_url="postgresql://u:p@host:5432/db",
        ),
    )
    data = dependency_secret_string_data(deps, name="demo")
    assert data["DATABASE_URL"] == "postgresql://u:p@host:5432/db"

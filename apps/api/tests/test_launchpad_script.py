from app.schemas.cloud import (
    CloudProvider,
    GcpCloudConfig,
    GcpResources,
    IaCEngine,
    KubernetesImageSource,
    KubernetesWorkloadOptions,
    ProvisioningWizardRequest,
    SecretBackend,
    WorkspaceRuntimeMode,
)
from app.services.launchpad_script import write_launchpad_script


def _gcp_request(image_source: KubernetesImageSource = KubernetesImageSource.BUILD_REGISTRY) -> ProvisioningWizardRequest:
    return ProvisioningWizardRequest(
        name="demo-gke",
        iac_engine=IaCEngine.LAUNCHPAD,
        runtime_mode=WorkspaceRuntimeMode.KUBERNETES,
        cloud=GcpCloudConfig(
            provider=CloudProvider.GCP,
            resources=GcpResources(
                project_id="demo-proj",
                region="us-central1",
                gke=True,
                vpc=True,
                subnets=True,
                artifact_registry=True,
                secret_backend=SecretBackend.SECRET_MANAGER,
            ),
        ),
        kubernetes_options=KubernetesWorkloadOptions(image_source=image_source),
    )


def test_launchpad_script_provisions_gke_vpc_and_registry(tmp_path):
    files = write_launchpad_script(tmp_path, _gcp_request())
    assert "infra/launchProvision.sh" in files
    body = (tmp_path / "infra" / "launchProvision.sh").read_text()
    assert "container clusters create" in body
    assert "artifacts repositories create" in body
    assert "compute networks create" in body
    assert "secretmanager" in body
    assert "LaunchProvision" in body
    assert "configure" in body


def test_launchpad_script_skips_registry_for_external_images(tmp_path):
    files = write_launchpad_script(tmp_path, _gcp_request(KubernetesImageSource.EXTERNAL))
    body = (tmp_path / files[0]).read_text()
    assert "skipping Artifact Registry" in body
    assert "artifacts repositories create" not in body

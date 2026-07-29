from pathlib import Path

from app.schemas.cloud import (
    CloudCredentials,
    IaCEngine,
    KubernetesPackaging,
    LocalCloudConfig,
    LocalResources,
    ProvisioningWizardRequest,
)
from app.services.iac_generator import IaCGenerator
from app.services.sandbox_runner import build_provision_bootstrap


def test_local_kind_workspace_skips_terraform(tmp_path: Path) -> None:
    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="kind-demo",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        kubernetes_packaging=KubernetesPackaging.NONE,
    )
    assert request.kubernetes_packaging == KubernetesPackaging.RAW_MANIFESTS

    bundle = gen.generate(request)
    root = Path(bundle.root_dir)
    assert bundle.provider.value == "local"
    assert (root / "README.md").is_file()
    assert (root / "infra" / "kind" / "README.md").is_file()
    assert not (root / "infra" / "terraform").exists()
    assert any(p.startswith("infra/k8s/") for p in bundle.files)

    bootstrap = build_provision_bootstrap(root, engine="terraform")
    assert bootstrap is not None
    assert "kind / local Kubernetes context" in bootstrap
    assert "kubectl apply" in bootstrap
    assert "terraform init" not in bootstrap

from pathlib import Path

from app.schemas.cloud import (
    CloudCredentials,
    ContainerScaffoldConfig,
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


def test_local_kind_writes_multi_framework_launch_manifests(tmp_path: Path) -> None:
    # Multi-framework (fullstack) core stacks scaffold a real app per stack and
    # emit launch-* manifests with real images — never the generic nginx fallback.
    import yaml

    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="multi-demo",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
        container_scaffold=ContainerScaffoldConfig(
            enabled=True,
            generate_dockerfile=True,
            generate_docker_compose=True,
            stack="nuxtjs",
            frameworks=["nuxtjs", "fastapi", "nestjs"],
            app_name="shop",
            listen_port=3000,
        ),
    )
    bundle = gen.generate(request)
    root = Path(bundle.root_dir)
    mdir = root / "infra" / "k8s" / "manifests"

    # No generic nginx Deployment.
    assert not (mdir / "deployment.yaml").exists()
    # Real per-stack app source + launch-* manifests with real images.
    for stack in ("nuxtjs", "fastapi", "nestjs"):
        assert (root / "apps" / f"shop-{stack}").is_dir()
        dep = yaml.safe_load((mdir / f"launch-{stack}-deployment.yaml").read_text())
        img = dep["spec"]["template"]["spec"]["containers"][0]["image"]
        assert img == f"shop-{stack}:latest"
        assert "nginx" not in img
    assert "docker-compose.yml" in bundle.files

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from app.core.secrets import encrypt_secret, decrypt_secret, mask_terminal_output
from app.schemas.cloud import (
    CloudProvider,
    GcpCloudConfig,
    GcpResources,
    IaCEngine,
    ProvisioningWizardRequest,
    SecretBackend,
    WorkspaceArtifactsMode,
)
from app.services.iac_generator import IaCGenerator


def test_mask_terminal_output_redacts_tokens_and_keys() -> None:
    raw = (
        "export AWS_SECRET_ACCESS_KEY=AKIAsupersecret\n"
        "Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz012345\n"
        "password=hunter2\n"
    )
    masked = mask_terminal_output(raw)
    assert "supersecret" not in masked
    assert "hunter2" not in masked
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" not in masked
    assert "[REDACTED]" in masked


def test_iac_generator_allocates_named_directory_with_collision_suffix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generator = IaCGenerator(workspace_root=root)
        request = ProvisioningWizardRequest(
            name="demo-gcp",
            iac_engine=IaCEngine.TERRAFORM,
            cloud=GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(project_id="my-project"),
            ),
        )
        first = generator.generate(request)
        second = generator.generate(request)
        assert Path(first.root_dir).name == "demo-gcp"
        assert Path(second.root_dir).name.startswith("demo-gcp-")
        assert Path(first.root_dir).parent == root
        assert Path(second.root_dir).parent == root


def test_encrypt_decrypt_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", "unit-test-encryption-key-material")
    from app.core.config import get_settings
    from app.core import secrets as secrets_mod

    get_settings.cache_clear()
    secrets_mod._fernet.cache_clear()

    token = "ghp_testtoken_should_be_encrypted"
    cipher = encrypt_secret(token)
    assert token not in cipher
    assert decrypt_secret(cipher) == token

    get_settings.cache_clear()
    secrets_mod._fernet.cache_clear()


def test_sanitize_dns1123_and_gke_cluster_naming() -> None:
    from app.services.terraform_bundle import sanitize_dns1123_name, write_terraform_bundle

    assert sanitize_dns1123_name("My_Env Name!!", max_len=40, prefix="gke") == "gke-my-env-name"
    long_name = "preview-pr-12345-" + ("x" * 80)
    gke = sanitize_dns1123_name(long_name, max_len=40, prefix="gke")
    assert len(gke) <= 40
    assert gke.startswith("gke-")
    assert not gke.endswith("-")
    assert gke == gke.lower()
    assert all(ch.isalnum() or ch == "-" for ch in gke)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_terraform_bundle(
            root,
            long_name[:64],
            GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(
                    project_id="my-project",
                    gke=True,
                    vpc=True,
                    subnets=True,
                    cloud_storage=True,
                    cloud_sql=True,
                ),
            ),
        )
        cluster = (root / "infra/terraform/modules/cluster/main.tf").read_text(
            encoding="utf-8"
        )
        assert "name                     = local.gke_cluster_name" in cluster
        assert "deletion_protection      = false" in cluster
        assert "depends_on = [google_container_cluster.gke]" in cluster
        assert "environment_id = var.environment_id" in cluster
        assert "EnvironmentId" not in cluster
        vpc = (root / "infra/terraform/modules/vpc/main.tf").read_text(encoding="utf-8")
        assert "ip_cidr_range = local.gcp_subnet_cidr" in vpc
        main = (root / "infra/terraform/main.tf").read_text(encoding="utf-8")
        assert "force_destroy               = true" in main
        assert "deletion_protection = false" in main
        assert "local.gcs_bucket_name" in main


def test_iac_generator_writes_gcp_terraform_bundle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        generator = IaCGenerator(workspace_root=Path(tmp))
        request = ProvisioningWizardRequest(
            name="demo-gcp",
            iac_engine=IaCEngine.TERRAFORM,
            cloud=GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(
                    project_id="my-project",
                    gke=True,
                    artifact_registry=True,
                    cloud_run=True,
                    secret_backend=SecretBackend.SECRET_MANAGER,
                ),
            ),
        )
        bundle = generator.generate(request)
        assert bundle.provider == CloudProvider.GCP
        assert Path(bundle.root_dir).name == "demo-gcp"
        expected = {
            "infra/terraform/main.tf",
            "infra/terraform/variables.tf",
            "infra/terraform/outputs.tf",
            "infra/terraform/providers.tf",
            "infra/terraform/terraform.tfvars",
            "infra/terraform/modules/vpc/main.tf",
            "infra/terraform/modules/vpc/variables.tf",
            "infra/terraform/modules/vpc/outputs.tf",
            "infra/terraform/modules/cluster/main.tf",
            "infra/terraform/modules/cluster/variables.tf",
            "infra/terraform/modules/cluster/outputs.tf",
            "infra/terraform/modules/secrets/main.tf",
            "infra/terraform/modules/secrets/variables.tf",
            "infra/terraform/modules/secrets/outputs.tf",
        }
        assert expected.issubset(set(bundle.files))
        assert "infra/terraform/apis.tf" in set(bundle.files)

        workspace = Path(bundle.root_dir)
        apis_tf = (workspace / "infra/terraform/apis.tf").read_text(encoding="utf-8")
        assert "google_project_service" in apis_tf
        assert "compute.googleapis.com" in apis_tf
        assert "serviceusage.googleapis.com" in apis_tf
        assert "cloudresourcemanager.googleapis.com" in apis_tf
        assert "container.googleapis.com" in apis_tf
        assert "iam.googleapis.com" in apis_tf
        assert "disable_on_destroy = false" in apis_tf

        root_main = (workspace / "infra/terraform/main.tf").read_text(encoding="utf-8")
        assert 'module "vpc"' in root_main
        assert "depends_on = [google_project_service.apis]" in root_main
        assert 'source = "./modules/vpc"' in root_main
        assert 'module "cluster"' in root_main
        assert 'source = "./modules/cluster"' in root_main
        assert 'module "secrets"' in root_main
        assert 'source = "./modules/secrets"' in root_main
        assert "google_artifact_registry_repository" in root_main
        assert "depends_on = [google_project_service.apis, module.vpc]" in root_main

        vpc_main = (
            workspace / "infra/terraform/modules/vpc/main.tf"
        ).read_text(encoding="utf-8")
        assert "google_compute_network" in vpc_main
        assert "google_compute_subnetwork" in vpc_main
        # VPC networks/subnetworks do not support labels in the GCP provider.
        assert "labels" not in vpc_main
        assert "local.name_55" in vpc_main
        assert "local.gcp_subnet_cidr" in vpc_main

        cluster_main = (
            workspace / "infra/terraform/modules/cluster/main.tf"
        ).read_text(encoding="utf-8")
        assert "google_container_cluster" in cluster_main
        assert "local.gke_cluster_name" in cluster_main
        assert "deletion_protection      = false" in cluster_main
        assert "local.gke_node_pool_name" in cluster_main
        assert "google_cloud_run_v2_service" in cluster_main
        assert "gke-" in cluster_main  # naming locals prefix gke-

        secrets_main = (
            workspace / "infra/terraform/modules/secrets/main.tf"
        ).read_text(encoding="utf-8")
        assert "google_secret_manager_secret" in secrets_main

        outputs = (
            workspace / "infra/terraform/outputs.tf"
        ).read_text(encoding="utf-8")
        assert "module.vpc.vpc_id" in outputs
        assert "module.cluster.gke_cluster_endpoint" in outputs


def test_iac_generator_wires_kubernetes_provider_to_gke_for_native_secrets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        generator = IaCGenerator(workspace_root=Path(tmp))
        request = ProvisioningWizardRequest(
            name="demo-gke-secrets",
            iac_engine=IaCEngine.TERRAFORM,
            cloud=GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(
                    project_id="my-project",
                    gke=True,
                    secret_backend=SecretBackend.NATIVE_K8S,
                ),
            ),
        )
        bundle = generator.generate(request)
        workspace = Path(bundle.root_dir)
        providers = (workspace / "infra/terraform/providers.tf").read_text(encoding="utf-8")
        assert "module.cluster.gke_cluster_endpoint" in providers
        assert "module.cluster.gke_cluster_ca_certificate" in providers
        assert "~/.kube/config" not in providers
        apis = (workspace / "infra/terraform/apis.tf").read_text(encoding="utf-8")
        assert "container.googleapis.com" in apis
        main = (workspace / "infra/terraform/main.tf").read_text(encoding="utf-8")
        assert "module.cluster" in main
        assert "depends_on = [google_project_service.apis, module.cluster]" in main
        cluster_out = (
            workspace / "infra/terraform/modules/cluster/outputs.tf"
        ).read_text(encoding="utf-8")
        assert "gke_cluster_ca_certificate" in cluster_out


def test_iac_generator_writes_pulumi_aws_bundle() -> None:
    from app.schemas.cloud import AwsCloudConfig, AwsResources

    with tempfile.TemporaryDirectory() as tmp:
        generator = IaCGenerator(workspace_root=Path(tmp))
        request = ProvisioningWizardRequest(
            name="demo-aws",
            iac_engine=IaCEngine.PULUMI,
            cloud=AwsCloudConfig(
                provider=CloudProvider.AWS,
                resources=AwsResources(s3=True, vpc=True),
            ),
        )
        bundle = generator.generate(request)
        assert "index.ts" in bundle.files
        index = (Path(bundle.root_dir) / "index.ts").read_text(encoding="utf-8")
        assert "@pulumi/aws" in index or "aws.ec2.Vpc" in index


def test_iac_generator_writes_raw_k8s_manifests() -> None:
    from app.schemas.cloud import KubernetesPackaging, KubernetesWorkloadOptions

    with tempfile.TemporaryDirectory() as tmp:
        generator = IaCGenerator(workspace_root=Path(tmp))
        request = ProvisioningWizardRequest(
            name="demo-k8s",
            iac_engine=IaCEngine.TERRAFORM,
            artifact_mode=WorkspaceArtifactsMode.BOTH,
            kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
            kubernetes_options=KubernetesWorkloadOptions(
                config_map=True,
                secret=True,
                service_account=True,
                ingress=True,
                hpa=True,
                pdb=True,
                network_policy=True,
                resource_quota=True,
                limit_range=True,
            ),
            cloud=GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(project_id="my-project", gke=True),
            ),
        )
        bundle = generator.generate(request)
        expected = {
            "infra/k8s/manifests/namespace.yaml",
            "infra/k8s/manifests/deployment.yaml",
            "infra/k8s/manifests/service.yaml",
            "infra/k8s/manifests/serviceaccount.yaml",
            "infra/k8s/manifests/configmap.yaml",
            "infra/k8s/manifests/secret.yaml",
            "infra/k8s/manifests/ingress.yaml",
            "infra/k8s/manifests/hpa.yaml",
            "infra/k8s/manifests/pdb.yaml",
            "infra/k8s/manifests/networkpolicy.yaml",
            "infra/k8s/manifests/resourcequota.yaml",
            "infra/k8s/manifests/limitrange.yaml",
        }
        assert expected.issubset(set(bundle.files))
        assert "infra/k8s/manifests/vpa.yaml" not in bundle.files
        deployment = (
            Path(bundle.root_dir) / "infra/k8s/manifests/deployment.yaml"
        ).read_text(encoding="utf-8")
        assert "resources:" in deployment
        assert "limits:" in deployment
        assert "readinessProbe:" in deployment
        assert "configMapRef:" in deployment
        assert "secretRef:" in deployment
        dep_doc = yaml.safe_load(deployment)
        pod_labels = dep_doc["spec"]["template"]["metadata"]["labels"]
        assert pod_labels["app"] == "app"
        assert pod_labels["app.kubernetes.io/instance"] == "demo-k8s"
        assert pod_labels["launchpad.io/managed-by"] == "launchpad-idp"
        assert "app" not in dep_doc["spec"]["template"]
        network = (
            Path(bundle.root_dir) / "infra/k8s/manifests/networkpolicy.yaml"
        ).read_text(encoding="utf-8")
        assert "policyTypes:" in network
        assert "Ingress" in network
        assert "Egress" in network


def test_iac_generator_writes_selected_workload_options() -> None:
    from app.schemas.cloud import (
        IngressClassName,
        KubernetesPackaging,
        KubernetesWorkloadOptions,
    )

    with tempfile.TemporaryDirectory() as tmp:
        generator = IaCGenerator(workspace_root=Path(tmp))
        request = ProvisioningWizardRequest(
            name="demo-opts",
            iac_engine=IaCEngine.TERRAFORM,
            artifact_mode=WorkspaceArtifactsMode.BOTH,
            kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
            kubernetes_options=KubernetesWorkloadOptions(
                config_map=False,
                secret=False,
                hpa=False,
                vpa=True,
                ingress=True,
                ingress_class=IngressClassName.TRAEFIK,
                install_ingress_nginx=False,
                network_policy=False,
                resource_quota=False,
                service_account=False,
                pdb=False,
                limit_range=False,
            ),
            cloud=GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(project_id="my-project", gke=True),
            ),
        )
        bundle = generator.generate(request)
        files = set(bundle.files)
        assert "infra/k8s/manifests/deployment.yaml" in files
        assert "infra/k8s/manifests/service.yaml" in files
        assert "infra/k8s/manifests/vpa.yaml" in files
        assert "infra/k8s/manifests/namespace.yaml" in files
        # Governance files follow option flags — not forced on.
        assert "infra/k8s/manifests/networkpolicy.yaml" not in files
        assert "infra/k8s/manifests/resourcequota.yaml" not in files
        assert "infra/k8s/manifests/limitrange.yaml" not in files
        assert "infra/k8s/manifests/ingress.yaml" in files
        assert "infra/k8s/manifests/configmap.yaml" not in files
        assert "infra/k8s/manifests/hpa.yaml" not in files
        deployment = (
            Path(bundle.root_dir) / "infra/k8s/manifests/deployment.yaml"
        ).read_text(encoding="utf-8")
        assert "memory: 128Mi" in deployment
        assert "memory: 256Mi" in deployment
        assert "livenessProbe:" in deployment
        assert "readinessProbe:" in deployment
        ingress = (Path(bundle.root_dir) / "infra/k8s/manifests/ingress.yaml").read_text(
            encoding="utf-8"
        )
        assert "ingressClassName: traefik" in ingress


def test_iac_generator_writes_ingress_nginx_addon() -> None:
    from app.schemas.cloud import KubernetesPackaging, KubernetesWorkloadOptions

    with tempfile.TemporaryDirectory() as tmp:
        generator = IaCGenerator(workspace_root=Path(tmp))
        request = ProvisioningWizardRequest(
            name="demo-nginx",
            iac_engine=IaCEngine.TERRAFORM,
            artifact_mode=WorkspaceArtifactsMode.BOTH,
            kubernetes_packaging=KubernetesPackaging.HELM,
            kubernetes_options=KubernetesWorkloadOptions(ingress=True, install_ingress_nginx=True),
            cloud=GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(project_id="my-project", gke=True),
            ),
        )
        bundle = generator.generate(request)
        assert "infra/k8s/addons/ingress-nginx-values.yaml" in bundle.files
        from app.services.sandbox_runner import build_provision_bootstrap

        cmd = build_provision_bootstrap(Path(bundle.root_dir), engine="terraform")
        assert cmd is not None
        assert "ingress-nginx/ingress-nginx" in cmd
        assert "infra/k8s/addons/ingress-nginx-values.yaml" in cmd


def test_iac_generator_writes_helm_chart() -> None:
    from app.schemas.cloud import AwsCloudConfig, AwsResources, KubernetesPackaging, KubernetesWorkloadOptions

    with tempfile.TemporaryDirectory() as tmp:
        generator = IaCGenerator(workspace_root=Path(tmp))
        request = ProvisioningWizardRequest(
            name="demo-helm",
            iac_engine=IaCEngine.TERRAFORM,
            artifact_mode=WorkspaceArtifactsMode.BOTH,
            kubernetes_packaging=KubernetesPackaging.HELM,
            kubernetes_options=KubernetesWorkloadOptions(ingress=True),
            cloud=AwsCloudConfig(
                provider=CloudProvider.AWS,
                resources=AwsResources(eks=True, vpc=True),
            ),
        )
        bundle = generator.generate(request)
        expected = {
            "infra/helm/app-chart/Chart.yaml",
            "infra/helm/app-chart/values.yaml",
            "infra/helm/app-chart/templates/_helpers.tpl",
            "infra/helm/app-chart/templates/deployment.yaml",
            "infra/helm/app-chart/templates/service.yaml",
            "infra/helm/app-chart/templates/ingress.yaml",
        }
        assert expected.issubset(set(bundle.files))
        chart = (
            Path(bundle.root_dir) / "infra/helm/app-chart/Chart.yaml"
        ).read_text(encoding="utf-8")
        assert "apiVersion: v2" in chart
        assert "name: app-chart" in chart


def test_build_provision_bootstrap_terraform_and_kubectl() -> None:
    from app.schemas.cloud import KubernetesPackaging
    from app.services.sandbox_runner import build_provision_bootstrap

    with tempfile.TemporaryDirectory() as tmp:
        generator = IaCGenerator(workspace_root=Path(tmp))
        request = ProvisioningWizardRequest(
            name="demo-pipe",
            iac_engine=IaCEngine.TERRAFORM,
            artifact_mode=WorkspaceArtifactsMode.BOTH,
            kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
            cloud=GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(project_id="my-project", gke=True),
            ),
        )
        bundle = generator.generate(request)
        cmd = build_provision_bootstrap(Path(bundle.root_dir), engine="terraform")
        assert cmd is not None
        assert "cd infra/terraform" in cmd
        assert "terraform init -input=false" in cmd
        assert "terraform apply -auto-approve -input=false" in cmd
        assert "kubectl apply -f infra/k8s/manifests/" in cmd
        assert "helm upgrade" not in cmd


def test_build_provision_bootstrap_prefers_helm_over_kubectl() -> None:
    from app.schemas.cloud import KubernetesPackaging
    from app.services.sandbox_runner import build_provision_bootstrap

    with tempfile.TemporaryDirectory() as tmp:
        generator = IaCGenerator(workspace_root=Path(tmp))
        request = ProvisioningWizardRequest(
            name="demo-helm-pipe",
            iac_engine=IaCEngine.TERRAFORM,
            artifact_mode=WorkspaceArtifactsMode.BOTH,
            kubernetes_packaging=KubernetesPackaging.HELM,
            cloud=GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(project_id="my-project", gke=True),
            ),
        )
        bundle = generator.generate(request)
        cmd = build_provision_bootstrap(Path(bundle.root_dir), engine="terraform")
        assert cmd is not None
        assert "helm upgrade --install app-chart infra/helm/app-chart/" in cmd
        assert "kubectl apply" not in cmd


def test_kubernetes_packaging_requires_runtime() -> None:
    from app.schemas.cloud import KubernetesPackaging
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProvisioningWizardRequest(
            name="no-cluster",
            iac_engine=IaCEngine.TERRAFORM,
            artifact_mode=WorkspaceArtifactsMode.MANIFEST_ONLY,
            kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
            cloud=GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(project_id="my-project", gke=False, cloud_run=False),
            ),
        )


def test_manifest_only_workspace_writes_only_manifests() -> None:
    from app.schemas.cloud import KubernetesPackaging

    with tempfile.TemporaryDirectory() as tmp:
        generator = IaCGenerator(workspace_root=Path(tmp))
        request = ProvisioningWizardRequest(
            name="demo-manifest-only",
            iac_engine=IaCEngine.TERRAFORM,
            artifact_mode=WorkspaceArtifactsMode.MANIFEST_ONLY,
            kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
            cloud=GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(project_id="my-project", gke=True),
            ),
        )
        bundle = generator.generate(request)
        assert any(path.startswith("infra/k8s/manifests/") for path in bundle.files)
        assert not any(path.startswith("infra/terraform/") for path in bundle.files)

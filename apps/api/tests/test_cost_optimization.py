"""Cost optimization suite — schema sync and manifest injection."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from app.schemas.cloud import (
    CloudProvider,
    CostHpaConfig,
    CostOptimizationConfig,
    CostResourceConfig,
    CostVpaConfig,
    GcpCloudConfig,
    GcpResources,
    IaCEngine,
    IdleShutdownConfig,
    KubernetesPackaging,
    KubernetesWorkloadOptions,
    ProvisioningWizardRequest,
    ResourceSizingPreset,
    SpotSchedulingConfig,
    SpotWorkloadPlacement,
    WorkspaceArtifactsMode,
)
from app.services.cost_optimization import resolve_resources
from app.services.iac_generator import IaCGenerator
from app.services.k8s_bundle import write_kubernetes_layout


def test_cost_optimization_enables_hpa_vpa_flags() -> None:
    request = ProvisioningWizardRequest(
        name="cost-demo",
        iac_engine=IaCEngine.TERRAFORM,
        artifact_mode=WorkspaceArtifactsMode.MANIFEST_ONLY,
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
        kubernetes_options=KubernetesWorkloadOptions(hpa=False, vpa=False),
        cost_optimization=CostOptimizationConfig(
            hpa=CostHpaConfig(enabled=True, min_replicas=3, max_replicas=12, target_cpu_utilization=65),
            vpa=CostVpaConfig(enabled=True),
        ),
        cloud=GcpCloudConfig(
            provider=CloudProvider.GCP,
            resources=GcpResources(project_id="my-project", gke=True),
        ),
    )
    assert request.kubernetes_options.hpa is True
    assert request.kubernetes_options.vpa is True


def test_write_kubernetes_layout_injects_spot_hpa_vpa_idle() -> None:
    cost = CostOptimizationConfig(
        spot_scheduling=SpotSchedulingConfig(
            enabled=True,
            placement=SpotWorkloadPlacement.PRODUCTION_ONDEMAND_FALLBACK,
            allocation_percent=70,
            provisioner="karpenter",
        ),
        hpa=CostHpaConfig(enabled=True, min_replicas=2, max_replicas=8, target_cpu_utilization=60),
        vpa=CostVpaConfig(enabled=True),
        resources=CostResourceConfig(preset=ResourceSizingPreset.BALANCED),
        idle_shutdown=IdleShutdownConfig(enabled=True),
    )
    options = KubernetesWorkloadOptions(hpa=True, vpa=True)

    with tempfile.TemporaryDirectory() as tmp:
        files = write_kubernetes_layout(
            Path(tmp),
            name="cost-ws",
            packaging=KubernetesPackaging.RAW_MANIFESTS,
            options=options,
            cost_optimization=cost,
        )
        assert "infra/k8s/manifests/deployment.yaml" in files
        assert "infra/k8s/manifests/hpa.yaml" in files
        assert "infra/k8s/manifests/vpa.yaml" in files
        assert "infra/k8s/manifests/idle-shutdown.yaml" in files
        assert "infra/k8s/addons/karpenter-nodepool.yaml" in files

        deployment = (Path(tmp) / "infra/k8s/manifests/deployment.yaml").read_text(encoding="utf-8")
        assert "launchpad-cost-optimization:" in deployment
        assert "capacity-type" in deployment
        assert "tolerations:" in deployment
        assert "key: \"spot\"" in deployment
        cpu_req, mem_req, _, _ = resolve_resources(cost.resources)
        assert cpu_req in deployment
        assert mem_req in deployment

        hpa = yaml.safe_load((Path(tmp) / "infra/k8s/manifests/hpa.yaml").read_text(encoding="utf-8"))
        assert hpa["spec"]["minReplicas"] == 2
        assert hpa["spec"]["maxReplicas"] == 8
        assert hpa["spec"]["metrics"][0]["resource"]["target"]["averageUtilization"] == 60

        vpa_docs = list(
            yaml.safe_load_all((Path(tmp) / "infra/k8s/manifests/vpa.yaml").read_text(encoding="utf-8"))
        )
        vpa = next(doc for doc in vpa_docs if doc and doc.get("kind") == "VerticalPodAutoscaler")
        assert vpa["spec"]["updatePolicy"]["updateMode"] == "Off"

        idle = (Path(tmp) / "infra/k8s/manifests/idle-shutdown.yaml").read_text(encoding="utf-8")
        assert "scale deployment/app --replicas=0" in idle
        assert "0 19 * * 1-5" in idle


def test_iac_generator_persists_cost_optimization_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        generator = IaCGenerator(workspace_root=Path(tmp))
        request = ProvisioningWizardRequest(
            name="snap-cost",
            iac_engine=IaCEngine.TERRAFORM,
            artifact_mode=WorkspaceArtifactsMode.MANIFEST_ONLY,
            kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
            cost_optimization=CostOptimizationConfig(
                spot_scheduling=SpotSchedulingConfig(enabled=True, allocation_percent=50),
            ),
            cloud=GcpCloudConfig(
                provider=CloudProvider.GCP,
                resources=GcpResources(project_id="my-project", gke=True),
            ),
        )
        bundle = generator.generate(request)
        snapshot = generator.read_wizard_snapshot(Path(bundle.root_dir))
        assert snapshot is not None
        assert "cost_optimization" in snapshot
        cost = snapshot["cost_optimization"]
        assert isinstance(cost, dict)
        spot = cost["spot_scheduling"]
        assert spot["enabled"] is True
        assert spot["allocation_percent"] == 50

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.models.domain import Organization
from app.schemas.cloud import (
    CloudCredentials,
    CloudProvider,
    IaCEngine,
    KubernetesPackaging,
    LocalCloudConfig,
    LocalResources,
    ProvisioningWizardRequest,
)
from app.services.kind_cluster import ensure_kind_cluster, probe_kind_cluster
from app.services.provisioning import ProvisioningService


@pytest.mark.asyncio
async def test_ensure_kind_cluster_runs_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    up = script_dir / "kind-up.sh"
    up.write_text("#!/usr/bin/env bash\necho kind-up-ok\n", encoding="utf-8")
    up.chmod(0o755)

    monkeypatch.setenv("KIND_AUTO_MANAGE", "true")
    with patch("app.services.kind_cluster.get_settings") as settings_mock:
        settings = settings_mock.return_value
        settings.kind_auto_manage = True
        settings.kind_cluster_name = "launchpad"
        settings.kind_scripts_dir = str(script_dir)
        settings.local_k8s_engine = "kind"
        settings.local_cluster_tool = "kind"
        settings.preview_node_port_min = 30080
        settings.preview_node_port_max = 30084
        settings.default_workload_image = "nginx:1.27-alpine"

        with patch("app.services.kind_cluster.local_cluster_available", return_value=True):
            result = await ensure_kind_cluster()
    assert result["status"] == "ready"
    assert result["engine"] == "kind"
    assert "kind-up-ok" in result["output"]


@pytest.mark.asyncio
async def test_generate_bundle_starts_kind_for_local(tmp_path: Path) -> None:
    request = ProvisioningWizardRequest(
        name="kind-auto",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources(cluster_name="launchpad")),
        credentials=CloudCredentials(),
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
    )

    session = AsyncMock()
    session.add = lambda *_a, **_k: None
    session.commit = AsyncMock()

    with (
        patch("app.services.provisioning.ensure_kind_cluster", new_callable=AsyncMock) as up,
        patch("app.services.provisioning.IaCGenerator") as gen_cls,
        patch("app.services.provisioning.encrypt_secret", return_value="enc"),
        patch("app.services.user_credentials.UserCloudCredentialsService.get_credentials", new_callable=AsyncMock, return_value=CloudCredentials()),
        patch("app.services.orgs.OrganizationService.ensure_personal_org", new_callable=AsyncMock, return_value=Organization(id=UUID("22222222-2222-2222-2222-222222222222"), name="Personal", slug="personal")),
    ):
        gen = gen_cls.return_value
        from app.schemas.cloud import IaCBundleSummary

        gen.generate.return_value = IaCBundleSummary(
            workspace_id="11111111-1111-1111-1111-111111111111",
            engine=IaCEngine.TERRAFORM,
            provider=CloudProvider.LOCAL,
            root_dir=str(tmp_path / "kind-auto"),
            files=["README.md"],
            name="kind-auto",
        )
        service = ProvisioningService(session)
        service._iac = gen
        summary = await service.generate_bundle(request, owner=AsyncMock(id="owner"))

    up.assert_awaited_once()
    assert summary.provider == CloudProvider.LOCAL


@pytest.mark.asyncio
async def test_generate_bundle_surfaces_kind_failure() -> None:
    request = ProvisioningWizardRequest(
        name="kind-fail",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(),
        credentials=CloudCredentials(),
    )
    session = AsyncMock()
    service = ProvisioningService(session)
    with patch(
        "app.services.provisioning.ensure_kind_cluster",
        new_callable=AsyncMock,
        side_effect=RuntimeError("kind missing"),
    ):
        with pytest.raises(HTTPException) as exc:
            await service.generate_bundle(request, owner=AsyncMock(id="owner"))
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "kind_cluster_unavailable"


@pytest.mark.asyncio
async def test_probe_kind_cluster_absent_without_auto_manage() -> None:
    kind_proc = MagicMock()
    kind_proc.returncode = 0
    kind_proc.communicate = AsyncMock(return_value=(b"", b""))

    def which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    with (
        patch("app.services.kind_cluster.get_settings") as settings_mock,
        patch("app.services.kind_cluster.shutil.which", side_effect=which),
        patch(
            "app.services.kind_cluster.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=kind_proc,
        ),
    ):
        settings = settings_mock.return_value
        settings.kind_auto_manage = False
        settings.kind_cluster_name = "launchpad"
        payload = await probe_kind_cluster()

    assert payload["status"] == "absent"
    assert payload["can_launch"] is False
    assert payload["auto_manage"] is False

"""Retry provision: preserve TF state and adopt existing GCP resources on 409."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.services.iac_apply import (
    _parse_already_exists_from_apply_error,
    _read_tf_variable_defaults,
    _resolve_tf_context,
    run_workspace_iac_apply,
)
from app.services.iac_state import (
    is_already_exists_apply_error,
    restore_iac_runtime_state,
    stash_iac_runtime_state,
    terraform_name_prefix,
)


def _settings() -> Settings:
    return Settings.model_construct(
        scaffold_cloud_deploy_enabled=True,
        iac_apply_timeout_seconds=60,
        iac_destroy_timeout_seconds=60,
    )


def test_terraform_name_prefix_mirrors_locals() -> None:
    assert terraform_name_prefix("My Env!") == "lp-my-env"
    assert terraform_name_prefix("demo") == "lp-demo"
    assert len(terraform_name_prefix("a" * 80)) <= 55
    assert len(terraform_name_prefix("a" * 80, max_len=63)) <= 63


def test_already_exists_detects_gcp_409() -> None:
    err = (
        "Error 409: The resource "
        "'projects/p/zones/us-central1-a/instances/lp-demo-vm' already exists"
    )
    assert is_already_exists_apply_error(err) is True
    assert is_already_exists_apply_error("plan: 1 to add") is False


def test_read_region_from_variables_defaults(tmp_path: Path) -> None:
    tf = tmp_path / "infra" / "terraform"
    tf.mkdir(parents=True)
    (tf / "variables.tf").write_text(
        'variable "project_id" {\n  type = string\n  default = "launchpad-504012"\n}\n'
        'variable "region" {\n  type = string\n  default = "europe-west3"\n}\n',
        encoding="utf-8",
    )
    (tf / "terraform.tfvars").write_text(
        'environment_id = "new-instance-gcp"\n',
        encoding="utf-8",
    )
    defaults = _read_tf_variable_defaults(tf)
    assert defaults["region"] == "europe-west3"
    assert defaults["project_id"] == "launchpad-504012"
    ctx = _resolve_tf_context(tf, {}, None)
    assert ctx["region"] == "europe-west3"
    assert ctx["project_id"] == "launchpad-504012"
    assert ctx["environment_id"] == "new-instance-gcp"


def test_parse_already_exists_prefers_error_resource_ids() -> None:
    err = """
Error: Error creating Repository: googleapi: Error 409: the repository already exists

  with google_artifact_registry_repository.ar,
  on main.tf line 85

Error: Error creating instance: googleapi: Error 409: The resource 'projects/launchpad-504012/zones/europe-west3-a/instances/lp-new-instance-gcp-vm' already exists, alreadyExists

  with module.cluster.google_compute_instance.app,
  on modules/cluster/main.tf line 56
"""
    pairs = _parse_already_exists_from_apply_error(err)
    by_addr = {a: rid for a, rid in pairs}
    assert "google_artifact_registry_repository.ar" in by_addr
    assert by_addr["module.cluster.google_compute_instance.app"] == (
        "projects/launchpad-504012/zones/europe-west3-a/instances/lp-new-instance-gcp-vm"
    )


def test_stash_restore_preserves_tfstate(tmp_path: Path) -> None:
    infra = tmp_path / "infra"
    tf = infra / "terraform"
    tf.mkdir(parents=True)
    (tf / "terraform.tfstate").write_text('{"version":4,"resources":[]}', encoding="utf-8")
    (tf / "main.tf").write_text("# old\n", encoding="utf-8")

    stash = stash_iac_runtime_state(infra)
    assert stash is not None

    import shutil

    shutil.rmtree(infra)
    infra.mkdir()
    (infra / "terraform").mkdir()
    (infra / "terraform" / "main.tf").write_text("# new\n", encoding="utf-8")

    restore_iac_runtime_state(infra, stash)
    restored = (infra / "terraform" / "terraform.tfstate").read_text(encoding="utf-8")
    assert '"version":4' in restored
    assert (infra / "terraform" / "main.tf").read_text(encoding="utf-8") == "# new\n"


def test_apply_adopts_existing_on_409(tmp_path: Path) -> None:
    tf = tmp_path / "infra" / "terraform"
    tf.mkdir(parents=True)
    (tf / "main.tf").write_text('resource "null_resource" "x" {}\n', encoding="utf-8")
    (tf / "variables.tf").write_text(
        'variable "project_id" {\n  default = "my-proj"\n}\n'
        'variable "region" {\n  default = "europe-west3"\n}\n',
        encoding="utf-8",
    )
    (tf / "terraform.tfvars").write_text(
        'environment_id = "demo"\n',
        encoding="utf-8",
    )

    calls: list[list[str]] = []
    apply_attempts = {"n": 0}

    def fake_run(cmd, **_):
        calls.append(list(cmd))
        if cmd[1] == "init":
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
        if cmd[1] == "apply":
            apply_attempts["n"] += 1
            if apply_attempts["n"] == 1:
                return subprocess.CompletedProcess(
                    cmd,
                    1,
                    stdout="",
                    stderr=(
                        "Error: Error creating Repository: googleapi: Error 409: "
                        "the repository already exists\n\n"
                        "  with google_artifact_registry_repository.ar,\n"
                        "Error: Error creating instance: googleapi: Error 409: "
                        "The resource 'projects/my-proj/zones/europe-west3-a/"
                        "instances/lp-demo-vm' already exists, alreadyExists\n\n"
                        "  with module.cluster.google_compute_instance.app,\n"
                    ),
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="applied", stderr="")
        if cmd[1] == "import":
            return subprocess.CompletedProcess(cmd, 0, stdout="Import successful", stderr="")
        if cmd[1] == "output":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"public_ip": {"value": "1.2.3.4"}}),
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    with (
        patch("app.services.iac_apply.shutil.which", return_value="/usr/bin/terraform"),
        patch("app.services.iac_apply._run", side_effect=fake_run),
    ):
        result = run_workspace_iac_apply(
            root_dir=str(tmp_path),
            engine="terraform",
            credentials=None,
            org_id="o",
            workspace_id="w",
            settings=_settings(),
        )

    assert result.status == "applied"
    assert result.outputs["public_ip"] == "1.2.3.4"
    assert apply_attempts["n"] == 2
    import_cmds = [c for c in calls if len(c) > 1 and c[1] == "import"]
    assert len(import_cmds) >= 1
    joined = [" ".join(c) for c in import_cmds]
    assert any("module.cluster.google_compute_instance.app" in j for j in joined)
    assert any("google_artifact_registry_repository.ar" in j for j in joined)
    assert any("europe-west3-a" in j for j in joined)
    assert any("locations/europe-west3/repositories/lp-demo" in j for j in joined)
    assert any("module.vpc.google_compute_network.vpc" in j for j in joined)
    assert any("module.vpc.google_compute_subnetwork.subnet" in j for j in joined)


def test_known_pulumi_gcp_import_includes_vm() -> None:
    from app.services.iac_apply import _known_pulumi_gcp_import_targets

    targets = _known_pulumi_gcp_import_targets(
        {
            "project_id": "launchpad-504012",
            "region": "europe-west3",
            "environment_id": "pulumi-code",
        }
    )
    by_name = {name: rid for _type, name, rid in targets}
    assert by_name["lp-vm"].endswith("/instances/lp-pulumi-code-vm")
    assert by_name["lp-vpc"].endswith("/networks/lp-pulumi-code-vpc")
    assert by_name["lp-subnet"].endswith("/subnetworks/lp-pulumi-code-subnet")

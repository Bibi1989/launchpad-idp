"""Tests for the deployment-runner plugin/adapter layer (app.plugins).

These verify the wrapper logic (command construction, result mapping, registry) without
requiring real terraform / pulumi / ansible binaries - the tool execution is mocked.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from app.plugins import (
    AnsibleConfigPlugin,
    AwsTerraformPlugin,
    CloudServicePlugin,
    GcpPulumiPlugin,
    PluginRegistry,
    PluginStatus,
)


def _completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


# --- interface / registry -------------------------------------------------------------


def test_plugins_implement_interface(tmp_path):
    assert issubclass(AwsTerraformPlugin, CloudServicePlugin)
    assert issubclass(GcpPulumiPlugin, CloudServicePlugin)
    assert issubclass(AnsibleConfigPlugin, CloudServicePlugin)


def test_registry_register_and_lookup(tmp_path):
    reg = PluginRegistry()
    tf = AwsTerraformPlugin(tmp_path)
    reg.register("aws", tf)
    assert reg.get("aws") is tf
    assert reg.require("aws") is tf
    assert "aws" in reg
    assert reg.keys() == ["aws"]
    with pytest.raises(KeyError):
        reg.require("missing")


def test_registry_no_override_by_default(tmp_path):
    reg = PluginRegistry()
    a = AwsTerraformPlugin(tmp_path)
    b = AwsTerraformPlugin(tmp_path)
    reg.register("aws", a)
    reg.register("aws", b)  # ignored
    assert reg.get("aws") is a
    reg.register("aws", b, override=True)
    assert reg.get("aws") is b


# --- AwsTerraformPlugin (Terraform CLI wrapped) ---------------------------------------


def test_terraform_provision_runs_init_then_apply(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    monkeypatch.setattr("app.plugins.aws_terraform_plugin.resolve_terraform_bin", lambda **k: "/usr/bin/terraform")

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "output":
            return _completed(cmd, 0, stdout='{"ip": {"value": "10.0.0.1"}}')
        return _completed(cmd, 0, stdout="ok")

    monkeypatch.setattr(subprocess, "run", fake_run)
    plugin = AwsTerraformPlugin(tmp_path)
    result = plugin.provision({"region": "us-east-1"})

    assert result.status is PluginStatus.SUCCESS
    assert result.outputs == {"ip": "10.0.0.1"}
    verbs = [c[1] for c in calls]
    assert verbs[0] == "init" and "apply" in verbs
    apply_cmd = next(c for c in calls if c[1] == "apply")
    assert "-var" in apply_cmd and "region=us-east-1" in apply_cmd
    # runs in the existing .tf directory, never modifies it
    assert "apply" in apply_cmd


def test_terraform_apply_failure_maps_to_failed(tmp_path, monkeypatch):
    monkeypatch.setattr("app.plugins.aws_terraform_plugin.resolve_terraform_bin", lambda **k: "/usr/bin/terraform")

    def fake_run(cmd, **kwargs):
        if cmd[1] == "apply":
            return _completed(cmd, 1, stderr="boom")
        return _completed(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = AwsTerraformPlugin(tmp_path).provision()
    assert result.status is PluginStatus.FAILED
    assert "boom" in result.raw


def test_terraform_skipped_when_cli_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("app.plugins.aws_terraform_plugin.resolve_terraform_bin", lambda **k: None)
    result = AwsTerraformPlugin(tmp_path).provision()
    assert result.status is PluginStatus.SKIPPED


def test_terraform_destroy(tmp_path, monkeypatch):
    monkeypatch.setattr("app.plugins.aws_terraform_plugin.resolve_terraform_bin", lambda **k: "/usr/bin/terraform")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _completed(cmd, 0))
    result = AwsTerraformPlugin(tmp_path).destroy()
    assert result.status is PluginStatus.DESTROYED


# --- GcpPulumiPlugin (Automation API wrapped) ----------------------------------------


def test_pulumi_skipped_when_automation_api_missing(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pulumi.automation" or name == "pulumi":
            raise ImportError("no pulumi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = GcpPulumiPlugin(tmp_path, stack_name="dev").provision()
    assert result.status is PluginStatus.SKIPPED
    assert "Automation API" in result.message


# --- AnsibleConfigPlugin (Ansible CLI wrapped) ---------------------------------------


def test_ansible_requires_host(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook.yml"
    playbook.write_text("- hosts: all\n")
    result = AnsibleConfigPlugin(playbook).provision({})
    assert result.status is PluginStatus.FAILED
    assert "host" in result.message


def test_ansible_runs_playbook_against_host(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook.yml"
    playbook.write_text("- hosts: all\n")
    monkeypatch.setattr(
        "app.plugins.ansible_config_plugin._resolve_ansible_playbook",
        lambda: "/usr/bin/ansible-playbook",
    )
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _completed(cmd, 0, stdout="PLAY RECAP ok")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = AnsibleConfigPlugin(playbook).provision({"host": "203.0.113.10"})
    assert result.status is PluginStatus.SUCCESS
    cmd = captured[0]
    assert str(playbook) in cmd
    assert "-i" in cmd and "203.0.113.10," in cmd  # ad-hoc single-host inventory


def test_ansible_destroy_is_skipped(tmp_path):
    playbook = tmp_path / "playbook.yml"
    playbook.write_text("- hosts: all\n")
    result = AnsibleConfigPlugin(playbook).destroy()
    assert result.status is PluginStatus.SKIPPED


# --- declarative manifest plugins -----------------------------------------------------

from app.plugins import (
    ManifestError,
    ManifestPlugin,
    load_manifest,
    manifest_to_catalog_entry,
    resolve_inputs,
)
from app.plugins import (
    PluginRegistry as _Reg,  # noqa: F401
)


def _terraform_manifest(working_dir="hetzner"):
    return {
        "id": "my-hetzner",
        "label": "My Hetzner (Terraform)",
        "runtime_targets": ["vm"],
        "credential_fields": [{"name": "hcloud_token", "label": "Token"}],
        "regions": [{"value": "nbg1", "label": "Nuremberg"}],
        "tiers": [{"id": "cx22", "label": "CX22", "vcpus": 2, "memory_mb": 4096}],
        "runner": {
            "type": "terraform",
            "working_dir": working_dir,
            "var_mapping": {"hcloud_token": "${credentials.hcloud_token}", "region": "${spec.region}"},
        },
    }


def test_load_manifest_valid_and_invalid():
    m = load_manifest(_terraform_manifest())
    assert m.id == "my-hetzner" and m.runner.type == "terraform"
    with pytest.raises(ManifestError):
        load_manifest({"id": "x"})  # missing runner


def test_resolve_inputs_substitutes_placeholders():
    out = resolve_inputs(
        {"tok": "${credentials.hcloud_token}", "region": "${spec.region}", "lit": "static"},
        credentials={"hcloud_token": "secret"},
        spec={"region": "nbg1"},
    )
    assert out == {"tok": "secret", "region": "nbg1", "lit": "static"}


def test_manifest_to_catalog_entry_shape():
    entry = manifest_to_catalog_entry(load_manifest(_terraform_manifest()))
    assert entry["id"] == "my-hetzner"
    assert entry["source"] == "manifest"
    assert entry["credential_fields"][0]["name"] == "hcloud_token"


def test_manifest_plugin_delegates_to_terraform_runner(tmp_path, monkeypatch):
    (tmp_path / "hetzner").mkdir()
    monkeypatch.setattr("app.plugins.aws_terraform_plugin.resolve_terraform_bin", lambda **k: "/usr/bin/terraform")
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.setdefault("cmds", []).append(cmd)
        seen["cwd"] = kwargs.get("cwd")
        if cmd[1] == "output":
            return _completed(cmd, 0, stdout="{}")
        return _completed(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    plugin = ManifestPlugin(load_manifest(_terraform_manifest()), bundle_root=tmp_path)
    result = plugin.provision({"credentials": {"hcloud_token": "secret"}, "spec": {"region": "nbg1"}})

    assert result.status is PluginStatus.SUCCESS
    # ran terraform in the manifest's working_dir under the bundle root
    assert seen["cwd"] == str(tmp_path / "hetzner")
    apply = next(c for c in seen["cmds"] if c[1] == "apply")
    # mapped vars were passed through to the runner
    assert "region=nbg1" in apply and "hcloud_token=secret" in apply


def test_manifest_plugin_blocks_path_traversal(tmp_path):
    plugin = ManifestPlugin(load_manifest(_terraform_manifest(working_dir="../../etc")), bundle_root=tmp_path)
    result = plugin.provision({"credentials": {}, "spec": {}})
    assert result.status is PluginStatus.FAILED
    assert "escapes the bundle root" in result.message


def test_manifest_registers_like_any_runner(tmp_path):
    reg = PluginRegistry()
    plugin = ManifestPlugin(load_manifest(_terraform_manifest()), bundle_root=tmp_path)
    reg.register(plugin.id, plugin)
    assert reg.require("my-hetzner") is plugin


# --- manifest alias tolerance + defaults (Pydantic v2) --------------------------------


def test_manifest_accepts_displayname_and_engine_aliases():
    # The exact shape that previously failed: displayName + runner.engine, no label/type.
    m = load_manifest({
        "displayName": "DigitalOcean Droplets",
        "runner": {"engine": "node", "entry": "DigitalOceanProvider"},
    })
    assert m.label == "DigitalOcean Droplets"
    assert m.id == "digitalocean-droplets"  # derived from the display name
    assert m.runner.type == "node"
    assert m.runner.entry == "DigitalOceanProvider"


def test_manifest_name_and_runtime_aliases():
    m = load_manifest({"name": "Cloudflare Tunnels", "runtime": {"engine": "cli", "command": "cloudflared"}})
    assert m.label == "Cloudflare Tunnels"
    assert m.runner.type == "cli"


def test_manifest_optional_metadata_defaults():
    m = load_manifest({"label": "Bare"})
    assert m.capabilities == [] and m.hooks == {}
    assert m.credentials_schema == {} and m.deployment_config_schema == {}
    assert m.runner.type == "node"  # sensible default runner


def test_manifest_clean_error_names_missing_fields():
    with pytest.raises(ManifestError) as exc:
        load_manifest({"runner": {"type": "terraform"}})
    assert "label: Field required" in str(exc.value)


def test_manifest_round_trips_through_storage():
    m = load_manifest({"displayName": "DO", "runner": {"engine": "terraform", "working_dir": "do"}})
    reloaded = load_manifest(json.loads(m.model_dump_json()))
    assert reloaded.id == m.id and reloaded.runner.type == "terraform"


def test_code_engine_validates_but_reports_not_executable(tmp_path):
    m = load_manifest({"label": "Node Plugin", "runner": {"engine": "node", "entry": "X"}})
    result = ManifestPlugin(m, bundle_root=tmp_path).provision({"credentials": {}, "spec": {}})
    assert result.status is PluginStatus.FAILED
    assert "not executable yet" in result.message


def test_load_manifests_from_dir_skips_bad_files(tmp_path):
    (tmp_path / "good.json").write_text(json.dumps({"label": "Good", "runner": {"engine": "terraform"}}))
    (tmp_path / "bad.json").write_text("{ not valid json")
    (tmp_path / "invalid.json").write_text(json.dumps({"runner": {}}))  # missing label
    from app.plugins.manifest import load_manifests_from_dir

    loaded = load_manifests_from_dir(tmp_path)
    assert [m.id for m, _ in loaded] == ["good"]  # bad + invalid skipped, not crashed


# --- modal registration: fixed fields + JSON Schema validation ------------------------

from app.plugins.manifest import manifest_field_errors  # noqa: E402


def test_manifest_accepts_fixed_fields_and_capability_object():
    m = load_manifest({
        "id": "digitalocean-droplet",
        "label": "DigitalOcean Droplets",
        "version": "1.2.0",
        "category": "cloud-provider",
        "icon": "https://x/do.svg",
        "runner": {"type": "terraform", "bundlePath": "digitalocean"},
        "capabilities": {"serviceType": "vm", "supportsTtl": True, "supportsEphemeralDb": True},
    })
    assert m.version == "1.2.0" and m.category == "cloud-provider"
    assert m.runner.working_dir == "digitalocean"  # bundlePath alias
    entry = manifest_to_catalog_entry(m)
    assert entry["capabilities"] == ["vm", "supportsTtl", "supportsEphemeralDb"]


def test_invalid_json_schema_is_a_field_error():
    errs = manifest_field_errors({
        "label": "X",
        "credentialsSchema": {"type": "not-a-type"},
    })
    locs = {e["loc"] for e in errs}
    assert any("credentialsSchema" in loc or "credentials_schema" in loc for loc in locs)


def test_bad_semver_is_a_field_error():
    errs = manifest_field_errors({"label": "X", "version": "nope", "runner": {"type": "terraform"}})
    assert any(e["loc"] == "version" for e in errs)


def test_valid_full_manifest_has_no_errors():
    assert manifest_field_errors({
        "label": "OK",
        "version": "1.0.0",
        "runner": {"type": "terraform", "working_dir": "x"},
        "credentialsSchema": {"type": "object", "properties": {"t": {"type": "string"}}},
    }) == []

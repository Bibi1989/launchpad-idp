"""Tests for the plugin-based multi-cloud provisioning engine (app.providers)."""

from __future__ import annotations

import httpx
import pytest
import yaml

from app.providers import (
    CredentialError,
    DeploymentStatus,
    ProvisioningError,
    ProvisionSpec,
    RuntimeTarget,
    build_catalog,
    get_provider,
    require_provider,
)
from app.providers.base import rollback_on_error
from app.providers.cloud_init import (
    poll_http_healthy,
    render_cloud_init,
    render_docker_user_data_bash,
    render_health_poll_script,
)


@pytest.fixture
def mock_httpx(monkeypatch):
    """Route every httpx.Client through a MockTransport with a supplied handler."""

    real_client = httpx.Client

    def install(handler):
        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", factory)

    return install


# --- base: rollback -------------------------------------------------------------------


def test_rollback_runs_cleanups_in_reverse_and_wraps_error():
    calls: list[str] = []
    with pytest.raises(ProvisioningError) as exc, rollback_on_error("test") as tracker:
        tracker.track("a", lambda: calls.append("a"))
        tracker.track("b", lambda: calls.append("b"))
        raise RuntimeError("boom")
    assert calls == ["b", "a"]  # newest first
    assert exc.value.partial_resource_ids == ["a", "b"]


def test_rollback_no_error_keeps_resources():
    with rollback_on_error("test") as tracker:
        tracker.track("a", lambda: None)
    assert tracker.resource_ids == ["a"]


# --- cloud-init -----------------------------------------------------------------------


def test_render_cloud_init_is_valid_yaml_and_injects_env():
    ci = render_cloud_init(
        image="ghcr.io/acme/app:1.2.3",
        app_port=3000,
        env_vars={"FOO": "bar", "TOKEN": "secret"},
        ssh_authorized_keys=["ssh-ed25519 AAAAKEY"],
    )
    assert ci.startswith("#cloud-config\n")
    parsed = yaml.safe_load(ci)
    env_file = next(f for f in parsed["write_files"] if f["path"].endswith("app.env"))
    assert "FOO=bar" in env_file["content"]
    assert "PORT=3000" in env_file["content"]
    unit = next(f for f in parsed["write_files"] if f["path"].endswith(".service"))
    assert "ghcr.io/acme/app:1.2.3" in unit["content"]
    assert parsed["ssh_authorized_keys"] == ["ssh-ed25519 AAAAKEY"]


def test_render_bash_user_data_and_health_script():
    bash = render_docker_user_data_bash(image="nginx:latest", app_port=8080, env_vars={"A": "1"})
    assert bash.startswith("#!/bin/bash")
    assert "nginx:latest" in bash
    script = render_health_poll_script(app_port=8080, health_path="healthz")
    assert "127.0.0.1:8080/healthz" in script


def test_poll_http_healthy(mock_httpx):
    mock_httpx(lambda req: httpx.Response(200, text="ok"))
    assert poll_http_healthy("http://example.test/", timeout_seconds=1, interval_seconds=0.5) is True


def test_poll_http_healthy_times_out(mock_httpx):
    mock_httpx(lambda req: httpx.Response(503))
    assert poll_http_healthy("http://example.test/", timeout_seconds=0, interval_seconds=0.5) is False


# --- registry / catalog ---------------------------------------------------------------


def test_catalog_lists_all_builtin_providers():
    ids = {c["id"] for c in build_catalog()}
    assert {"hetzner", "digitalocean", "railway", "gcp", "aws", "azure"} <= ids


def test_require_provider_unknown_raises():
    with pytest.raises(KeyError):
        require_provider("does-not-exist")


def test_credential_fields_present():
    het = get_provider("hetzner")
    assert het is not None
    names = [f.name for f in het.credential_fields()]
    assert "api_token" in names


# --- Hetzner --------------------------------------------------------------------------


def test_hetzner_provision_and_status(mock_httpx):
    server = {
        "id": 4711,
        "status": "initializing",
        "public_net": {"ipv4": {"ip": "203.0.113.10"}},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/v1/servers":
            return httpx.Response(201, json={"server": server})
        if req.method == "GET" and req.url.path == "/v1/servers/4711":
            running = {**server, "status": "running"}
            return httpx.Response(200, json={"server": running})
        return httpx.Response(404, json={"error": "nope"})

    mock_httpx(handler)
    provider = require_provider("hetzner")
    spec = ProvisionSpec(
        environment_id="env-1",
        runtime_target=RuntimeTarget.VM,
        image="ghcr.io/acme/app:latest",
        app_port=3000,
        region="nbg1",
        tier="cx22",
    )
    result = provider.provision("env-1", spec, credentials={"api_token": "tok"})
    assert result.resource_id == "4711"
    assert result.ip_address == "203.0.113.10"
    assert result.status == DeploymentStatus.PROVISIONING

    status = provider.get_status("4711", credentials={"api_token": "tok"})
    assert status.status == DeploymentStatus.RUNNING


def test_hetzner_provision_requires_image():
    provider = require_provider("hetzner")
    spec = ProvisionSpec(environment_id="e", runtime_target=RuntimeTarget.VM)
    with pytest.raises(CredentialError):
        provider.provision("e", spec, credentials={"api_token": "tok"})


def test_hetzner_destroy_is_idempotent(mock_httpx):
    mock_httpx(lambda req: httpx.Response(404))
    provider = require_provider("hetzner")
    provider.destroy("9999", credentials={"api_token": "tok"})  # no raise on 404


def test_hetzner_validate_credentials(mock_httpx):
    mock_httpx(lambda req: httpx.Response(401, json={"error": "unauthorized"}))
    provider = require_provider("hetzner")
    assert provider.validate_credentials({"api_token": "bad"}) is False


# --- DigitalOcean ---------------------------------------------------------------------


def test_digitalocean_provision(mock_httpx):
    droplet = {
        "id": 555,
        "status": "new",
        "networks": {"v4": [{"type": "public", "ip_address": "198.51.100.7"}]},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/v2/droplets":
            return httpx.Response(202, json={"droplet": droplet})
        if req.method == "POST" and req.url.path == "/v2/account/keys":
            return httpx.Response(201, json={"ssh_key": {"id": 1}})
        return httpx.Response(404)

    mock_httpx(handler)
    provider = require_provider("digitalocean")
    spec = ProvisionSpec(
        environment_id="env-2",
        image="ghcr.io/acme/app:latest",
        region="nyc3",
        tier="s-1vcpu-1gb",
    )
    result = provider.provision("env-2", spec, credentials={"api_token": "tok"})
    assert result.resource_id == "555"
    assert result.ip_address == "198.51.100.7"


# --- Railway --------------------------------------------------------------------------


def test_railway_provision_creates_project_and_service(mock_httpx):
    def handler(req: httpx.Request) -> httpx.Response:
        body = req.content.decode()
        if "projectCreate" in body:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "projectCreate": {
                            "id": "proj-1",
                            "name": "lp-env",
                            "environments": {"edges": [{"node": {"id": "rwenv-1"}}]},
                        }
                    }
                },
            )
        if "serviceCreate" in body:
            return httpx.Response(200, json={"data": {"serviceCreate": {"id": "svc-1"}}})
        if "variableUpsert" in body:
            return httpx.Response(200, json={"data": {"variableUpsert": True}})
        return httpx.Response(200, json={"data": {}})

    mock_httpx(handler)
    provider = require_provider("railway")
    spec = ProvisionSpec(
        environment_id="env-3",
        runtime_target=RuntimeTarget.PAAS,
        image="ghcr.io/acme/app:latest",
        env_vars={"NODE_ENV": "production"},
    )
    result = provider.provision("env-3", spec, credentials={"api_token": "tok"})
    assert result.resource_id == "svc-1"
    assert "proj-1" in result.resource_ids
    assert result.runtime_target == RuntimeTarget.PAAS


def test_railway_rolls_back_project_on_service_failure(mock_httpx):
    deleted: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = req.content.decode()
        if "projectCreate" in body:
            return httpx.Response(
                200,
                json={"data": {"projectCreate": {"id": "proj-x", "environments": {"edges": []}}}},
            )
        if "serviceCreate" in body:
            return httpx.Response(200, json={"errors": [{"message": "quota exceeded"}]})
        if "projectDelete" in body:
            deleted.append("proj-x")
            return httpx.Response(200, json={"data": {"projectDelete": True}})
        return httpx.Response(200, json={"data": {}})

    mock_httpx(handler)
    provider = require_provider("railway")
    spec = ProvisionSpec(environment_id="env-4", runtime_target=RuntimeTarget.PAAS, image="img:1")
    with pytest.raises(ProvisioningError):
        provider.provision("env-4", spec, credentials={"api_token": "tok"})
    assert deleted == ["proj-x"]  # rollback deleted the orphan project


# --- catalog: native + legacy coexist -------------------------------------------------


def test_catalog_has_native_and_legacy_ids():
    ids = {c["id"] for c in build_catalog()}
    assert {"aws", "gcp", "azure", "cloudflare"} <= ids  # native own primary ids
    assert {"aws-legacy", "gcp-legacy", "azure-legacy"} <= ids  # bridges retained


# --- AWS (boto3, faked) ---------------------------------------------------------------


class _FakeEC2:
    def __init__(self, record):
        self._record = record

    def describe_security_groups(self, **kw):
        return {"SecurityGroups": []}

    def create_security_group(self, **kw):
        return {"GroupId": "sg-123"}

    def authorize_security_group_ingress(self, **kw):
        self._record["ingress"] = kw

    def run_instances(self, **kw):
        self._record["user_data"] = kw["UserData"]
        return {"Instances": [{
            "InstanceId": "i-abc",
            "State": {"Name": "pending"},
            "PublicIpAddress": "203.0.113.55",
        }]}

    def describe_instances(self, **kw):
        return {"Reservations": [{"Instances": [{
            "InstanceId": "i-abc",
            "State": {"Name": "running"},
            "PublicIpAddress": "203.0.113.55",
        }]}]}

    def terminate_instances(self, **kw):
        self._record["terminated"] = kw["InstanceIds"]

    def delete_security_group(self, **kw):
        self._record["sg_deleted"] = kw["GroupId"]


class _FakeSSM:
    def get_parameter(self, **kw):
        return {"Parameter": {"Value": "ami-0ubuntu"}}


class _FakeSTS:
    def get_caller_identity(self):
        return {"Account": "123456789012"}


@pytest.fixture
def fake_boto3(monkeypatch):
    import boto3

    record: dict = {}

    def client(service, **kwargs):
        if service == "ec2":
            return _FakeEC2(record)
        if service == "ssm":
            return _FakeSSM()
        if service == "sts":
            return _FakeSTS()
        raise AssertionError(f"unexpected boto3 client {service}")

    monkeypatch.setattr(boto3, "client", client)
    return record


def test_aws_provision_and_status(fake_boto3):
    provider = require_provider("aws")
    spec = ProvisionSpec(
        environment_id="env-a",
        image="ghcr.io/acme/app:latest",
        region="us-east-1",
        tier="t3.small",
        app_port=3000,
    )
    creds = {"aws_access_key_id": "AKIA", "aws_secret_access_key": "sekret", "aws_region": "us-east-1"}
    result = provider.provision("env-a", spec, credentials=creds)
    assert result.resource_id == "i-abc"
    assert result.ip_address == "203.0.113.55"
    assert "sg-123" in result.resource_ids
    assert "#cloud-config" in fake_boto3["user_data"]

    status = provider.get_status("i-abc", credentials=creds)
    assert status.status == DeploymentStatus.RUNNING


def test_aws_validate_credentials(fake_boto3):
    provider = require_provider("aws")
    assert provider.validate_credentials(
        {"aws_access_key_id": "AKIA", "aws_secret_access_key": "s"}
    ) is True


# --- GCP (token faked, REST mocked) ---------------------------------------------------


def test_gcp_provision(monkeypatch, mock_httpx):
    provider = require_provider("gcp")
    monkeypatch.setattr(type(provider), "_token_and_project", lambda self, creds: ("tok", "proj-1"))

    instance = {
        "status": "PROVISIONING",
        "networkInterfaces": [{"accessConfigs": [{"natIP": "34.68.1.2"}]}],
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path.endswith("/global/firewalls"):
            return httpx.Response(200, json={"name": "fw-op"})
        if req.method == "POST" and req.url.path.endswith("/instances"):
            return httpx.Response(200, json={"name": "insert-op"})
        if req.method == "GET" and "/instances/" in req.url.path:
            return httpx.Response(200, json=instance)
        return httpx.Response(404, json={"error": {"code": 404}})

    mock_httpx(handler)
    spec = ProvisionSpec(environment_id="env-g", image="img:1", region="us-central1", tier="e2-small")
    result = provider.provision("env-g", spec, credentials={"gcp_sa_key_json": "{}"})
    assert result.ip_address == "34.68.1.2"
    assert result.resource_id.startswith("us-central1-a/")


# --- Azure (token faked, ARM mocked) --------------------------------------------------


def test_azure_provision(monkeypatch, mock_httpx):
    provider = require_provider("azure")
    monkeypatch.setattr(type(provider), "_token", lambda self, creds: "tok")

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "PUT":
            return httpx.Response(200, json={"id": path, "properties": {}})
        if req.method == "GET" and "publicIPAddresses" in path:
            return httpx.Response(200, json={"properties": {"ipAddress": "20.10.5.6"}})
        return httpx.Response(200, json={})

    mock_httpx(handler)
    creds = {
        "azure_client_id": "c", "azure_client_secret": "s",
        "azure_tenant_id": "t", "azure_subscription_id": "sub-1",
    }
    spec = ProvisionSpec(environment_id="env-z", image="img:1", region="eastus", tier="Standard_B2s")
    result = provider.provision("env-z", spec, credentials=creds)
    assert result.ip_address == "20.10.5.6"
    assert result.resource_id.startswith("launchpad-")


# --- Cloudflare (REST mocked) ---------------------------------------------------------


def test_cloudflare_provision_worker(mock_httpx):
    uploaded: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/workers/subdomain"):
            return httpx.Response(200, json={"result": {"subdomain": "acme"}})
        if req.method == "PUT" and "/workers/scripts/" in path:
            uploaded["hit"] = True
            return httpx.Response(200, json={"success": True, "result": {}})
        return httpx.Response(200, json={"success": True, "result": []})

    mock_httpx(handler)
    provider = require_provider("cloudflare")
    spec = ProvisionSpec(
        environment_id="env-cf",
        runtime_target=RuntimeTarget.PAAS,
        env_vars={"API_KEY": "x"},
        extra={"worker_script": "addEventListener('fetch', e => e.respondWith(new Response('hi')))"},
    )
    creds = {"cloudflare_api_token": "tok", "cloudflare_account_id": "acct-1"}
    result = provider.provision("env-cf", spec, credentials=creds)
    assert uploaded.get("hit") is True
    assert result.endpoints == ["https://lp-env-cf.acme.workers.dev"]
    assert result.status == DeploymentStatus.RUNNING


def test_cloudflare_is_paas_only():
    provider = require_provider("cloudflare")
    assert provider.service_types() == [RuntimeTarget.PAAS]


# --- provisioning tools catalog -------------------------------------------------------


def test_tools_catalog_has_generic_and_native():
    from app.providers.tools import build_tools_catalog

    ids = {t["id"] for t in build_tools_catalog()}
    assert {"terraform", "opentofu", "pulumi", "ansible", "cloud-init"} <= ids
    assert {"aws-native", "azure-native", "gcp-native"} <= ids


def test_tools_for_cloud_restricts_native_to_matching_cloud():
    from app.providers.tools import tools_for_cloud

    aws = {t["id"] for t in tools_for_cloud("aws")}
    assert "aws-native" in aws
    assert "azure-native" not in aws and "gcp-native" not in aws

    hetzner = {t["id"] for t in tools_for_cloud("hetzner")}
    assert hetzner == {
        "scripting",
        "terraform",
        "opentofu",
        "pulumi",
        "ansible",
        "cloud-init",
        "puppet",
        "chef",
    }

    azure = {t["id"] for t in tools_for_cloud("azure")}
    assert "azure-native" in azure and "aws-native" not in azure


# --- provider services taxonomy -------------------------------------------------------


def test_services_grouped_by_runtime():
    from app.providers.provider_services import services_for

    aws = {g["runtime"] for g in services_for("aws")}
    assert {"kubernetes", "docker", "vm"} <= aws
    assert {g["runtime"] for g in services_for("cloudflare")} == {"paas"}
    # legacy alias shares base services
    assert services_for("aws-legacy") == services_for("aws")


# --- provisioning scaffold ------------------------------------------------------------


def test_scaffold_scripting_is_default_and_emits_cloud_init():
    from app.providers.base import ProvisionSpec
    from app.providers.provisioning_scaffold import render_provisioning_files

    spec = ProvisionSpec(environment_id="e", image="img:1", app_port=3000)
    files = render_provisioning_files("scripting", "hetzner", spec)
    paths = [f.path for f in files]
    assert "provision/cloud-init.yaml" in paths
    ci = next(f for f in files if f.path.endswith("cloud-init.yaml"))
    assert ci.content.startswith("#cloud-config")


def test_scaffold_terraform_targets_selected_cloud():
    from app.providers.base import ProvisionSpec
    from app.providers.provisioning_scaffold import render_provisioning_files

    spec = ProvisionSpec(environment_id="e", image="img:1", region="nbg1")
    files = render_provisioning_files("terraform", "hetzner", spec)
    main = next(f for f in files if f.path.endswith("main.tf"))
    assert "hcloud" in main.content
    assert any(f.path.endswith("cloud-init.yaml") for f in files)


def test_scaffold_unknown_tool_falls_back_to_scripting():
    from app.providers.base import ProvisionSpec
    from app.providers.provisioning_scaffold import render_provisioning_files

    spec = ProvisionSpec(environment_id="e", image="img:1")
    files = render_provisioning_files("nonsense", "aws", spec)
    assert any(f.path == "provision/cloud-init.yaml" for f in files)


def test_tools_catalog_has_single_default():
    from app.providers.tools import build_tools_catalog

    defaults = [(t["id"], t["category"]) for t in build_tools_catalog() if t.get("default")]
    assert defaults == [("scripting", "iac"), ("cloud-init", "config")]


# --- Linode + Render (new plugins) ----------------------------------------------------


def test_linode_provision(mock_httpx):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/v4/linode/instances":
            return httpx.Response(200, json={"id": 12345, "status": "provisioning", "ipv4": ["139.0.0.9"]})
        return httpx.Response(404)

    mock_httpx(handler)
    provider = require_provider("linode")
    spec = ProvisionSpec(environment_id="env-l", image="ghcr.io/acme/app:latest", region="us-east", tier="g6-nanode-1")
    result = provider.provision("env-l", spec, credentials={"api_token": "tok"})
    assert result.resource_id == "12345"
    assert result.ip_address == "139.0.0.9"
    assert result.runtime_target == RuntimeTarget.VM


def test_linode_requires_image():
    provider = require_provider("linode")
    spec = ProvisionSpec(environment_id="e", runtime_target=RuntimeTarget.VM)
    with pytest.raises(CredentialError):
        provider.provision("e", spec, credentials={"api_token": "tok"})


def test_render_provision_from_image(mock_httpx):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/v1/owners":
            return httpx.Response(200, json=[{"owner": {"id": "usr-1"}}])
        if req.method == "POST" and req.url.path == "/v1/services":
            return httpx.Response(201, json={"service": {"id": "srv-1", "serviceDetails": {"url": "https://x.onrender.com"}}})
        return httpx.Response(404)

    mock_httpx(handler)
    provider = require_provider("render")
    spec = ProvisionSpec(environment_id="env-r", runtime_target=RuntimeTarget.PAAS, image="ghcr.io/acme/app:latest", tier="starter")
    result = provider.provision("env-r", spec, credentials={"api_key": "rnd_x"})
    assert result.resource_id == "srv-1"
    assert result.endpoints == ["https://x.onrender.com"]


def test_render_rolls_back_on_failure(mock_httpx):
    deleted: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/v1/owners":
            return httpx.Response(200, json=[{"owner": {"id": "usr-1"}}])
        if req.method == "POST" and req.url.path == "/v1/services":
            return httpx.Response(500, text="boom")
        return httpx.Response(404)

    mock_httpx(handler)
    provider = require_provider("render")
    spec = ProvisionSpec(environment_id="env-r2", runtime_target=RuntimeTarget.PAAS, image="img:1")
    with pytest.raises(ProvisioningError):
        provider.provision("env-r2", spec, credentials={"api_key": "rnd_x"})


def test_new_providers_in_catalog_and_services():
    ids = {c["id"] for c in build_catalog()}
    assert {"linode", "render"} <= ids
    from app.providers.provider_services import services_for
    assert {g["runtime"] for g in services_for("linode")} == {"kubernetes", "docker", "vm"}
    assert {g["runtime"] for g in services_for("render")} == {"paas"}

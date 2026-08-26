from app.providers.registry import build_catalog
from app.providers.service_plugins import adapter_id_for, expand_service_plugins, split_plugin_id
from app.services.plugin_ai import PluginAiService


def test_expand_service_plugins_keeps_parents_and_adds_gke():
    expanded = expand_service_plugins(build_catalog())
    ids = {row["id"] for row in expanded}
    assert {"gcp", "aws", "azure", "gcp-gke", "aws-eks", "azure-aks", "cloudflare-workers"} <= ids
    gke = next(row for row in expanded if row["id"] == "gcp-gke")
    assert gke["parent_cloud"] == "gcp"
    assert gke["service"] == "gke"
    assert gke["source"] == "builtin-plugin"


def test_adapter_id_for_service_plugin():
    assert adapter_id_for("gcp-gke") == "gcp"
    assert adapter_id_for("aws") == "aws"
    assert split_plugin_id("digitalocean-droplet") == ("digitalocean", "droplet")


def test_plugin_ai_heuristic_gke_inherits_gcp():
    raw = PluginAiService()._heuristic_manifest("GKE cluster on Google Cloud")
    assert raw["id"] == "gcp-gke"
    assert raw.get("parentCloud") == "gcp"
    assert raw["capabilities"]["serviceType"] == "kubernetes"
    creds = raw["credentialsSchema"]
    assert "gcp_sa_key_json" in creds["properties"]
    assert "required" not in creds
    deploy = raw["deploymentConfigSchema"]
    assert deploy["properties"]["imageSource"]["enum"] == ["build_registry", "external"]
    assert "artifactRegistry" in deploy["properties"]
    assert deploy["properties"]["secretBackend"]["enum"] == ["secret_manager", "native_k8s"]


def test_plugin_ai_generate_schemas_gke(monkeypatch):
    monkeypatch.setattr(PluginAiService, "gemini_configured", property(lambda self: False))
    creds, deploy, source = PluginAiService().generate_schemas(
        parent_cloud="gcp",
        service_type="kubernetes",
        plugin_id="gcp-gke",
        label="Google GKE",
    )
    assert source == "heuristic"
    assert "gcp_sa_key_json" in creds["properties"]
    assert deploy["properties"]["clusterName"]["title"] == "Cluster name"


def test_plugin_ai_generate_heuristic_is_valid(monkeypatch):
    monkeypatch.setattr(PluginAiService, "gemini_configured", property(lambda self: False))
    manifest, source = PluginAiService().generate("provision a DigitalOcean droplet")
    assert source == "heuristic"
    assert manifest["id"] == "digitalocean-droplet"
    assert (manifest.get("parentCloud") or manifest.get("parent_cloud")) == "digitalocean"

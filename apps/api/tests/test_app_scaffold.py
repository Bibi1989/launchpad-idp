"""Tests for the runnable mini-application generator (app_scaffold).

Covers the acceptance criteria: frontend stacks get a UI dashboard, backend
stacks expose health APIs, database/Redis status is surfaced when selected, the
generated image (not the Nginx placeholder) is deployed, images target Kind via
`kind load` + `imagePullPolicy: IfNotPresent`, and non-core / multi-framework
selections keep the legacy behavior.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

from app.schemas.cloud import (
    CloudCredentials,
    CloudProvider,
    ContainerScaffoldConfig,
    DataStoreDependency,
    DependencyPlacement,
    IaCEngine,
    KubernetesPackaging,
    KubernetesWorkloadOptions,
    LocalCloudConfig,
    LocalResources,
    ProvisioningWizardRequest,
    WorkloadDependenciesConfig,
    WorkspaceArtifactsMode,
)
from app.services.iac_generator import IaCGenerator


def _generate(
    tmp_path: Path,
    *,
    name: str,
    stack: str,
    frameworks: list[str],
    dependencies: WorkloadDependenciesConfig | None = None,
    options: KubernetesWorkloadOptions | None = None,
    listen_port: int = 8080,
) -> Path:
    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name=name,
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
        kubernetes_options=options or KubernetesWorkloadOptions(config_map=True, secret=True),
        container_scaffold=ContainerScaffoldConfig(
            enabled=True,
            stack=stack,
            frameworks=frameworks,
            app_name=name,
            listen_port=listen_port,
        ),
        dependencies=dependencies or WorkloadDependenciesConfig(),
    )
    return Path(gen.generate(request).root_dir)


def _deployment_container(root: Path) -> tuple[dict, dict]:
    docs = list(yaml.safe_load_all((root / "infra/k8s/manifests/deployment.yaml").read_text()))
    deployment = next(d for d in docs if d and d.get("kind") == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return deployment, container


def test_fastapi_backend_generates_health_apis_and_real_image(tmp_path: Path) -> None:
    root = _generate(
        tmp_path,
        name="fastapi-svc",
        stack="fastapi",
        frameworks=["fastapi"],
        listen_port=8000,
        dependencies=WorkloadDependenciesConfig(
            postgres=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
            redis=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
        ),
    )

    # Real source exists and is valid Python.
    main_py = (root / "apps/fastapi-svc/main.py").read_text()
    ast.parse(main_py)
    for route in ("/health", "/ready", "/info", "/api/status"):
        assert f'"{route}"' in main_py or f"'{route}'" in main_py

    # Dependency drivers included.
    requirements = (root / "apps/fastapi-svc/requirements.txt").read_text()
    assert "psycopg[binary]" in requirements
    assert "redis==" in requirements

    # Deployment runs the generated image, not the Nginx placeholder.
    _, container = _deployment_container(root)
    assert container["image"] == "fastapi-svc:latest"
    assert container["imagePullPolicy"] == "IfNotPresent"
    assert container["ports"][0]["containerPort"] == 8000
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"
    assert container["readinessProbe"]["httpGet"]["path"] == "/ready"
    assert "nginx" not in container["image"]

    # Non-root backend UID + dependency env wiring.
    deployment, _ = _deployment_container(root)
    assert deployment["spec"]["template"]["spec"]["securityContext"]["runAsUser"] == 10001
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["HAS_DATABASE"] == "true"
    assert env["HAS_REDIS"] == "true"
    envfrom = {list(x)[0]: x[list(x)[0]]["name"] for x in container["envFrom"]}
    assert envfrom["secretRef"] == "app-secrets"

    # Secret carries the connection strings the app reads.
    secret = yaml.safe_load((root / "infra/k8s/manifests/secret.yaml").read_text())
    assert "DATABASE_URL" in secret["stringData"]
    assert "REDIS_URL" in secret["stringData"]


def test_node_backend_selects_drivers_and_dashboard(tmp_path: Path) -> None:
    root = _generate(
        tmp_path,
        name="node-svc",
        stack="express",
        frameworks=["express"],
        dependencies=WorkloadDependenciesConfig(
            mysql=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
            redis=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
        ),
    )
    pkg = json.loads((root / "apps/node-svc/package.json").read_text())
    assert "express" in pkg["dependencies"]
    assert "mysql2" in pkg["dependencies"]
    assert "redis" in pkg["dependencies"]
    assert "pg" not in pkg["dependencies"]  # postgres not selected

    server = (root / "apps/node-svc/server.js").read_text()
    assert "DASHBOARD_HTML" in server
    assert "/api/status" in server
    assert "checkDatabase" in server and "checkRedis" in server

    _, container = _deployment_container(root)
    assert container["image"] == "node-svc:latest"
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"


def test_react_frontend_generates_dashboard_ui(tmp_path: Path) -> None:
    root = _generate(
        tmp_path,
        name="web-app",
        stack="react_vite",
        frameworks=["react_vite"],
        dependencies=WorkloadDependenciesConfig(
            postgres=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
        ),
    )
    app_dir = root / "apps/web-app"
    for rel in ("index.html", "src/App.jsx", "src/main.jsx", "vite.config.js", "nginx/default.conf"):
        assert (app_dir / rel).is_file(), rel
    assert (app_dir / "docker-entrypoint.d/10-launchpad-config.sh").is_file()

    # Health endpoint for probes + SPA fallback.
    conf = (app_dir / "nginx/default.conf").read_text()
    assert "location = /healthz" in conf
    assert "try_files" in conf

    # Dashboard renders deployment metadata + dependency status.
    app_jsx = (app_dir / "src/App.jsx").read_text()
    assert "METADATA" in app_jsx
    assert "config.json" in app_jsx
    assert "hasDatabase" in app_jsx

    deployment, container = _deployment_container(root)
    assert container["image"] == "web-app:latest"
    assert container["ports"][0]["containerPort"] == 8080
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert deployment["spec"]["template"]["spec"]["securityContext"]["runAsUser"] == 101
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["HAS_DATABASE"] == "true"
    assert env["HAS_REDIS"] == "false"


def test_kind_scripts_build_load_and_deploy(tmp_path: Path) -> None:
    root = _generate(tmp_path, name="kind-svc", stack="fastapi", frameworks=["fastapi"])
    build = (root / "scripts/build-image.sh").read_text()
    load = (root / "scripts/kind-load.sh").read_text()
    deploy = (root / "scripts/deploy-kind.sh").read_text()

    assert "docker build -t" in build
    assert "kind-svc:latest" in build
    assert "kind load docker-image" in load
    assert "kind load docker-image" in deploy
    assert "kubectl apply -f infra/k8s/manifests/ -R" in deploy
    # Namespace-wide readiness wait (not hardcoded deployment/app).
    assert "wait --for=condition=Available" in deploy
    assert "deployment --all" in deploy
    # Scripts are executable.
    assert (root / "scripts/deploy-kind.sh").stat().st_mode & 0o111


def test_non_core_stack_uses_app_latest_placeholder(tmp_path: Path) -> None:
    # Single non-core stack (Rust) has no source generator -> legacy Dockerfile
    # + app:latest until the workspace image is built.
    root = _generate(tmp_path, name="rust-app", stack="rust", frameworks=["rust"])
    assert (root / "dockers/rust-app/Dockerfile").is_file()
    assert not (root / "apps/rust-app").exists()
    _, container = _deployment_container(root)
    assert container["image"] == "app:latest"


def test_multi_framework_generates_launch_manifests_not_nginx(tmp_path: Path) -> None:
    # Fullstack (multi-framework) catalog templates must deploy real per-stack
    # images via launch-* manifests, NOT the generic nginx Deployment.
    root = _generate(
        tmp_path,
        name="fullstack-app",
        stack="react_vite",
        frameworks=["react_vite", "fastapi"],
    )
    mdir = root / "infra/k8s/manifests"
    assert not (mdir / "deployment.yaml").exists()
    assert not (mdir / "service.yaml").exists()
    # DNS-1123 safe names (react_vite -> react-vite).
    web = yaml.safe_load((mdir / "launch-react-vite-deployment.yaml").read_text())
    api = yaml.safe_load((mdir / "launch-fastapi-deployment.yaml").read_text())
    assert web["spec"]["template"]["spec"]["containers"][0]["image"] == "fullstack-app-react-vite:latest"
    assert api["spec"]["template"]["spec"]["containers"][0]["image"] == "fullstack-app-fastapi:latest"
    # Frontend auto-exposed to preview.
    assert web["metadata"]["annotations"]["launchpad.io/preview-target"] == "true"
    # Runnable app source scaffolded for each stack.
    assert (root / "apps/fullstack-app-react-vite/src/App.jsx").is_file()
    assert (root / "apps/fullstack-app-fastapi/main.py").is_file()


_STATIC_FRONTENDS = ["react_vite", "vuejs", "svelte", "angular"]
_SSR_FRONTENDS = ["nextjs", "nuxtjs"]
_BACKENDS = ["fastapi", "flask", "django", "express", "node", "nestjs", "go", "springboot", "dotnet"]


@pytest.mark.parametrize("stack", _STATIC_FRONTENDS + _SSR_FRONTENDS + _BACKENDS)
def test_every_core_stack_generates_runnable_app(tmp_path: Path, stack: str) -> None:
    """Each core stack produces source + Dockerfile and deploys its own image."""
    root = _generate(
        tmp_path,
        name="svc",
        stack=stack,
        frameworks=[stack],
        dependencies=WorkloadDependenciesConfig(
            postgres=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
            redis=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
        ),
    )
    app_dir = root / "apps/svc"
    assert (app_dir / "Dockerfile").is_file()
    assert (app_dir / "README.md").is_file()
    assert (app_dir / ".dockerignore").is_file()
    # Has at least one real source file beyond metadata.
    source_files = [
        p for p in app_dir.rglob("*")
        if p.is_file() and p.name not in {"Dockerfile", "README.md", ".dockerignore"}
    ]
    assert source_files, f"{stack} produced no source"

    _, container = _deployment_container(root)
    assert container["image"] == "svc:latest"
    assert container["imagePullPolicy"] == "IfNotPresent"
    assert "nginx" not in container["image"]

    frontend = stack in _STATIC_FRONTENDS or stack in _SSR_FRONTENDS
    if frontend:
        assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    else:
        assert container["livenessProbe"]["httpGet"]["path"] == "/health"
        assert container["readinessProbe"]["httpGet"]["path"] == "/ready"

    # Kind scripts always generated.
    assert (root / "scripts/deploy-kind.sh").is_file()


@pytest.mark.parametrize("stack", _BACKENDS)
def test_backends_expose_health_apis_in_source(tmp_path: Path, stack: str) -> None:
    root = _generate(tmp_path, name="svc", stack=stack, frameworks=[stack])
    source = "\n".join(
        p.read_text(errors="ignore")
        for p in (root / "apps/svc").rglob("*")
        if p.is_file() and p.suffix in {".py", ".js", ".ts", ".go", ".java", ".cs"}
    )
    for route in ("/health", "/ready", "/info", "/api/status"):
        assert route in source, f"{stack} missing {route}"


def test_mariadb_dependency_generates_manifest_and_secret(tmp_path: Path) -> None:
    root = _generate(
        tmp_path,
        name="mdb-svc",
        stack="fastapi",
        frameworks=["fastapi"],
        listen_port=8000,
        dependencies=WorkloadDependenciesConfig(
            mariadb=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
        ),
    )
    assert (root / "infra/k8s/manifests/mariadb-deployment.yaml").is_file()
    mdb = yaml.safe_load((root / "infra/k8s/manifests/mariadb-deployment.yaml").read_text())
    assert mdb["spec"]["template"]["spec"]["containers"][0]["image"].startswith("mariadb")
    secret = yaml.safe_load((root / "infra/k8s/manifests/secret.yaml").read_text())["stringData"]
    assert "MARIADB_URL" in secret
    assert secret["MARIADB_URL"].startswith("mysql://")  # wire-compatible


def test_multi_service_generates_deployment_service_per_workload(tmp_path: Path) -> None:
    """A workspace can host >1 Deployment+Service, each with its own stack /
    service type / selector, and each scaffolds a runnable app image."""
    from app.schemas.cloud import ContainerServiceSpec, ServiceTypeName

    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="multi",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
        container_scaffold=ContainerScaffoldConfig(
            enabled=True,
            generate_docker_compose=True,
            services=[
                ContainerServiceSpec(name="api", stack="fastapi", listen_port=8000,
                                     service_type=ServiceTypeName.CLUSTER_IP, selector="api"),
                ContainerServiceSpec(name="web", stack="react_vite",
                                     service_type=ServiceTypeName.NODE_PORT, selector="web"),
            ],
        ),
    )
    root = Path(gen.generate(request).root_dir)
    mdir = root / "infra/k8s/manifests"

    # Generic nginx fallback must NOT be emitted alongside the per-stack workloads.
    assert not (mdir / "deployment.yaml").exists()
    assert not (mdir / "service.yaml").exists()

    api_dep = yaml.safe_load((mdir / "launch-api-deployment.yaml").read_text())
    api_svc = yaml.safe_load((mdir / "launch-api-service.yaml").read_text())
    web_dep = yaml.safe_load((mdir / "launch-web-deployment.yaml").read_text())
    web_svc = yaml.safe_load((mdir / "launch-web-service.yaml").read_text())

    # k8s resource names are launch-*, but images stay <slug>:latest (built from apps/<slug>/).
    assert api_dep["spec"]["template"]["spec"]["containers"][0]["image"] == "api:latest"
    assert web_dep["spec"]["template"]["spec"]["containers"][0]["image"] == "web:latest"
    assert api_svc["metadata"]["name"] == "launch-api-service"
    assert web_svc["metadata"]["name"] == "launch-web-service"
    assert api_svc["spec"]["type"] == "ClusterIP"
    assert web_svc["spec"]["type"] == "NodePort"
    assert api_svc["spec"]["selector"]["app"] == "api"
    assert web_svc["spec"]["selector"]["app"] == "web"
    assert api_dep["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] == "IfNotPresent"

    # Each core service scaffolds runnable source so the image can be built + kind-loaded.
    assert (root / "apps/api/main.py").is_file()
    assert (root / "apps/web/src/App.jsx").is_file()


def test_multi_service_no_nginx_fallback_and_ingress_routes(tmp_path: Path) -> None:
    """No orphan nginx deployment.yaml; Ingress routes / to web, /api to backend."""
    from app.schemas.cloud import ContainerServiceSpec

    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="shop",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
        kubernetes_options=KubernetesWorkloadOptions(ingress=True),
        container_scaffold=ContainerScaffoldConfig(
            enabled=True,
            services=[
                ContainerServiceSpec(name="web", stack="react_vite", selector="web"),
                ContainerServiceSpec(name="server", stack="fastapi", listen_port=8000, selector="server"),
            ],
        ),
    )
    root = Path(gen.generate(request).root_dir)
    mdir = root / "infra/k8s/manifests"

    assert not (mdir / "deployment.yaml").exists()
    assert not (mdir / "service.yaml").exists()
    assert (mdir / "launch-web-deployment.yaml").is_file()
    assert (mdir / "launch-server-deployment.yaml").is_file()

    # Frontend auto-exposed to preview; backend not.
    web = yaml.safe_load((mdir / "launch-web-deployment.yaml").read_text())
    server = yaml.safe_load((mdir / "launch-server-deployment.yaml").read_text())
    assert web["metadata"]["annotations"]["launchpad.io/preview-target"] == "true"
    assert "annotations" not in server["metadata"]

    # Multi-path ingress.
    ing = yaml.safe_load((mdir / "ingress.yaml").read_text())
    routes = {
        p["path"]: (p["backend"]["service"]["name"], p["backend"]["service"]["port"]["number"])
        for p in ing["spec"]["rules"][0]["http"]["paths"]
    }
    assert routes["/"] == ("launch-web-service", 8080)
    assert routes["/api"] == ("launch-server-service", 8000)


def test_explicit_expose_preview_overrides_default(tmp_path: Path) -> None:
    """expose_preview=True on a backend routes it at / even without a frontend."""
    from app.schemas.cloud import ContainerServiceSpec

    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="apis",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
        container_scaffold=ContainerScaffoldConfig(
            enabled=True,
            services=[
                ContainerServiceSpec(name="gateway", stack="fastapi", listen_port=8000, expose_preview=True),
                ContainerServiceSpec(name="worker", stack="go", listen_port=8080),
            ],
        ),
    )
    root = Path(gen.generate(request).root_dir)
    mdir = root / "infra/k8s/manifests"
    gateway = yaml.safe_load((mdir / "launch-gateway-deployment.yaml").read_text())
    assert gateway["metadata"]["annotations"]["launchpad.io/preview-target"] == "true"
    ing = yaml.safe_load((mdir / "ingress.yaml").read_text())
    routes = {p["path"]: p["backend"]["service"]["name"] for p in ing["spec"]["rules"][0]["http"]["paths"]}
    assert routes["/"] == "launch-gateway-service"


def test_link_repo_skips_default_app_scaffold(tmp_path: Path) -> None:
    """Link/Import keeps container_scaffold enabled but must not invent apps/*."""
    from app.schemas.cloud import GcpCloudConfig, GcpResources

    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="linked-only",
        iac_engine=IaCEngine.PULUMI,
        artifact_mode=WorkspaceArtifactsMode.IAC_ONLY,
        cloud=GcpCloudConfig(
            provider=CloudProvider.GCP,
            resources=GcpResources(project_id="demo"),
        ),
        credentials=CloudCredentials(),
        container_scaffold=ContainerScaffoldConfig(
            enabled=True,
            generate_dockerfile=False,
            generate_docker_compose=False,
            services=[],
            frameworks=[],
        ),
    )
    root = Path(gen.generate(request).root_dir)
    assert (root / "infra/pulumi/Pulumi.yaml").is_file()
    assert not (root / "apps").exists()
    assert not (root / "docker-compose.yml").exists()


def test_scaffold_disabled_uses_placeholder(tmp_path: Path) -> None:
    gen = IaCGenerator(workspace_root=tmp_path)
    request = ProvisioningWizardRequest(
        name="bare-app",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=LocalCloudConfig(resources=LocalResources()),
        credentials=CloudCredentials(),
        kubernetes_packaging=KubernetesPackaging.RAW_MANIFESTS,
    )
    root = Path(gen.generate(request).root_dir)
    _, container = _deployment_container(root)
    assert container["image"] == "app:latest"
    assert not (root / "apps").exists()


def test_fullstack_wires_db_env_and_frontend_api_url(tmp_path: Path) -> None:
    """Multi-stack previews must connect to datastores and surface status:
    every launch-* app gets the DB/Redis secret env + HAS_* flags, and the
    frontend's API_URL points at the backend Service for its status dashboard."""
    root = _generate(
        tmp_path,
        name="fnep",
        stack="nextjs",
        frameworks=["nextjs", "express"],
        dependencies=WorkloadDependenciesConfig(
            postgres=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
            redis=DataStoreDependency(enabled=True, placement=DependencyPlacement.IN_CLUSTER),
        ),
    )
    mdir = root / "infra/k8s/manifests"

    def _env(fname):
        d = yaml.safe_load((mdir / fname).read_text())
        c = d["spec"]["template"]["spec"]["containers"][0]
        env = {e["name"]: e.get("value") for e in c["env"] if "value" in e}
        envfrom = [list(x)[0] for x in c.get("envFrom", [])]
        return env, envfrom

    be_env, be_from = _env("launch-express-deployment.yaml")
    fe_env, fe_from = _env("launch-nextjs-deployment.yaml")

    # Backend can connect: DB env flags + secret with DATABASE_URL/REDIS_URL.
    assert be_env["HAS_DATABASE"] == "true"
    assert be_env["HAS_REDIS"] == "true"
    assert "secretRef" in be_from
    assert fe_from == ["secretRef"]  # frontend also gets the secret

    # Frontend points at the backend Service so its dashboard shows live status.
    svc = yaml.safe_load((mdir / "launch-express-service.yaml").read_text())
    port = svc["spec"]["ports"][0]["port"]
    backend_url = f"http://launch-express-service:{port}"
    assert fe_env["API_URL"] == backend_url
    assert fe_env["BACKEND_URL"] == backend_url
    assert fe_env["NEXT_PUBLIC_API_URL"] == backend_url

    # The backend must NOT advertise a bogus upstream API target - API_URL is a
    # frontend-only wiring that points at a real Service, never "api-server".
    assert "API_URL" not in be_env
    assert "api-server" not in "".join(f"{k}={v}" for k, v in be_env.items())

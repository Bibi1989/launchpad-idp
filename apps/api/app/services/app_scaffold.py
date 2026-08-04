"""Generate runnable mini-applications for scaffolded Kubernetes workloads.

Historically Launchpad emitted Dockerfiles and Kubernetes manifests that
referenced application entrypoints (``uvicorn main:app``, ``node server.js``,
``npm run build``) that were never actually generated, and every Deployment
shipped a generic ``nginx`` placeholder image. This module closes that gap for
the *core* stacks: it generates real, buildable source code — with health
endpoints, a live health dashboard, and database/Redis connectivity checks —
plus a tuned Dockerfile, ``.dockerignore``, build scripts, and Kind
build/load/deploy scripts, so a workspace is immediately runnable on a local
Kind cluster.

The generated application reads the exact environment contract that the
manifests inject (``DATABASE_URL`` / ``MYSQL_URL`` / ``MONGODB_URI`` /
``REDIS_URL`` from the app Secret, and ``ENVIRONMENT_NAME`` / ``APP_VERSION`` /
``POD_NAME`` / ``POD_NAMESPACE`` / ``REPLICA_COUNT`` / ``HAS_DATABASE`` /
``HAS_REDIS`` from the Deployment env). See
``app.services.workload_dependencies.dependency_secret_string_data`` and
``app.services.k8s_bundle._render_workload_env_block``.

Supported core stacks generate a full runnable application:

- Static frontends (Nginx-served SPA): React (Vite), Vue, Svelte, Angular
- SSR frontends (Node server): Next.js, Nuxt
- Backends (HTTP health APIs): FastAPI, Flask, Django, Express, Node, NestJS,
  Go, Spring Boot, .NET
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.schemas.cloud import ProvisioningWizardRequest, WorkloadDependenciesConfig
from app.schemas.dockerfile_schema import ProjectStack
from app.services.dockerfile_scaffold import resolve_scaffold_stacks
from app.services.k8s_bundle import WorkloadImageSpec, _namespace_name

APP_VERSION = "1.0.0"

# Static single-page frontends built to a `dist/` and served by non-root Nginx.
_STATIC_FRONTEND_STACKS: frozenset[ProjectStack] = frozenset(
    {
        ProjectStack.REACT_VITE,
        ProjectStack.VUEJS,
        ProjectStack.SVELTE,
        ProjectStack.ANGULAR,
    }
)

# Server-side-rendered frontends that run a Node process.
_SSR_FRONTEND_STACKS: frozenset[ProjectStack] = frozenset(
    {
        ProjectStack.NEXTJS,
        ProjectStack.NUXTJS,
    }
)

# Backend HTTP services exposing /health, /ready, /info, /api/status, dashboard.
_BACKEND_STACKS: frozenset[ProjectStack] = frozenset(
    {
        ProjectStack.FASTAPI,
        ProjectStack.FLASK,
        ProjectStack.DJANGO,
        ProjectStack.EXPRESS,
        ProjectStack.NODE,
        ProjectStack.NESTJS,
        ProjectStack.GO,
        ProjectStack.SPRINGBOOT,
        ProjectStack.JAVA,
        ProjectStack.DOTNET,
    }
)

# Stacks with a full source generator in this module.
CORE_STACKS: frozenset[ProjectStack] = (
    _STATIC_FRONTEND_STACKS | _SSR_FRONTEND_STACKS | _BACKEND_STACKS
)

_DEFAULT_PORTS: dict[ProjectStack, int] = {
    # static frontends serve on 8080 (nginx-unprivileged) — see resolve_app_port
    ProjectStack.REACT_VITE: 8080,
    ProjectStack.VUEJS: 8080,
    ProjectStack.SVELTE: 8080,
    ProjectStack.ANGULAR: 8080,
    # SSR frontends
    ProjectStack.NEXTJS: 3000,
    ProjectStack.NUXTJS: 3000,
    # backends
    ProjectStack.FASTAPI: 8000,
    ProjectStack.FLASK: 5000,
    ProjectStack.DJANGO: 8000,
    ProjectStack.EXPRESS: 3000,
    ProjectStack.NODE: 3000,
    ProjectStack.NESTJS: 3000,
    ProjectStack.GO: 8080,
    ProjectStack.SPRINGBOOT: 8080,
    ProjectStack.JAVA: 8080,
    ProjectStack.DOTNET: 8080,
}


def _sanitize_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-") or "app"
    return cleaned[:63]


def is_core_stack(stack: ProjectStack) -> bool:
    return stack in CORE_STACKS


def resolve_app_port(stack: ProjectStack, requested: int | None) -> int:
    if stack in _STATIC_FRONTEND_STACKS:
        # nginx-unprivileged serves on 8080 regardless of the wizard value.
        return 8080
    return requested or _DEFAULT_PORTS.get(stack, 8080)


# --------------------------------------------------------------------------- #
# CoreScaffold: the resolved plan for one single-service core application
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CoreScaffold:
    stack: ProjectStack
    app_name: str
    port: int
    dependencies: WorkloadDependenciesConfig

    @property
    def is_static_frontend(self) -> bool:
        return self.stack in _STATIC_FRONTEND_STACKS

    @property
    def is_ssr_frontend(self) -> bool:
        return self.stack in _SSR_FRONTEND_STACKS

    @property
    def is_frontend(self) -> bool:
        return self.is_static_frontend or self.is_ssr_frontend

    @property
    def app_dir(self) -> str:
        return f"apps/{self.app_name}"

    @property
    def image(self) -> str:
        return f"{self.app_name}:latest"

    def image_spec(self) -> WorkloadImageSpec:
        if self.is_static_frontend:
            return WorkloadImageSpec(
                image=self.image,
                image_pull_policy="IfNotPresent",
                container_port=8080,
                liveness_path="/healthz",
                readiness_path="/healthz",
                run_as_user=101,
                read_only_root_fs=False,
                writable_mounts=(),
                app_version=APP_VERSION,
            )
        if self.is_ssr_frontend:
            return WorkloadImageSpec(
                image=self.image,
                image_pull_policy="IfNotPresent",
                container_port=self.port,
                liveness_path="/healthz",
                readiness_path="/healthz",
                run_as_user=10001,
                read_only_root_fs=False,
                writable_mounts=(),
                app_version=APP_VERSION,
            )
        return WorkloadImageSpec(
            image=self.image,
            image_pull_policy="IfNotPresent",
            container_port=self.port,
            liveness_path="/health",
            readiness_path="/ready",
            run_as_user=10001,
            read_only_root_fs=True,
            writable_mounts=(("tmp", "/tmp"),),
            app_version=APP_VERSION,
        )

    def files(self) -> dict[str, str]:
        """Return workspace-relative path -> content for the application source."""
        builder = _STACK_BUILDERS.get(self.stack)
        if builder is None:  # pragma: no cover - guarded by resolve_core_scaffold
            raw: dict[str, str] = {}
        else:
            raw = builder(self.app_name, self.port, self.dependencies)
        return {f"{self.app_dir}/{rel}": content for rel, content in raw.items()}

    def build_script(self) -> tuple[str, str]:
        """Return (relative path, content) for the plain docker build script."""
        content = _BUILD_SCRIPT.replace("__IMAGE__", self.image).replace(
            "__CONTEXT__", self.app_dir
        )
        return "scripts/build-image.sh", content

    def kind_scripts(self, *, cluster_name: str, namespace: str) -> dict[str, str]:
        """Return Kind build/load/deploy scripts keyed by workspace-relative path."""
        subs = {
            "__IMAGE__": self.image,
            "__CONTEXT__": self.app_dir,
            "__CLUSTER__": cluster_name,
            "__NAMESPACE__": namespace,
            "__PORT__": str(self.image_spec().container_port),
        }
        load = _KIND_LOAD_SCRIPT
        deploy = _KIND_DEPLOY_SCRIPT
        for token, value in subs.items():
            load = load.replace(token, value)
            deploy = deploy.replace(token, value)
        return {
            "scripts/kind-load.sh": load,
            "scripts/deploy-kind.sh": deploy,
        }


def resolve_core_scaffold(request: ProvisioningWizardRequest) -> CoreScaffold | None:
    """Return a CoreScaffold when the request selects a single core stack.

    Returns None for disabled scaffolding, explicit multi-service specs,
    multi-framework (fullstack) selections, and non-core single stacks — those
    keep the legacy Dockerfile-only behavior and the Nginx placeholder image.
    """
    cfg = request.container_scaffold
    if not cfg.enabled or cfg.services:
        return None
    stacks = resolve_scaffold_stacks(stack=cfg.stack, frameworks=cfg.frameworks)
    if len(stacks) != 1:
        return None
    stack = stacks[0]
    if not is_core_stack(stack):
        return None
    app_name = _sanitize_name(cfg.app_name or request.name)
    port = resolve_app_port(stack, cfg.listen_port)
    return CoreScaffold(
        stack=stack,
        app_name=app_name,
        port=port,
        dependencies=request.dependencies,
    )


def workload_image_spec_for_request(
    request: ProvisioningWizardRequest,
) -> WorkloadImageSpec | None:
    scaffold = resolve_core_scaffold(request)
    return scaffold.image_spec() if scaffold else None


# --------------------------------------------------------------------------- #
# Shared build / Kind scripts
# --------------------------------------------------------------------------- #

_BUILD_SCRIPT = """\
#!/usr/bin/env bash
# Build the application container image locally.
set -euo pipefail
IMAGE="${IMAGE:-__IMAGE__}"
CONTEXT="__CONTEXT__"
echo "==> docker build -t ${IMAGE} ${CONTEXT}"
docker build -t "${IMAGE}" "${CONTEXT}"
echo "==> Built ${IMAGE}"
"""

_KIND_LOAD_SCRIPT = """\
#!/usr/bin/env bash
# Load the locally-built image into the Kind cluster (no registry required).
set -euo pipefail
IMAGE="${IMAGE:-__IMAGE__}"
CLUSTER="${KIND_CLUSTER:-__CLUSTER__}"
echo "==> kind load docker-image ${IMAGE} --name ${CLUSTER}"
kind load docker-image "${IMAGE}" --name "${CLUSTER}"
echo "==> Loaded ${IMAGE} into kind cluster ${CLUSTER}"
"""

_KIND_DEPLOY_SCRIPT = """\
#!/usr/bin/env bash
# One-shot: build -> load into Kind -> apply manifests -> wait for rollout.
# Requires: docker, kind, kubectl. No remote container registry needed.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${IMAGE:-__IMAGE__}"
CLUSTER="${KIND_CLUSTER:-__CLUSTER__}"
CONTEXT="__CONTEXT__"
NAMESPACE="__NAMESPACE__"

echo "==> [1/4] Building image ${IMAGE}"
docker build -t "${IMAGE}" "${CONTEXT}"

echo "==> [2/4] Loading image into kind cluster ${CLUSTER}"
kind load docker-image "${IMAGE}" --name "${CLUSTER}"

echo "==> [3/4] Applying manifests"
kubectl apply -f infra/k8s/manifests/ -R

echo "==> [4/4] Waiting for all deployments to become Available"
# Namespace-wide: waits for every generated Deployment (launch-web, launch-server,
# postgres, …) rather than a hardcoded deployment/app.
kubectl -n "${NAMESPACE}" wait --for=condition=Available --timeout=180s deployment --all

echo ""
echo "==> Done. Deployments + Services:"
kubectl -n "${NAMESPACE}" get deploy,svc
echo "==> Port-forward a service, e.g.:"
echo "    kubectl -n ${NAMESPACE} port-forward svc/app 8080:80"
"""


# --------------------------------------------------------------------------- #
# Shared backend health dashboard (served by FastAPI and Node/Express)
# --------------------------------------------------------------------------- #

_BACKEND_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Launchpad Service Dashboard</title>
<style>
:root { color-scheme: light dark; --bg: #0b1120; --card: #111a2e; --card-border: #1e293b; --text: #f1f5f9; --text-muted: #94a3b8; --accent: #0ea5e9; --inset: #090e17; }
* { box-sizing: border-box; }
body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: var(--bg); color: var(--text); }
.wrap { max-width: 1024px; margin: 0 auto; padding: 40px 24px 64px; }
.breadcrumbs { font-size: 11px; font-weight: 600; letter-spacing: 0.1em; color: var(--text-muted); margin-bottom: 32px; display: flex; align-items: center; gap: 8px; }
.breadcrumbs .active { color: var(--accent); }
.header-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 40px; border-bottom: 1px solid var(--card-border); padding-bottom: 24px; }
h1 { font-size: 28px; margin: 0 0 8px 0; font-weight: 600; letter-spacing: -0.02em; }
.sub { color: var(--text-muted); font-size: 14px; margin: 0 0 12px 0; }
.target-line { font-size: 13px; color: var(--text-muted); margin: 0; }
code { background: var(--inset); padding: 4px 8px; border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; color: var(--accent); border: 1px solid var(--card-border); }
.status-bar { display: flex; flex-direction: column; align-items: flex-end; gap: 12px; }
.auto-refresh { font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: var(--text-muted); display: flex; align-items: center; gap: 6px; }
.btn { background: var(--card); border: 1px solid var(--card-border); color: var(--text); padding: 8px 16px; border-radius: 8px; font-size: 13px; font-weight: 500; cursor: pointer; display: flex; align-items: center; gap: 8px; transition: all 0.2s; }
.btn:hover { background: var(--card-border); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #64748b; display: inline-block; }
.dot.up { background: #22c55e; box-shadow: 0 0 0 3px rgba(34,197,94,.18); }
.dot.down, .dot.err { background: #ef4444; box-shadow: 0 0 0 3px rgba(239,68,68,.18); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; }
.card { background: var(--card); border: 1px solid var(--card-border); border-radius: 12px; padding: 0; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -2px rgba(0,0,0,0.1); }
.card-header { padding: 20px 24px; border-bottom: 1px solid var(--card-border); display: flex; justify-content: space-between; align-items: flex-start; background: rgba(15,23,42,0.5); }
.card-header-left p { margin: 0 0 4px 0; font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: var(--text-muted); }
.card-header-left h3 { margin: 0; font-size: 16px; font-weight: 600; color: var(--text); }
.pill { font-size: 12px; font-weight: 500; padding: 4px 10px; border-radius: 999px; display: inline-flex; align-items: center; gap: 6px; }
.pill.up { background: rgba(34,197,94,.1); color: #4ade80; border: 1px solid rgba(34,197,94,.2); }
.pill.err, .pill.down { background: rgba(239,68,68,.1); color: #f87171; border: 1px solid rgba(239,68,68,.2); }
.pill.na { background: rgba(148,163,184,.1); color: #cbd5e1; border: 1px solid rgba(148,163,184,.2); }
.field { padding: 16px 24px; border-bottom: 1px solid var(--card-border); display: flex; justify-content: space-between; align-items: center; }
.field-label { font-size: 11px; font-weight: 600; letter-spacing: 0.05em; color: var(--text-muted); }
.field-val { font-size: 14px; font-weight: 500; }
.flex-row { display: flex; padding: 16px 24px; gap: 16px; border-bottom: 1px solid var(--card-border); }
.inset-box { background: var(--inset); border: 1px solid var(--card-border); border-radius: 8px; padding: 12px 16px; flex: 1; }
.inset-box-label { font-size: 10px; font-weight: 600; letter-spacing: 0.08em; color: var(--text-muted); margin-bottom: 4px; }
.inset-box-val { font-size: 16px; font-weight: 500; }
.text-block { padding: 24px; font-size: 13px; color: var(--text-muted); line-height: 1.5; border-bottom: 1px solid var(--card-border); flex-grow: 1; }
.card-footer { padding: 16px 24px; background: rgba(15,23,42,0.3); margin-top: auto; }
.card-link { font-size: 12px; font-weight: 600; letter-spacing: 0.05em; color: var(--accent); cursor: pointer; text-decoration: none; }
.card-link:hover { text-decoration: underline; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
</style>
</head>
<body>
<div class="wrap">
  <div class="breadcrumbs">
    <span>INFRASTRUCTURE</span> &rsaquo; <span class="active">BACKEND STATUS DASHBOARD</span>
  </div>
  <div class="header-top">
    <div>
      <h1 id="appname">Launchpad Service</h1>
      <p class="sub">Live health dashboard &middot; endpoints: <a href="/health">/health</a> &middot; <a href="/ready">/ready</a> &middot; <a href="/info">/info</a> &middot; <a href="/api/status">/api/status</a></p>
      <p class="target-line">Target: <code id="target-url"></code></p>
    </div>
    <div class="status-bar">
      <div class="auto-refresh">
        <span id="appdot" class="dot up"></span> AUTO-REFRESHING EVERY 5S
      </div>
      <button class="btn" onclick="refresh()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
        Refresh Status
      </button>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="card-header">
        <div class="card-header-left">
          <p>APPLICATION</p>
          <h3>BACKEND SERVICE</h3>
        </div>
        <div id="app-status" class="pill up">
          <span class="dot"></span> Healthy
        </div>
      </div>
      <div class="field">
        <span class="field-label">VERSION</span>
        <div class="field-val" id="app-version">—</div>
      </div>
      <div class="field">
        <span class="field-label">UPTIME</span>
        <div class="field-val" id="app-uptime">—</div>
      </div>
      <div class="card-footer">
        <span class="card-link">VIEW LOGS</span>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <div class="card-header-left">
          <p>DEPLOYMENT</p>
          <h3>METADATA</h3>
        </div>
        <div class="pill up">
          <span class="dot"></span> Active
        </div>
      </div>
      <div class="field">
        <span class="field-label">NAMESPACE</span>
        <div class="field-val" id="k8s-ns">—</div>
      </div>
      <div class="flex-row">
        <div class="inset-box">
          <div class="inset-box-label">POD</div>
          <div class="inset-box-val" id="k8s-pod" style="font-size:12px;word-break:break-all">—</div>
        </div>
        <div class="inset-box">
          <div class="inset-box-label">REPLICAS</div>
          <div class="inset-box-val" id="k8s-replicas">—</div>
        </div>
      </div>
      <div class="card-footer">
        <span class="card-link">INSPECT POD</span>
      </div>
    </div>
    <div class="card" id="db-card">
      <div class="card-header">
        <div class="card-header-left">
          <p>DATABASE</p>
          <h3 id="db-kind">POSTGRESQL</h3>
        </div>
        <div id="db-pill" class="pill na">
          <span class="dot"></span> Configured
        </div>
      </div>
      <div class="field">
        <span class="field-label">LAST SUCCESS</span>
        <div class="field-val" id="db-last">—</div>
      </div>
      <div class="text-block" id="db-err" style="color:#f87171">
      </div>
      <div class="card-footer">
        <span class="card-link">CREDENTIALS</span>
      </div>
    </div>
    <div class="card" id="redis-card">
      <div class="card-header">
        <div class="card-header-left">
          <p>CACHE</p>
          <h3>REDIS CACHE</h3>
        </div>
        <div id="redis-pill" class="pill na">
          <span class="dot"></span> Configured
        </div>
      </div>
      <div class="flex-row">
        <div class="inset-box">
          <div class="inset-box-label">LATENCY</div>
          <div class="inset-box-val" id="redis-latency">—</div>
        </div>
        <div class="inset-box">
          <div class="inset-box-label">LAST SUCCESS</div>
          <div class="inset-box-val" id="redis-last" style="font-size:12px;word-break:break-all">—</div>
        </div>
      </div>
      <div class="text-block" id="redis-err" style="color:#f87171">
      </div>
      <div class="card-footer">
        <span class="card-link">METRICS</span>
      </div>
    </div>
  </div>
</div>
<script>
document.getElementById("target-url").textContent = window.location.origin;
function fmtDep(pillId, errId, dep) {
  var pill = document.getElementById(pillId);
  var err = document.getElementById(errId);
  err.textContent = "";
  if (!dep || !dep.configured) { pill.className = "pill na"; pill.innerHTML = '<span class="dot"></span> Not configured'; return; }
  if (dep.connected) { pill.className = "pill up"; pill.innerHTML = '<span class="dot"></span> Connected'; }
  else {
    pill.className = "pill down"; pill.innerHTML = '<span class="dot"></span> Disconnected';
    if (dep.error) err.textContent = dep.error;
  }
}
async function refresh() {
  try {
    var res = await fetch("/api/status", { cache: "no-store" });
    var s = await res.json();
    document.getElementById("appname").textContent = s.app.name;
    document.title = s.app.name + " — Dashboard";
    var appDot = document.getElementById("appdot");
    var appStatus = document.getElementById("app-status");
    var up = s.app.status === "healthy";
    appDot.className = "dot " + (up ? "up" : "down");
    appStatus.className = "pill " + (up ? "up" : "down");
    appStatus.innerHTML = '<span class="dot"></span> ' + (up ? "Healthy" : "Unhealthy");
    document.getElementById("app-version").textContent = s.app.version;
    document.getElementById("app-uptime").textContent = s.app.uptimeSeconds + "s";
    document.getElementById("k8s-ns").textContent = s.kubernetes.namespace;
    document.getElementById("k8s-pod").textContent = s.kubernetes.pod;
    document.getElementById("k8s-replicas").textContent = s.kubernetes.replicas;
    fmtDep("db-pill", "db-err", s.database);
    document.getElementById("db-kind").textContent = (s.database.kind || "database").toUpperCase();
    document.getElementById("db-last").textContent = s.database.lastSuccess || "never";
    fmtDep("redis-pill", "redis-err", s.redis);
    document.getElementById("redis-latency").textContent =
      (s.redis.latencyMs != null) ? (s.redis.latencyMs + " ms") : "—";
    document.getElementById("redis-last").textContent = s.redis.lastSuccess || "never";
    document.getElementById("db-card").style.display = s.database.configured ? "flex" : "none";
    document.getElementById("redis-card").style.display = s.redis.configured ? "flex" : "none";
  } catch (e) {
    document.getElementById("appdot").className = "dot down";
  }
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# FastAPI application
# --------------------------------------------------------------------------- #

_FASTAPI_MAIN = r'''"""Launchpad-generated FastAPI service with health + dependency checks."""

import os
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

APP_NAME = os.environ.get("ENVIRONMENT_NAME", "launchpad-app")
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
NAMESPACE = os.environ.get("POD_NAMESPACE", "default")
POD_NAME = os.environ.get("POD_NAME", socket.gethostname())
REPLICA_COUNT = os.environ.get("REPLICA_COUNT", "1")
PORT = int(os.environ.get("PORT", "8000"))
DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or os.environ.get("MYSQL_URL")
    or os.environ.get("MONGODB_URI")
)
REDIS_URL = os.environ.get("REDIS_URL")
STARTED_AT = time.time()

_last_db_success = None
_last_redis_success = None

DASHBOARD_HTML = __DASHBOARD__

app = FastAPI(title=APP_NAME + " — Launchpad service", version=APP_VERSION)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def check_database():
    global _last_db_success
    url = DATABASE_URL
    if not url:
        return {"configured": False, "connected": False, "error": None,
                "kind": None, "lastSuccess": _last_db_success}
    try:
        if url.startswith("postgres"):
            import psycopg

            with psycopg.connect(url, connect_timeout=3) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            kind = "postgresql"
        elif url.startswith("mysql"):
            import pymysql

            parsed = urlparse(url)
            conn = pymysql.connect(
                host=parsed.hostname,
                port=parsed.port or 3306,
                user=parsed.username,
                password=parsed.password,
                database=(parsed.path or "/").lstrip("/") or None,
                connect_timeout=3,
            )
            conn.ping(reconnect=False)
            conn.close()
            kind = "mysql"
        elif url.startswith("mongodb"):
            from pymongo import MongoClient

            client = MongoClient(url, serverSelectionTimeoutMS=3000)
            client.admin.command("ping")
            client.close()
            kind = "mongodb"
        else:
            return {"configured": True, "connected": False,
                    "error": "unsupported database scheme", "kind": None,
                    "lastSuccess": _last_db_success}
        _last_db_success = _now_iso()
        return {"configured": True, "connected": True, "error": None,
                "kind": kind, "lastSuccess": _last_db_success}
    except Exception as exc:  # noqa: BLE001 - surfaced to the dashboard
        return {"configured": True, "connected": False, "error": str(exc)[:200],
                "kind": None, "lastSuccess": _last_db_success}


def check_redis():
    global _last_redis_success
    if not REDIS_URL:
        return {"configured": False, "connected": False, "error": None,
                "latencyMs": None, "lastSuccess": _last_redis_success}
    try:
        import redis

        client = redis.from_url(REDIS_URL, socket_connect_timeout=3, socket_timeout=3)
        start = time.perf_counter()
        client.ping()
        latency = round((time.perf_counter() - start) * 1000, 2)
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
        _last_redis_success = _now_iso()
        return {"configured": True, "connected": True, "error": None,
                "latencyMs": latency, "lastSuccess": _last_redis_success}
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "connected": False, "error": str(exc)[:200],
                "latencyMs": None, "lastSuccess": _last_redis_success}


def build_status():
    database = check_database()
    redis_status = check_redis()
    return {
        "app": {
            "name": APP_NAME,
            "version": APP_VERSION,
            "status": "healthy",
            "uptimeSeconds": round(time.time() - STARTED_AT, 1),
        },
        "kubernetes": {
            "namespace": NAMESPACE,
            "pod": POD_NAME,
            "replicas": REPLICA_COUNT,
            "deployment": "app",
        },
        "database": database,
        "redis": redis_status,
        "timestamp": _now_iso(),
    }


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": _now_iso()}


@app.get("/ready")
def ready():
    status = build_status()
    problems = []
    if status["database"]["configured"] and not status["database"]["connected"]:
        problems.append("database")
    if status["redis"]["configured"] and not status["redis"]["connected"]:
        problems.append("redis")
    payload = {
        "status": "ready" if not problems else "degraded",
        "problems": problems,
        "timestamp": _now_iso(),
    }
    return JSONResponse(payload, status_code=503 if problems else 200)


@app.get("/info")
def info():
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "namespace": NAMESPACE,
        "pod": POD_NAME,
        "replicas": REPLICA_COUNT,
        "port": PORT,
        "dependencies": {
            "database": bool(DATABASE_URL),
            "redis": bool(REDIS_URL),
        },
    }


@app.get("/api/status")
def api_status():
    return build_status()


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(DASHBOARD_HTML)
'''


def _fastapi_requirements(deps: WorkloadDependenciesConfig) -> str:
    lines = ["fastapi==0.115.6", "uvicorn[standard]==0.34.0"]
    if deps.postgres.enabled:
        lines.append("psycopg[binary]==3.2.3")
    if deps.mysql.enabled:
        lines.append("PyMySQL==1.1.1")
    if deps.mongodb.enabled:
        lines.append("pymongo==4.10.1")
    if deps.redis.enabled:
        lines.append("redis==5.2.1")
    return "\n".join(lines) + "\n"


_FASTAPI_DOCKERFILE = """\
# syntax=docker/dockerfile:1.7
# Launchpad-generated FastAPI image (multi-stage, non-root USER 10001).
FROM python:3.12-slim AS builder
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M -s /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=10001:10001 . .
ENV PATH="/opt/venv/bin:$PATH" \\
    PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PORT=__PORT__
USER 10001:10001
EXPOSE __PORT__
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:__PORT__/health'); sys.exit(0)" || exit 1
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
"""

_PY_DOCKERIGNORE = """\
__pycache__/
*.pyc
*.pyo
.venv/
venv/
.env
.git/
.gitignore
*.md
.pytest_cache/
"""


def _fastapi_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    main_py = _FASTAPI_MAIN.replace("__DASHBOARD__", repr(_BACKEND_DASHBOARD_HTML))
    return {
        "main.py": main_py,
        "requirements.txt": _fastapi_requirements(deps),
        "Dockerfile": _FASTAPI_DOCKERFILE.replace("__PORT__", str(port)),
        ".dockerignore": _PY_DOCKERIGNORE,
        "README.md": _app_readme(app_name, "FastAPI", port, deps),
    }


# --------------------------------------------------------------------------- #
# Node / Express application
# --------------------------------------------------------------------------- #

_NODE_SERVER = r'''// Launchpad-generated Node.js/Express service with health + dependency checks.
'use strict';

const express = require('express');

const APP_NAME = process.env.ENVIRONMENT_NAME || 'launchpad-app';
const APP_VERSION = process.env.APP_VERSION || '1.0.0';
const NAMESPACE = process.env.POD_NAMESPACE || 'default';
const POD_NAME = process.env.POD_NAME || require('os').hostname();
const REPLICA_COUNT = process.env.REPLICA_COUNT || '1';
const PORT = parseInt(process.env.PORT || '3000', 10);
const DATABASE_URL =
  process.env.DATABASE_URL || process.env.MYSQL_URL || process.env.MONGODB_URI || null;
const REDIS_URL = process.env.REDIS_URL || null;
const STARTED_AT = Date.now();

let lastDbSuccess = null;
let lastRedisSuccess = null;

const DASHBOARD_HTML = __DASHBOARD__;

function nowIso() {
  return new Date().toISOString();
}

async function checkDatabase() {
  const url = DATABASE_URL;
  if (!url) {
    return { configured: false, connected: false, error: null, kind: null, lastSuccess: lastDbSuccess };
  }
  try {
    if (url.startsWith('postgres')) {
      const { Client } = require('pg');
      const client = new Client({ connectionString: url, connectionTimeoutMillis: 3000 });
      await client.connect();
      await client.query('SELECT 1');
      await client.end();
      lastDbSuccess = nowIso();
      return { configured: true, connected: true, error: null, kind: 'postgresql', lastSuccess: lastDbSuccess };
    } else if (url.startsWith('mysql')) {
      const mysql = require('mysql2/promise');
      const conn = await mysql.createConnection(url);
      await conn.query('SELECT 1');
      await conn.end();
      lastDbSuccess = nowIso();
      return { configured: true, connected: true, error: null, kind: 'mysql', lastSuccess: lastDbSuccess };
    } else if (url.startsWith('mongodb')) {
      const { MongoClient } = require('mongodb');
      const client = new MongoClient(url, { serverSelectionTimeoutMS: 3000 });
      await client.connect();
      await client.db().command({ ping: 1 });
      await client.close();
      lastDbSuccess = nowIso();
      return { configured: true, connected: true, error: null, kind: 'mongodb', lastSuccess: lastDbSuccess };
    }
    return { configured: true, connected: false, error: 'unsupported database scheme', kind: null, lastSuccess: lastDbSuccess };
  } catch (err) {
    return { configured: true, connected: false, error: String(err.message || err).slice(0, 200), kind: null, lastSuccess: lastDbSuccess };
  }
}

async function checkRedis() {
  if (!REDIS_URL) {
    return { configured: false, connected: false, error: null, latencyMs: null, lastSuccess: lastRedisSuccess };
  }
  let client;
  try {
    const redis = require('redis');
    client = redis.createClient({ url: REDIS_URL, socket: { connectTimeout: 3000 } });
    client.on('error', () => {});
    await client.connect();
    const start = process.hrtime.bigint();
    await client.ping();
    const latency = Number(process.hrtime.bigint() - start) / 1e6;
    await client.quit();
    lastRedisSuccess = nowIso();
    return { configured: true, connected: true, error: null, latencyMs: Math.round(latency * 100) / 100, lastSuccess: lastRedisSuccess };
  } catch (err) {
    try { if (client) await client.disconnect(); } catch (e) { /* ignore */ }
    return { configured: true, connected: false, error: String(err.message || err).slice(0, 200), latencyMs: null, lastSuccess: lastRedisSuccess };
  }
}

async function buildStatus() {
  const [database, redisStatus] = await Promise.all([checkDatabase(), checkRedis()]);
  return {
    app: {
      name: APP_NAME,
      version: APP_VERSION,
      status: 'healthy',
      uptimeSeconds: Math.round((Date.now() - STARTED_AT) / 100) / 10,
    },
    kubernetes: { namespace: NAMESPACE, pod: POD_NAME, replicas: REPLICA_COUNT, deployment: 'app' },
    database,
    redis: redisStatus,
    timestamp: nowIso(),
  };
}

const app = express();

app.get('/health', (req, res) => res.json({ status: 'ok', timestamp: nowIso() }));

app.get('/ready', async (req, res) => {
  const status = await buildStatus();
  const problems = [];
  if (status.database.configured && !status.database.connected) problems.push('database');
  if (status.redis.configured && !status.redis.connected) problems.push('redis');
  res.status(problems.length ? 503 : 200).json({
    status: problems.length ? 'degraded' : 'ready',
    problems,
    timestamp: nowIso(),
  });
});

app.get('/info', (req, res) => res.json({
  name: APP_NAME,
  version: APP_VERSION,
  namespace: NAMESPACE,
  pod: POD_NAME,
  replicas: REPLICA_COUNT,
  port: PORT,
  dependencies: { database: !!DATABASE_URL, redis: !!REDIS_URL },
}));

app.get('/api/status', async (req, res) => res.json(await buildStatus()));

app.get('/', (req, res) => res.type('html').send(DASHBOARD_HTML));

app.listen(PORT, '0.0.0.0', () => {
  console.log(`${APP_NAME} listening on :${PORT}`);
});
'''


def _node_package_json(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> str:
    dependencies = {"express": "^4.21.2"}
    if deps.postgres.enabled:
        dependencies["pg"] = "^8.13.1"
    if deps.mysql.enabled:
        dependencies["mysql2"] = "^3.11.5"
    if deps.mongodb.enabled:
        dependencies["mongodb"] = "^6.12.0"
    if deps.redis.enabled:
        dependencies["redis"] = "^4.7.0"
    import json

    payload = {
        "name": app_name,
        "version": APP_VERSION,
        "private": True,
        "description": "Launchpad-generated Node.js/Express service",
        "main": "server.js",
        "scripts": {"start": "node server.js"},
        "engines": {"node": ">=20"},
        "dependencies": dependencies,
    }
    return json.dumps(payload, indent=2) + "\n"


_NODE_DOCKERFILE = """\
# syntax=docker/dockerfile:1.7
# Launchpad-generated Node.js/Express image (multi-stage, non-root USER 10001).
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --omit=dev --no-audit --no-fund

FROM node:22-alpine AS runtime
WORKDIR /app
RUN apk add --no-cache wget \\
  && addgroup -g 10001 -S app && adduser -u 10001 -S -G app app
COPY --from=deps --chown=10001:10001 /app/node_modules ./node_modules
COPY --chown=10001:10001 . .
ENV NODE_ENV=production PORT=__PORT__
USER 10001:10001
EXPOSE __PORT__
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:__PORT__/health || exit 1
CMD ["node", "server.js"]
"""

_NODE_DOCKERIGNORE = """\
node_modules/
npm-debug.log
.env
.git/
.gitignore
*.md
dist/
coverage/
"""


def _node_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    import json

    server_js = _NODE_SERVER.replace("__DASHBOARD__", json.dumps(_BACKEND_DASHBOARD_HTML))
    return {
        "server.js": server_js,
        "package.json": _node_package_json(app_name, port, deps),
        "Dockerfile": _NODE_DOCKERFILE.replace("__PORT__", str(port)),
        ".dockerignore": _NODE_DOCKERIGNORE,
        "README.md": _app_readme(app_name, "Node.js/Express", port, deps),
    }


# --------------------------------------------------------------------------- #
# React (Vite) application
# --------------------------------------------------------------------------- #

_REACT_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Launchpad Frontend</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""

_REACT_MAIN_JSX = """\
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import './styles.css';

createRoot(document.getElementById('root')).render(<App />);
"""

_REACT_APP_JSX = r'''import React, { useEffect, useState } from 'react';

function Badge({ ok, labelOn, labelOff, labelNa, na }) {
  if (na) return <span className="pill na">{labelNa}</span>;
  return <span className={ok ? 'pill up' : 'pill down'}>{ok ? labelOn : labelOff}</span>;
}

export default function App() {
  const [config, setConfig] = useState(null);
  const [healthy, setHealthy] = useState(true);
  const [updated, setUpdated] = useState('');

  async function refresh() {
    try {
      const res = await fetch('/config.json', { cache: 'no-store' });
      setConfig(await res.json());
    } catch (e) {
      setConfig((c) => c || {});
    }
    try {
      const h = await fetch('/healthz', { cache: 'no-store' });
      setHealthy(h.ok);
    } catch (e) {
      setHealthy(false);
    }
    setUpdated(new Date().toLocaleTimeString());
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  const c = config || {};
  return (
    <div className="wrap">
      <div className="breadcrumbs">
        <span>INFRASTRUCTURE</span> &rsaquo; <span className="active">SPA STATUS DASHBOARD</span>
      </div>
      <div className="header-top">
        <div>
          <h1>Frontend Health</h1>
          <p className="sub">Real-time monitoring of single-page application and environment.</p>
          <p className="target-line">Target: <code>{window.location.origin}</code></p>
        </div>
        <div className="status-bar">
          <div className="auto-refresh">
            <span className="dot up" /> AUTO-REFRESHING EVERY 5S
          </div>
          <button className="btn" onClick={refresh}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
            Refresh Status
          </button>
        </div>
      </div>
      <div className="grid">
        <div className="card">
          <div className="card-header">
            <div className="card-header-left">
              <p>APPLICATION</p>
              <h3>FRONTEND SPA</h3>
            </div>
            <div className={'pill ' + (healthy ? 'up' : 'err')}>
              <span className="dot" /> {healthy ? 'Available' : 'Unavailable'}
            </div>
          </div>
          <div className="field">
            <span className="field-label">VERSION</span>
            <div className="field-val">{c.version || '—'}</div>
          </div>
          <div className="card-footer">
            <span className="card-link">VIEW LOGS</span>
          </div>
        </div>
        <div className="card">
          <div className="card-header">
            <div className="card-header-left">
              <p>DEPLOYMENT</p>
              <h3>METADATA</h3>
            </div>
            <div className="pill up">
              <span className="dot" /> Active
            </div>
          </div>
          <div className="field">
            <span className="field-label">NAMESPACE</span>
            <div className="field-val">{c.namespace || '—'}</div>
          </div>
          <div className="flex-row">
            <div className="inset-box">
              <div className="inset-box-label">POD</div>
              <div className="inset-box-val">{c.pod || '—'}</div>
            </div>
            <div className="inset-box">
              <div className="inset-box-label">REPLICAS</div>
              <div className="inset-box-val">{c.replicas || '—'}</div>
            </div>
          </div>
          <div className="card-footer">
            <span className="card-link">INSPECT POD</span>
          </div>
        </div>
        {c.hasDatabase && (
          <div className="card">
            <div className="card-header">
              <div className="card-header-left">
                <p>DATABASE</p>
                <h3>POSTGRESQL</h3>
              </div>
              <div className="pill na">
                <span className="dot" /> Configured
              </div>
            </div>
            <div className="text-block">
              Live connection status is reported by the backend service.
            </div>
            <div className="card-footer">
              <span className="card-link">CREDENTIALS</span>
            </div>
          </div>
        )}
        {c.hasRedis && (
          <div className="card">
            <div className="card-header">
              <div className="card-header-left">
                <p>CACHE</p>
                <h3>REDIS CACHE</h3>
              </div>
              <div className="pill na">
                <span className="dot" /> Configured
              </div>
            </div>
            <div className="text-block">
              Live ping status is reported by the backend service.
            </div>
            <div className="card-footer">
              <span className="card-link">METRICS</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
'''

_REACT_STYLES = """\
:root { color-scheme: light dark; --bg: #0b1120; --card: #111a2e; --card-border: #1e293b; --text: #f1f5f9; --text-muted: #94a3b8; --accent: #0ea5e9; --inset: #090e17; }
* { box-sizing: border-box; }
body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: var(--bg); color: var(--text); }
.wrap { max-width: 1024px; margin: 0 auto; padding: 40px 24px 64px; }
header { display: flex; align-items: center; gap: 14px; }
h1 { font-size: 22px; margin: 0; }
.sub { color: #94a3b8; font-size: 13px; margin: 6px 0 24px; }
code { background: #1e293b; padding: 2px 6px; border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 0.9em; color: var(--accent); }
.dot { width: 14px; height: 14px; border-radius: 50%; background: #64748b; }
.dot.up { background: #22c55e; box-shadow: 0 0 0 4px rgba(34,197,94,.18); }
.dot.down { background: #ef4444; box-shadow: 0 0 0 4px rgba(239,68,68,.18); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 24px; margin-top: 24px; }
.card { background: var(--card); border: 1px solid var(--card-border); border-radius: 12px; padding: 24px; display: flex; flex-direction: column; }
.card h3 { margin: 0 0 10px; font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: #94a3b8; }
.row { display: flex; justify-content: space-between; gap: 10px; padding: 4px 0; font-size: 14px; }
.row.full { display: block; color: #94a3b8; font-size: 12px; }
.row .k { color: #94a3b8; }
.row .v { font-weight: 600; text-align: right; }
.pill { font-size: 12px; padding: 2px 8px; border-radius: 999px; }
.pill.up { background: rgba(34,197,94,.15); color: #4ade80; }
.pill.down { background: rgba(239,68,68,.15); color: #f87171; }
.pill.na { background: rgba(100,116,139,.15); color: #cbd5e1; }
footer { margin-top: 26px; color: #64748b; font-size: 12px; }

/* New Dashboard Styles */
.breadcrumbs { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
.breadcrumbs span.active { color: var(--accent); }
.header-top { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 24px; }
.header-top h1 { font-size: 32px; font-weight: 700; margin: 0 0 16px 0; letter-spacing: -0.02em; }
.header-top .sub { color: var(--text-muted); font-size: 15px; margin: 0 0 8px 0; }
.target-line { font-size: 15px; color: var(--text-muted); margin: 0 0 32px 0; }
.status-bar { display: flex; align-items: center; gap: 16px; margin-top: 12px; }
.auto-refresh { display: flex; align-items: center; gap: 8px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); }
.auto-refresh .dot { width: 8px; height: 8px; background: #10b981; box-shadow: none; }
.btn { background: transparent; border: 1px solid var(--card-border); color: var(--text); padding: 8px 16px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; display: flex; align-items: center; gap: 8px; transition: all 0.2s; }
.btn:hover { background: var(--card-border); }
.card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 24px; }
.card-header-left p { margin: 0 0 4px 0; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-weight: 600; }
.card-header-left h3 { margin: 0; font-size: 18px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.02em; color: #fff; line-height: 1.3; }
.card-header .pill { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 500; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--card-border); }
.card-header .pill .dot { width: 6px; height: 6px; box-shadow: none; }
.card-header .pill.up { color: #10b981; border-color: rgba(16, 185, 129, 0.2); background: rgba(16, 185, 129, 0.05); }
.card-header .pill.up .dot { background: #10b981; }
.card-header .pill.down { color: #f59e0b; border-color: rgba(245, 158, 11, 0.2); background: rgba(245, 158, 11, 0.05); }
.card-header .pill.down .dot { background: #f59e0b; }
.card-header .pill.err { color: #ef4444; border-color: rgba(239, 68, 68, 0.2); background: rgba(239, 68, 68, 0.05); }
.card-header .pill.err .dot { background: #ef4444; }
.card-header .pill.na { color: #94a3b8; border-color: rgba(148, 163, 184, 0.2); background: rgba(148, 163, 184, 0.05); }
.card-header .pill.na .dot { background: #94a3b8; }
.field { margin-bottom: 16px; }
.field-label { display: block; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-weight: 600; margin-bottom: 8px; }
.field-val { background: var(--inset); border: 1px solid var(--card-border); border-radius: 6px; padding: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 13px; color: var(--text); word-break: break-all; display: flex; justify-content: space-between; align-items: center; }
.text-block { color: var(--text-muted); font-size: 14px; line-height: 1.5; margin-bottom: 16px; }
.flex-row { display: flex; gap: 12px; margin-bottom: 16px; }
.flex-row .inset-box { flex: 1; background: var(--inset); border: 1px solid var(--card-border); border-radius: 6px; padding: 12px; }
.inset-box-label { font-size: 10px; text-transform: uppercase; color: var(--text-muted); margin-bottom: 4px; font-weight: 600; }
.inset-box-val { font-size: 13px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-weight: 600; color: #fff; }
.progress-container { margin-bottom: 16px; display: flex; align-items: center; gap: 12px; }
.progress-bar { flex: 1; height: 6px; background: var(--inset); border-radius: 999px; overflow: hidden; border: 1px solid var(--card-border); }
.progress-fill { height: 100%; background: #10b981; border-radius: 999px; }
.progress-text { font-size: 12px; color: var(--text-muted); font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.card-footer { margin-top: auto; padding-top: 24px; display: flex; justify-content: space-between; align-items: center; }
.card-link { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--accent); text-decoration: none; cursor: pointer; }
.card-link:hover { opacity: 0.8; }
.card-link.muted { color: var(--text-muted); }
"""

_REACT_VITE_CONFIG = """\
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist' },
});
"""

_REACT_NGINX_CONF = """\
server {
    listen 8080;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location = /healthz {
        add_header Content-Type text/plain;
        return 200 'ok';
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
"""

_REACT_ENTRYPOINT = """\
#!/bin/sh
# Render runtime deployment metadata into config.json served alongside the SPA.
set -e
cat > /usr/share/nginx/html/config.json <<EOF
{
  "name": "${ENVIRONMENT_NAME:-launchpad-app}",
  "version": "${APP_VERSION:-1.0.0}",
  "namespace": "${POD_NAMESPACE:-default}",
  "pod": "${POD_NAME:-unknown}",
  "replicas": "${REPLICA_COUNT:-1}",
  "hasDatabase": ${HAS_DATABASE:-false},
  "hasRedis": ${HAS_REDIS:-false},
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
"""

# Shared static-frontend infra (Nginx-served SPA). ``__DIST__`` is the build
# output directory copied into the Nginx web root.
_STATIC_FRONTEND_DOCKERFILE = """\
# syntax=docker/dockerfile:1.7
# Launchpad-generated static SPA served by non-root Nginx on :8080.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
USER 0
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/10-launchpad-config.sh /docker-entrypoint.d/10-launchpad-config.sh
RUN chmod +x /docker-entrypoint.d/10-launchpad-config.sh \\
  && chown -R 101:101 /usr/share/nginx/html
COPY --from=build --chown=101:101 /app/__DIST__ /usr/share/nginx/html
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
"""

_STATIC_DOCKERIGNORE = """\
node_modules/
dist/
.angular/
.env
.git/
.gitignore
*.md
npm-debug.log
"""


def _static_frontend_common_files(
    app_name: str,
    deps: WorkloadDependenciesConfig,
    *,
    stack_label: str,
    dist_dir: str,
) -> dict[str, str]:
    """Return the Nginx/entrypoint/Dockerfile files shared by all static SPAs."""
    return {
        "nginx/default.conf": _REACT_NGINX_CONF,
        "docker-entrypoint.d/10-launchpad-config.sh": _REACT_ENTRYPOINT,
        "Dockerfile": _STATIC_FRONTEND_DOCKERFILE.replace("__DIST__", dist_dir),
        ".dockerignore": _STATIC_DOCKERIGNORE,
        "README.md": _app_readme(app_name, stack_label, 8080, deps, frontend=True),
    }


def _react_package_json(app_name: str) -> str:
    payload = {
        "name": app_name,
        "version": APP_VERSION,
        "private": True,
        "type": "module",
        "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},
        "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},
        "devDependencies": {
            "@vitejs/plugin-react": "^4.3.4",
            "vite": "^5.4.11",
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def _react_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    files = {
        "package.json": _react_package_json(app_name),
        "vite.config.js": _REACT_VITE_CONFIG,
        "index.html": _REACT_INDEX_HTML,
        "src/main.jsx": _REACT_MAIN_JSX,
        "src/App.jsx": _REACT_APP_JSX,
        "src/styles.css": _REACT_STYLES,
    }
    files.update(
        _static_frontend_common_files(app_name, deps, stack_label="React (Vite)", dist_dir="dist")
    )
    return files


# --------------------------------------------------------------------------- #
# Per-application README
# --------------------------------------------------------------------------- #


def _app_readme(
    app_name: str,
    stack_label: str,
    port: int,
    deps: WorkloadDependenciesConfig,
    *,
    frontend: bool = False,
) -> str:
    is_frontend = frontend
    dep_lines = []
    if deps.postgres.enabled:
        dep_lines.append("- **PostgreSQL** — `DATABASE_URL`")
    if deps.mysql.enabled:
        dep_lines.append("- **MySQL** — `MYSQL_URL`")
    if deps.mariadb.enabled:
        dep_lines.append("- **MariaDB** — `MARIADB_URL` (MySQL-compatible)")
    if deps.mongodb.enabled:
        dep_lines.append("- **MongoDB** — `MONGODB_URI`")
    if deps.redis.enabled:
        dep_lines.append("- **Redis** — `REDIS_URL`")
    deps_section = "\n".join(dep_lines) if dep_lines else "_None configured._"

    if is_frontend:
        endpoints = (
            "- `GET /` — status dashboard (deployment metadata, health indicator, "
            "configured dependencies)\n"
            "- `GET /healthz` — availability probe (used by Kubernetes liveness/readiness)\n"
            "- `GET /config.json` — runtime deployment metadata injected at container start"
        )
    else:
        endpoints = (
            "- `GET /` — live health dashboard\n"
            "- `GET /health` — liveness probe (process is up)\n"
            "- `GET /ready` — readiness probe (503 when a configured dependency is down)\n"
            "- `GET /info` — application metadata\n"
            "- `GET /api/status` — full JSON status (app + Kubernetes + database + Redis)"
        )

    return f"""# {app_name} — {stack_label}

Launchpad-generated {stack_label} mini-application. It is containerized, wired to
its Kubernetes manifests under `infra/k8s/manifests/`, and immediately runnable
on a local Kind cluster.

## Endpoints

{endpoints}

## Configured dependencies

{deps_section}

The application reads connection strings from the workload Secret
(`app-secrets`) and deployment metadata from the downward API
(`POD_NAME`, `POD_NAMESPACE`, `REPLICA_COUNT`, …).

## Run on Kind (no registry required)

From the workspace root:

```bash
# Build the image, load it into the Kind cluster, apply manifests, wait for rollout
./scripts/deploy-kind.sh

# Then browse the app:
kubectl -n <namespace> port-forward svc/app 8080:80
# open http://127.0.0.1:8080/
```

Individual steps are also available:

```bash
./scripts/build-image.sh   # docker build -t {app_name}:latest {("apps/" + app_name)}
./scripts/kind-load.sh     # kind load docker-image {app_name}:latest
kubectl apply -f infra/k8s/manifests/ -R
```

## Run locally with Docker

```bash
docker build -t {app_name}:latest apps/{app_name}
docker run --rm -p {port}:{port} {app_name}:latest
# open http://127.0.0.1:{port}/
```
"""


# --------------------------------------------------------------------------- #
# Vue (Vite) — static frontend
# --------------------------------------------------------------------------- #

_VUE_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Launchpad Frontend</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
"""

_VUE_MAIN_JS = """\
import { createApp } from 'vue';
import App from './App.vue';
import './styles.css';

createApp(App).mount('#app');
"""

_VUE_VITE_CONFIG = """\
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  build: { outDir: 'dist' },
});
"""

_VUE_APP_VUE = r"""<script setup>
import { ref, onMounted, onUnmounted } from 'vue';

const config = ref({});
const healthy = ref(true);
const updated = ref('');
let timer = null;

async function refresh() {
  try {
    const res = await fetch('/config.json', { cache: 'no-store' });
    config.value = await res.json();
  } catch (e) { /* keep previous */ }
  try {
    const h = await fetch('/healthz', { cache: 'no-store' });
    healthy.value = h.ok;
  } catch (e) { healthy.value = false; }
  updated.value = new Date().toLocaleTimeString();
}

onMounted(() => { refresh(); timer = setInterval(refresh, 5000); });
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <div class="wrap">
    <div class="breadcrumbs">
      <span>INFRASTRUCTURE</span> &rsaquo; <span class="active">SPA STATUS DASHBOARD</span>
    </div>
    <div class="header-top">
      <div>
        <h1>Frontend Health</h1>
        <p class="sub">Real-time monitoring of single-page application and environment.</p>
        <p class="target-line">Target: <code>{{ window.location.origin }}</code></p>
      </div>
      <div class="status-bar">
        <div class="auto-refresh">
          <span class="dot up"></span> AUTO-REFRESHING EVERY 5S
        </div>
        <button class="btn" @click="refresh">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
          Refresh Status
        </button>
      </div>
    </div>
    <div class="grid">
      <div class="card">
        <div class="card-header">
          <div class="card-header-left">
            <p>APPLICATION</p>
            <h3>FRONTEND SPA</h3>
          </div>
          <div :class="'pill ' + (healthy ? 'up' : 'err')">
            <span class="dot"></span> {{ healthy ? 'Available' : 'Unavailable' }}
          </div>
        </div>
        <div class="field">
          <span class="field-label">VERSION</span>
          <div class="field-val">{{ config.version || '—' }}</div>
        </div>
        <div class="card-footer">
          <span class="card-link">VIEW LOGS</span>
        </div>
      </div>
      <div class="card">
        <div class="card-header">
          <div class="card-header-left">
            <p>DEPLOYMENT</p>
            <h3>METADATA</h3>
          </div>
          <div class="pill up">
            <span class="dot"></span> Active
          </div>
        </div>
        <div class="field">
          <span class="field-label">NAMESPACE</span>
          <div class="field-val">{{ config.namespace || '—' }}</div>
        </div>
        <div class="flex-row">
          <div class="inset-box">
            <div class="inset-box-label">POD</div>
            <div class="inset-box-val">{{ config.pod || '—' }}</div>
          </div>
          <div class="inset-box">
            <div class="inset-box-label">REPLICAS</div>
            <div class="inset-box-val">{{ config.replicas || '—' }}</div>
          </div>
        </div>
        <div class="card-footer">
          <span class="card-link">INSPECT POD</span>
        </div>
      </div>
      <div class="card" v-if="config.hasDatabase">
        <div class="card-header">
          <div class="card-header-left">
            <p>DATABASE</p>
            <h3>POSTGRESQL</h3>
          </div>
          <div class="pill na">
            <span class="dot"></span> Configured
          </div>
        </div>
        <div class="text-block">
          Live connection status is reported by the backend service.
        </div>
        <div class="card-footer">
          <span class="card-link">CREDENTIALS</span>
        </div>
      </div>
      <div class="card" v-if="config.hasRedis">
        <div class="card-header">
          <div class="card-header-left">
            <p>CACHE</p>
            <h3>REDIS CACHE</h3>
          </div>
          <div class="pill na">
            <span class="dot"></span> Configured
          </div>
        </div>
        <div class="text-block">
          Live ping status is reported by the backend service.
        </div>
        <div class="card-footer">
          <span class="card-link">METRICS</span>
        </div>
      </div>
    </div>
  </div>
</template>
"""


def _vue_package_json(app_name: str) -> str:
    payload = {
        "name": app_name,
        "version": APP_VERSION,
        "private": True,
        "type": "module",
        "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},
        "dependencies": {"vue": "^3.5.13"},
        "devDependencies": {"@vitejs/plugin-vue": "^5.2.1", "vite": "^5.4.11"},
    }
    return json.dumps(payload, indent=2) + "\n"


def _vue_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    files = {
        "package.json": _vue_package_json(app_name),
        "vite.config.js": _VUE_VITE_CONFIG,
        "index.html": _VUE_INDEX_HTML,
        "src/main.js": _VUE_MAIN_JS,
        "src/App.vue": _VUE_APP_VUE,
        "src/styles.css": _REACT_STYLES,
    }
    files.update(_static_frontend_common_files(app_name, deps, stack_label="Vue", dist_dir="dist"))
    return files


# --------------------------------------------------------------------------- #
# Svelte (Vite) — static frontend
# --------------------------------------------------------------------------- #

_SVELTE_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Launchpad Frontend</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
"""

_SVELTE_MAIN_JS = """\
import './styles.css';
import App from './App.svelte';

const app = new App({ target: document.getElementById('app') });
export default app;
"""

_SVELTE_VITE_CONFIG = """\
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  build: { outDir: 'dist' },
});
"""

_SVELTE_CONFIG_JS = """\
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
export default { preprocess: vitePreprocess() };
"""

_SVELTE_APP = r"""<script>
  import { onMount, onDestroy } from 'svelte';

  let config = {};
  let healthy = true;
  let updated = '';
  let timer;

  async function refresh() {
    try {
      const res = await fetch('/config.json', { cache: 'no-store' });
      config = await res.json();
    } catch (e) { /* keep previous */ }
    try {
      const h = await fetch('/healthz', { cache: 'no-store' });
      healthy = h.ok;
    } catch (e) { healthy = false; }
    updated = new Date().toLocaleTimeString();
  }

  onMount(() => { refresh(); timer = setInterval(refresh, 5000); });
  onDestroy(() => clearInterval(timer));
</script>

<div class="wrap">
  <div class="breadcrumbs">
    <span>INFRASTRUCTURE</span> &rsaquo; <span class="active">SPA STATUS DASHBOARD</span>
  </div>
  <div class="header-top">
    <div>
      <h1>Frontend Health</h1>
      <p class="sub">Real-time monitoring of single-page application and environment.</p>
      <p class="target-line">Target: <code>{window.location.origin}</code></p>
    </div>
    <div class="status-bar">
      <div class="auto-refresh">
        <span class="dot up"></span> AUTO-REFRESHING EVERY 5S
      </div>
      <button class="btn" on:click={refresh}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
        Refresh Status
      </button>
    </div>
  </div>
  <div class="grid">
    <div class="card">
      <div class="card-header">
        <div class="card-header-left">
          <p>APPLICATION</p>
          <h3>FRONTEND SPA</h3>
        </div>
        <div class="pill {healthy ? 'up' : 'err'}">
          <span class="dot"></span> {healthy ? 'Available' : 'Unavailable'}
        </div>
      </div>
      <div class="field">
        <span class="field-label">VERSION</span>
        <div class="field-val">{config.version || '—'}</div>
      </div>
      <div class="card-footer">
        <span class="card-link">VIEW LOGS</span>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <div class="card-header-left">
          <p>DEPLOYMENT</p>
          <h3>METADATA</h3>
        </div>
        <div class="pill up">
          <span class="dot"></span> Active
        </div>
      </div>
      <div class="field">
        <span class="field-label">NAMESPACE</span>
        <div class="field-val">{config.namespace || '—'}</div>
      </div>
      <div class="flex-row">
        <div class="inset-box">
          <div class="inset-box-label">POD</div>
          <div class="inset-box-val">{config.pod || '—'}</div>
        </div>
        <div class="inset-box">
          <div class="inset-box-label">REPLICAS</div>
          <div class="inset-box-val">{config.replicas || '—'}</div>
        </div>
      </div>
      <div class="card-footer">
        <span class="card-link">INSPECT POD</span>
      </div>
    </div>
    {#if config.hasDatabase}
      <div class="card">
        <div class="card-header">
          <div class="card-header-left">
            <p>DATABASE</p>
            <h3>POSTGRESQL</h3>
          </div>
          <div class="pill na">
            <span class="dot"></span> Configured
          </div>
        </div>
        <div class="text-block">
          Live connection status is reported by the backend service.
        </div>
        <div class="card-footer">
          <span class="card-link">CREDENTIALS</span>
        </div>
      </div>
    {/if}
    {#if config.hasRedis}
      <div class="card">
        <div class="card-header">
          <div class="card-header-left">
            <p>CACHE</p>
            <h3>REDIS CACHE</h3>
          </div>
          <div class="pill na">
            <span class="dot"></span> Configured
          </div>
        </div>
        <div class="text-block">
          Live ping status is reported by the backend service.
        </div>
        <div class="card-footer">
          <span class="card-link">METRICS</span>
        </div>
      </div>
    {/if}
  </div>
  <footer>Generated by Launchpad &middot; last update {updated || '—'}</footer>
</div>
"""


def _svelte_package_json(app_name: str) -> str:
    payload = {
        "name": app_name,
        "version": APP_VERSION,
        "private": True,
        "type": "module",
        "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},
        "devDependencies": {
            "svelte": "^4.2.19",
            "@sveltejs/vite-plugin-svelte": "^3.1.2",
            "vite": "^5.4.11",
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def _svelte_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    files = {
        "package.json": _svelte_package_json(app_name),
        "vite.config.js": _SVELTE_VITE_CONFIG,
        "svelte.config.js": _SVELTE_CONFIG_JS,
        "index.html": _SVELTE_INDEX_HTML,
        "src/main.js": _SVELTE_MAIN_JS,
        "src/App.svelte": _SVELTE_APP,
        "src/styles.css": _REACT_STYLES,
    }
    files.update(_static_frontend_common_files(app_name, deps, stack_label="Svelte", dist_dir="dist"))
    return files


# --------------------------------------------------------------------------- #
# Angular — static frontend (application builder)
# --------------------------------------------------------------------------- #

_ANGULAR_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Launchpad Frontend</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
  </head>
  <body>
    <app-root></app-root>
  </body>
</html>
"""

_ANGULAR_MAIN_TS = """\
import { bootstrapApplication } from '@angular/platform-browser';
import { AppComponent } from './app/app.component';

bootstrapApplication(AppComponent).catch((err) => console.error(err));
"""

_ANGULAR_APP_COMPONENT = r"""import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="wrap">
      <div class="breadcrumbs">
        <span>INFRASTRUCTURE</span> &rsaquo; <span class="active">SPA STATUS DASHBOARD</span>
      </div>
      <div class="header-top">
        <div>
          <h1>Frontend Health</h1>
          <p class="sub">Real-time monitoring of single-page application and environment.</p>
          <p class="target-line">Target: <code>{{ windowLocationOrigin }}</code></p>
        </div>
        <div class="status-bar">
          <div class="auto-refresh">
            <span class="dot up"></span> AUTO-REFRESHING EVERY 5S
          </div>
          <button class="btn" (click)="refresh()">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
            Refresh Status
          </button>
        </div>
      </div>
      <div class="grid">
        <div class="card">
          <div class="card-header">
            <div class="card-header-left">
              <p>APPLICATION</p>
              <h3>FRONTEND SPA</h3>
            </div>
            <div class="pill" [class.up]="healthy" [class.err]="!healthy">
              <span class="dot"></span> {{ healthy ? 'Available' : 'Unavailable' }}
            </div>
          </div>
          <div class="field">
            <span class="field-label">VERSION</span>
            <div class="field-val">{{ config.version || '—' }}</div>
          </div>
          <div class="card-footer">
            <span class="card-link">VIEW LOGS</span>
          </div>
        </div>
        <div class="card">
          <div class="card-header">
            <div class="card-header-left">
              <p>DEPLOYMENT</p>
              <h3>METADATA</h3>
            </div>
            <div class="pill up">
              <span class="dot"></span> Active
            </div>
          </div>
          <div class="field">
            <span class="field-label">NAMESPACE</span>
            <div class="field-val">{{ config.namespace || '—' }}</div>
          </div>
          <div class="flex-row">
            <div class="inset-box">
              <div class="inset-box-label">POD</div>
              <div class="inset-box-val">{{ config.pod || '—' }}</div>
            </div>
            <div class="inset-box">
              <div class="inset-box-label">REPLICAS</div>
              <div class="inset-box-val">{{ config.replicas || '—' }}</div>
            </div>
          </div>
          <div class="card-footer">
            <span class="card-link">INSPECT POD</span>
          </div>
        </div>
        <div class="card" *ngIf="config.hasDatabase">
          <div class="card-header">
            <div class="card-header-left">
              <p>DATABASE</p>
              <h3>POSTGRESQL</h3>
            </div>
            <div class="pill na">
              <span class="dot"></span> Configured
            </div>
          </div>
          <div class="text-block">
            Live connection status is reported by the backend service.
          </div>
          <div class="card-footer">
            <span class="card-link">CREDENTIALS</span>
          </div>
        </div>
        <div class="card" *ngIf="config.hasRedis">
          <div class="card-header">
            <div class="card-header-left">
              <p>CACHE</p>
              <h3>REDIS CACHE</h3>
            </div>
            <div class="pill na">
              <span class="dot"></span> Configured
            </div>
          </div>
          <div class="text-block">
            Live ping status is reported by the backend service.
          </div>
          <div class="card-footer">
            <span class="card-link">METRICS</span>
          </div>
        </div>
      </div>
    </div>
  `,
})
export class AppComponent implements OnInit, OnDestroy {
  config: any = {};
  healthy = true;
  updated = '';
  private timer: any;

  async refresh(): Promise<void> {
    try {
      const res = await fetch('/config.json', { cache: 'no-store' });
      this.config = await res.json();
    } catch (e) { /* keep previous */ }
    try {
      const h = await fetch('/healthz', { cache: 'no-store' });
      this.healthy = h.ok;
    } catch (e) { this.healthy = false; }
    this.updated = new Date().toLocaleTimeString();
  }

  ngOnInit(): void { this.refresh(); this.timer = setInterval(() => this.refresh(), 5000); }
  ngOnDestroy(): void { clearInterval(this.timer); }
}
"""

_ANGULAR_TSCONFIG = """\
{
  "compileOnSave": false,
  "compilerOptions": {
    "outDir": "./dist/out-tsc",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "experimentalDecorators": true,
    "moduleResolution": "bundler",
    "target": "ES2022",
    "module": "ES2022",
    "lib": ["ES2022", "dom"]
  },
  "angularCompilerOptions": {
    "strictTemplates": true
  }
}
"""

_ANGULAR_TSCONFIG_APP = """\
{
  "extends": "./tsconfig.json",
  "compilerOptions": { "outDir": "./out-tsc/app", "types": [] },
  "files": ["src/main.ts"]
}
"""


def _angular_json(app_name: str) -> str:
    payload = {
        "$schema": "./node_modules/@angular/cli/lib/config/schema.json",
        "version": 1,
        "newProjectRoot": "projects",
        "projects": {
            app_name: {
                "projectType": "application",
                "root": "",
                "sourceRoot": "src",
                "architect": {
                    "build": {
                        "builder": "@angular-devkit/build-angular:application",
                        "options": {
                            "outputPath": f"dist/{app_name}",
                            "index": "src/index.html",
                            "browser": "src/main.ts",
                            "tsConfig": "tsconfig.app.json",
                            "styles": ["src/styles.css"],
                        },
                    }
                },
            }
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def _angular_package_json(app_name: str) -> str:
    payload = {
        "name": app_name,
        "version": APP_VERSION,
        "private": True,
        "scripts": {"build": "ng build"},
        "dependencies": {
            "@angular/animations": "^17.3.0",
            "@angular/common": "^17.3.0",
            "@angular/compiler": "^17.3.0",
            "@angular/core": "^17.3.0",
            "@angular/forms": "^17.3.0",
            "@angular/platform-browser": "^17.3.0",
            "@angular/platform-browser-dynamic": "^17.3.0",
            "@angular/router": "^17.3.0",
            "rxjs": "~7.8.0",
            "tslib": "^2.3.0",
            "zone.js": "~0.14.3",
        },
        "devDependencies": {
            "@angular-devkit/build-angular": "^17.3.0",
            "@angular/cli": "^17.3.0",
            "@angular/compiler-cli": "^17.3.0",
            "typescript": "~5.4.2",
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def _angular_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    files = {
        "package.json": _angular_package_json(app_name),
        "angular.json": _angular_json(app_name),
        "tsconfig.json": _ANGULAR_TSCONFIG,
        "tsconfig.app.json": _ANGULAR_TSCONFIG_APP,
        "src/index.html": _ANGULAR_INDEX_HTML,
        "src/main.ts": _ANGULAR_MAIN_TS,
        "src/styles.css": _REACT_STYLES,
        "src/app/app.component.ts": _ANGULAR_APP_COMPONENT,
    }
    # Angular's application builder emits into dist/<name>/browser.
    files.update(
        _static_frontend_common_files(
            app_name, deps, stack_label="Angular", dist_dir=f"dist/{app_name}/browser"
        )
    )
    return files


# --------------------------------------------------------------------------- #
# Shared SSR-frontend dashboard (Next.js & Nuxt) + metadata payload
# --------------------------------------------------------------------------- #

# Server handler body (JS) returning deployment metadata from the environment.
_SSR_META_JS = """\
{
  name: process.env.ENVIRONMENT_NAME || 'launchpad-app',
  version: process.env.APP_VERSION || '1.0.0',
  namespace: process.env.POD_NAMESPACE || 'default',
  pod: process.env.POD_NAME || 'unknown',
  replicas: process.env.REPLICA_COUNT || '1',
  hasDatabase: (process.env.HAS_DATABASE || 'false') === 'true',
  hasRedis: (process.env.HAS_REDIS || 'false') === 'true',
}"""

_SSR_DASHBOARD_STYLES = _REACT_STYLES


# --------------------------------------------------------------------------- #
# Next.js — SSR frontend (standalone output)
# --------------------------------------------------------------------------- #

_NEXT_CONFIG = """\
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  async rewrites() {
    const backendUrl = process.env.API_URL || process.env.BACKEND_URL || 'http://api-server:8080';
    return [
      {
        source: '/backend-api/:path*',
        destination: `${backendUrl}/:path*`,
      },
    ];
  },
};
module.exports = nextConfig;
"""

_NEXT_LAYOUT = """\
import './globals.css';

export const metadata = { title: 'Launchpad Frontend' };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
"""

_NEXT_HEALTHZ_ROUTE = """\
export const dynamic = 'force-dynamic';
export async function GET() {
  return new Response('ok', { headers: { 'Content-Type': 'text/plain' } });
}
"""

_NEXT_META_ROUTE = (
    "export const dynamic = 'force-dynamic';\n"
    "export async function GET() {\n"
    "  const base = " + _SSR_META_JS + ";\n"
    "  return Response.json({\n"
    "    ...base,\n"
    "    apiUrl: process.env.API_URL || process.env.BACKEND_URL || 'http://api-server:8080',\n"
    "  });\n"
    "}\n"
)

_NEXT_PAGE = r"""'use client';
import { useEffect, useState } from 'react';

export default function Page() {
  const [meta, setMeta] = useState({});
  const [backendStatus, setBackendStatus] = useState(null);
  const [healthy, setHealthy] = useState(true);
  const [updated, setUpdated] = useState('');

  async function refresh() {
    try { const r = await fetch('/api/meta', { cache: 'no-store' }); setMeta(await r.json()); } catch (e) {}
    try { const b = await fetch('/backend-api/api/status', { cache: 'no-store' }); setBackendStatus(await b.json()); } catch (e) {}
    try { const h = await fetch('/healthz', { cache: 'no-store' }); setHealthy(h.ok); } catch (e) { setHealthy(false); }
    setUpdated(new Date().toLocaleTimeString());
  }
  useEffect(() => { refresh(); const id = setInterval(refresh, 5000); return () => clearInterval(id); }, []);

  const dbOk = backendStatus?.database?.connected;
  const redisOk = backendStatus?.redis?.connected;

  return (
    <div className="wrap">
      <div className="breadcrumbs">
        <span>INFRASTRUCTURE</span> &rsaquo; <span className="active">FULLSTACK STATUS DASHBOARD</span>
      </div>
      <div className="header-top">
        <div>
          <h1>Fullstack Health</h1>
          <p className="sub">Real-time monitoring of frontend, backend, and persistence layers.</p>
          <p className="target-line">Target: <code>{meta.apiUrl || 'http://launch-express-service:3000'}</code></p>
        </div>
        <div className="status-bar">
          <div className="auto-refresh">
            <span className="dot up" /> AUTO-REFRESHING EVERY 5S
          </div>
          <button className="btn" onClick={refresh}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
            Refresh Status
          </button>
        </div>
      </div>
      
      <div className="grid">
        <div className="card">
          <div className="card-header">
            <div className="card-header-left">
              <p>APPLICATION</p>
              <h3>FRONTEND APP<br/>(NEXT.JS)</h3>
            </div>
            <div className={'pill ' + (healthy ? 'up' : 'err')}>
              <span className="dot" /> {healthy ? 'Available' : 'Unavailable'}
            </div>
          </div>
          <div className="field">
            <span className="field-label">NAMESPACE</span>
            <div className="field-val">{meta.namespace || 'launchpad-env-fea0ddc2...'}</div>
          </div>
          <div className="field">
            <span className="field-label">POD</span>
            <div className="field-val">{meta.pod || 'launch-nextjs-844cf9764b...'}</div>
          </div>
          <div className="card-footer">
            <span className="card-link">VIEW LOGS</span>
            <span className="card-link muted">INSPECT POD</span>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <div className="card-header-left">
              <p>SERVICES</p>
              <h3>BACKEND API<br/>(EXPRESS)</h3>
            </div>
            <div className={'pill ' + (backendStatus ? 'up' : 'down')}>
              <span className="dot" /> {backendStatus ? 'Connected' : 'Connecting...'}
            </div>
          </div>
          <div className="field">
            <span className="field-label">INTERNAL URL</span>
            <div className="field-val">
              <span>{meta.apiUrl || 'http://launch-express-service:3000'}</span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
            </div>
          </div>
          <div className="field">
            <span className="field-label">API NAME</span>
            <div className="field-val">{backendStatus?.name || 'api-server'}</div>
          </div>
          <div className="card-footer">
            <span className="card-link down" style={{color: '#f59e0b'}}>TROUBLESHOOT</span>
            <span className="card-link muted">PROXY TEST</span>
          </div>
        </div>

        {meta.hasDatabase && (
          <div className="card">
            <div className="card-header">
              <div className="card-header-left">
                <p>DATABASE</p>
                <h3>POSTGRESQL<br/>DATABASE</h3>
              </div>
              <div className={'pill ' + (dbOk ? 'up' : 'na')}>
                <span className="dot" /> {dbOk ? 'Connected' : (backendStatus ? 'Disconnected' : 'Configured')}
              </div>
            </div>
            <div className="text-block">
              Database URL connected to backend service using the <code>DATABASE_URL</code> environment variable.
            </div>
            <div className="flex-row">
              <div className="inset-box">
                <div className="inset-box-label">STORAGE</div>
                <div className="inset-box-val">20 GB PVC</div>
              </div>
              <div className="inset-box">
                <div className="inset-box-label">VERSION</div>
                <div className="inset-box-val">PG 14.5</div>
              </div>
            </div>
            <div className="card-footer">
              <span className="card-link">CREDENTIALS</span>
              <span className="card-link muted">SNAPSHOTS</span>
            </div>
          </div>
        )}

        {meta.hasRedis && (
          <div className="card">
            <div className="card-header">
              <div className="card-header-left">
                <p>CACHE</p>
                <h3>REDIS CACHE</h3>
              </div>
              <div className={'pill ' + (redisOk ? 'up' : 'na')}>
                <span className="dot" /> {redisOk ? 'Connected' : (backendStatus ? 'Disconnected' : 'Configured')}
              </div>
            </div>
            <div className="text-block">
              Redis instance is ready and connected for session management via <code>REDIS_URL</code>.
            </div>
            <div className="progress-container">
              <div className="progress-bar"><div className="progress-fill" style={{width: '12%'}}></div></div>
              <span className="progress-text">12% Memory Used</span>
            </div>
            <div className="card-footer">
              <span className="card-link">FLUSH CACHE</span>
              <span className="card-link muted">METRICS</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
"""


def _next_dockerfile(port: int) -> str:
    return (
        """\
# syntax=docker/dockerfile:1.7
# Launchpad-generated Next.js image (standalone output, non-root USER 10001).
FROM node:22-alpine AS deps
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app && apk add --no-cache wget
ENV NODE_ENV=production PORT=__PORT__ HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=10001:10001 /app/public ./public
COPY --from=build --chown=10001:10001 /app/.next/standalone ./
COPY --from=build --chown=10001:10001 /app/.next/static ./.next/static
USER 10001:10001
EXPOSE __PORT__
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:__PORT__/healthz || exit 1
CMD ["node", "server.js"]
""".replace("__PORT__", str(port))
    )


def _next_package_json(app_name: str) -> str:
    payload = {
        "name": app_name,
        "version": APP_VERSION,
        "private": True,
        "scripts": {"dev": "next dev", "build": "next build", "start": "next start"},
        "dependencies": {
            "next": "^14.2.15",
            "react": "^18.3.1",
            "react-dom": "^18.3.1",
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def _next_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    return {
        "package.json": _next_package_json(app_name),
        "next.config.js": _NEXT_CONFIG,
        "app/globals.css": _SSR_DASHBOARD_STYLES,
        "app/layout.jsx": _NEXT_LAYOUT,
        "app/page.jsx": _NEXT_PAGE,
        "app/healthz/route.js": _NEXT_HEALTHZ_ROUTE,
        "app/api/meta/route.js": _NEXT_META_ROUTE,
        "public/.gitkeep": "",
        "Dockerfile": _next_dockerfile(port),
        ".dockerignore": _NODE_DOCKERIGNORE + ".next/\n",
        "README.md": _app_readme(app_name, "Next.js", port, deps, frontend=True),
    }


# --------------------------------------------------------------------------- #
# Nuxt — SSR frontend (Nitro node-server)
# --------------------------------------------------------------------------- #

_NUXT_CONFIG = """\
export default defineNuxtConfig({
  ssr: true,
  nitro: { preset: 'node-server' },
  devtools: { enabled: false },
});
"""

_NUXT_HEALTHZ = """\
export default defineEventHandler(() => 'ok');
"""

_NUXT_META = (
    "export default defineEventHandler(() => (" + _SSR_META_JS + "));\n"
)

_NUXT_APP_VUE = r"""<script setup>
import { ref, onMounted, onUnmounted } from 'vue';

const meta = ref({});
const healthy = ref(true);
const updated = ref('');
let timer = null;

async function refresh() {
  try { const r = await fetch('/api/meta', { cache: 'no-store' }); meta.value = await r.json(); } catch (e) {}
  try { const h = await fetch('/healthz', { cache: 'no-store' }); healthy.value = h.ok; } catch (e) { healthy.value = false; }
  updated.value = new Date().toLocaleTimeString();
}
onMounted(() => { refresh(); timer = setInterval(refresh, 5000); });
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <div class="wrap">
    <div class="breadcrumbs">
      <span>INFRASTRUCTURE</span> &rsaquo; <span class="active">SSR STATUS DASHBOARD</span>
    </div>
    <div class="header-top">
      <div>
        <h1>Frontend Health</h1>
        <p class="sub">Real-time monitoring of single-page application and environment.</p>
        <p class="target-line">Target: <code>{{ windowLocationOrigin }}</code></p>
      </div>
      <div class="status-bar">
        <div class="auto-refresh">
          <span class="dot up"></span> AUTO-REFRESHING EVERY 5S
        </div>
        <button class="btn" @click="refresh">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
          Refresh Status
        </button>
      </div>
    </div>
    <div class="grid">
      <div class="card">
        <div class="card-header">
          <div class="card-header-left">
            <p>APPLICATION</p>
            <h3>FRONTEND NUXT</h3>
          </div>
          <div :class="'pill ' + (healthy ? 'up' : 'err')">
            <span class="dot"></span> {{ healthy ? 'Available' : 'Unavailable' }}
          </div>
        </div>
        <div class="field">
          <span class="field-label">VERSION</span>
          <div class="field-val">{{ meta.version || '—' }}</div>
        </div>
        <div class="card-footer">
          <span class="card-link">VIEW LOGS</span>
        </div>
      </div>
      <div class="card">
        <div class="card-header">
          <div class="card-header-left">
            <p>DEPLOYMENT</p>
            <h3>METADATA</h3>
          </div>
          <div class="pill up">
            <span class="dot"></span> Active
          </div>
        </div>
        <div class="field">
          <span class="field-label">NAMESPACE</span>
          <div class="field-val">{{ meta.namespace || '—' }}</div>
        </div>
        <div class="flex-row">
          <div class="inset-box">
            <div class="inset-box-label">POD</div>
            <div class="inset-box-val">{{ meta.pod || '—' }}</div>
          </div>
          <div class="inset-box">
            <div class="inset-box-label">REPLICAS</div>
            <div class="inset-box-val">{{ meta.replicas || '—' }}</div>
          </div>
        </div>
        <div class="card-footer">
          <span class="card-link">INSPECT POD</span>
        </div>
      </div>
      <div class="card" v-if="meta.hasDatabase">
        <div class="card-header">
          <div class="card-header-left">
            <p>DATABASE</p>
            <h3>POSTGRESQL</h3>
          </div>
          <div class="pill na">
            <span class="dot"></span> Configured
          </div>
        </div>
        <div class="text-block">
          Live connection status is reported by the backend service.
        </div>
        <div class="card-footer">
          <span class="card-link">CREDENTIALS</span>
        </div>
      </div>
      <div class="card" v-if="meta.hasRedis">
        <div class="card-header">
          <div class="card-header-left">
            <p>CACHE</p>
            <h3>REDIS CACHE</h3>
          </div>
          <div class="pill na">
            <span class="dot"></span> Configured
          </div>
        </div>
        <div class="text-block">
          Live ping status is reported by the backend service.
        </div>
        <div class="card-footer">
          <span class="card-link">METRICS</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style>
__STYLES__
</style>
"""


def _nuxt_dockerfile(port: int) -> str:
    return (
        """\
# syntax=docker/dockerfile:1.7
# Launchpad-generated Nuxt image (Nitro node-server, non-root USER 10001).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app && apk add --no-cache wget
COPY --from=build --chown=10001:10001 /app/.output ./.output
ENV NODE_ENV=production HOST=0.0.0.0 PORT=__PORT__ NITRO_PORT=__PORT__ NITRO_HOST=0.0.0.0
USER 10001:10001
EXPOSE __PORT__
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:__PORT__/healthz || exit 1
CMD ["node", ".output/server/index.mjs"]
""".replace("__PORT__", str(port))
    )


def _nuxt_package_json(app_name: str) -> str:
    payload = {
        "name": app_name,
        "version": APP_VERSION,
        "private": True,
        "scripts": {"build": "nuxt build", "dev": "nuxt dev", "preview": "nuxt preview"},
        "devDependencies": {"nuxt": "^3.14.159"},
    }
    return json.dumps(payload, indent=2) + "\n"


def _nuxt_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    return {
        "package.json": _nuxt_package_json(app_name),
        "nuxt.config.ts": _NUXT_CONFIG,
        "app.vue": _NUXT_APP_VUE.replace("__STYLES__", _SSR_DASHBOARD_STYLES.rstrip()),
        "server/routes/healthz.ts": _NUXT_HEALTHZ,
        "server/api/meta.get.ts": _NUXT_META,
        "Dockerfile": _nuxt_dockerfile(port),
        ".dockerignore": _NODE_DOCKERIGNORE + ".nuxt/\n.output/\n",
        "README.md": _app_readme(app_name, "Nuxt", port, deps, frontend=True),
    }


# --------------------------------------------------------------------------- #
# Shared Python health/dependency check block (Flask, Django)
# --------------------------------------------------------------------------- #

# Reused verbatim by the Flask and Django generators. FastAPI ships its own
# self-contained copy (see _FASTAPI_MAIN). ``__DASHBOARD__`` is replaced with a
# repr() of the shared backend dashboard HTML.
_PY_CHECKS = r'''import os
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

APP_NAME = os.environ.get("ENVIRONMENT_NAME", "launchpad-app")
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
NAMESPACE = os.environ.get("POD_NAMESPACE", "default")
POD_NAME = os.environ.get("POD_NAME", socket.gethostname())
REPLICA_COUNT = os.environ.get("REPLICA_COUNT", "1")
PORT = int(os.environ.get("PORT", "8000"))
DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or os.environ.get("MYSQL_URL")
    or os.environ.get("MARIADB_URL")
    or os.environ.get("MONGODB_URI")
)
REDIS_URL = os.environ.get("REDIS_URL")
STARTED_AT = time.time()

_last_db_success = None
_last_redis_success = None

DASHBOARD_HTML = __DASHBOARD__


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def check_database():
    global _last_db_success
    url = DATABASE_URL
    if not url:
        return {"configured": False, "connected": False, "error": None,
                "kind": None, "lastSuccess": _last_db_success}
    try:
        if url.startswith("postgres"):
            import psycopg

            with psycopg.connect(url, connect_timeout=3) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            kind = "postgresql"
        elif url.startswith("mysql") or url.startswith("mariadb"):
            import pymysql

            parsed = urlparse(url)
            conn = pymysql.connect(
                host=parsed.hostname,
                port=parsed.port or 3306,
                user=parsed.username,
                password=parsed.password,
                database=(parsed.path or "/").lstrip("/") or None,
                connect_timeout=3,
            )
            conn.ping(reconnect=False)
            conn.close()
            kind = "mysql"
        elif url.startswith("mongodb"):
            from pymongo import MongoClient

            client = MongoClient(url, serverSelectionTimeoutMS=3000)
            client.admin.command("ping")
            client.close()
            kind = "mongodb"
        else:
            return {"configured": True, "connected": False,
                    "error": "unsupported database scheme", "kind": None,
                    "lastSuccess": _last_db_success}
        _last_db_success = _now_iso()
        return {"configured": True, "connected": True, "error": None,
                "kind": kind, "lastSuccess": _last_db_success}
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "connected": False, "error": str(exc)[:200],
                "kind": None, "lastSuccess": _last_db_success}


def check_redis():
    global _last_redis_success
    if not REDIS_URL:
        return {"configured": False, "connected": False, "error": None,
                "latencyMs": None, "lastSuccess": _last_redis_success}
    try:
        import redis

        client = redis.from_url(REDIS_URL, socket_connect_timeout=3, socket_timeout=3)
        start = time.perf_counter()
        client.ping()
        latency = round((time.perf_counter() - start) * 1000, 2)
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
        _last_redis_success = _now_iso()
        return {"configured": True, "connected": True, "error": None,
                "latencyMs": latency, "lastSuccess": _last_redis_success}
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "connected": False, "error": str(exc)[:200],
                "latencyMs": None, "lastSuccess": _last_redis_success}


def build_status():
    database = check_database()
    redis_status = check_redis()
    return {
        "app": {"name": APP_NAME, "version": APP_VERSION, "status": "healthy",
                "uptimeSeconds": round(time.time() - STARTED_AT, 1)},
        "kubernetes": {"namespace": NAMESPACE, "pod": POD_NAME,
                       "replicas": REPLICA_COUNT, "deployment": "app"},
        "database": database,
        "redis": redis_status,
        "timestamp": _now_iso(),
    }


def readiness_problems():
    status = build_status()
    problems = []
    if status["database"]["configured"] and not status["database"]["connected"]:
        problems.append("database")
    if status["redis"]["configured"] and not status["redis"]["connected"]:
        problems.append("redis")
    return problems
'''


def _py_deps(deps: WorkloadDependenciesConfig) -> list[str]:
    lines: list[str] = []
    if deps.postgres.enabled:
        lines.append("psycopg[binary]==3.2.3")
    if deps.mysql.enabled or deps.mariadb.enabled:
        lines.append("PyMySQL==1.1.1")
    if deps.mongodb.enabled:
        lines.append("pymongo==4.10.1")
    if deps.redis.enabled:
        lines.append("redis==5.2.1")
    return lines


# --------------------------------------------------------------------------- #
# Flask backend
# --------------------------------------------------------------------------- #

_FLASK_ROUTES = '''

from flask import Flask, Response, jsonify

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify(status="ok", timestamp=_now_iso())


@app.get("/ready")
def ready():
    problems = readiness_problems()
    body = {"status": "ready" if not problems else "degraded",
            "problems": problems, "timestamp": _now_iso()}
    return jsonify(body), (503 if problems else 200)


@app.get("/info")
def info():
    return jsonify(name=APP_NAME, version=APP_VERSION, namespace=NAMESPACE,
                   pod=POD_NAME, replicas=REPLICA_COUNT, port=PORT,
                   dependencies={"database": bool(DATABASE_URL), "redis": bool(REDIS_URL)})


@app.get("/api/status")
def api_status():
    return jsonify(build_status())


@app.get("/")
def dashboard():
    return Response(DASHBOARD_HTML, mimetype="text/html")
'''

_FLASK_DOCKERFILE = """\
# syntax=docker/dockerfile:1.7
# Launchpad-generated Flask image (gunicorn, non-root USER 10001).
FROM python:3.12-slim AS builder
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M -s /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=10001:10001 . .
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PORT=__PORT__
USER 10001:10001
EXPOSE __PORT__
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:__PORT__/health'); sys.exit(0)" || exit 1
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT} -w 2 app:app"]
"""


def _flask_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    app_py = (_PY_CHECKS + _FLASK_ROUTES).replace("__DASHBOARD__", repr(_BACKEND_DASHBOARD_HTML))
    reqs = ["flask==3.1.0", "gunicorn==23.0.0"] + _py_deps(deps)
    return {
        "app.py": app_py,
        "requirements.txt": "\n".join(reqs) + "\n",
        "Dockerfile": _FLASK_DOCKERFILE.replace("__PORT__", str(port)),
        ".dockerignore": _PY_DOCKERIGNORE,
        "README.md": _app_readme(app_name, "Flask", port, deps),
    }


# --------------------------------------------------------------------------- #
# Django backend
# --------------------------------------------------------------------------- #

_DJANGO_MANAGE = """\
#!/usr/bin/env python
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
"""

_DJANGO_SETTINGS = """\
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "launchpad-insecure-dev-key")
DEBUG = False
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth"]
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = []
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {}
USE_TZ = True
"""

_DJANGO_URLS = """\
from django.urls import path

from health import views

urlpatterns = [
    path("", views.dashboard),
    path("health", views.health),
    path("ready", views.ready),
    path("info", views.info),
    path("api/status", views.api_status),
]
"""

_DJANGO_WSGI = """\
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()
"""

_DJANGO_VIEWS_ROUTES = '''

from django.http import HttpResponse, JsonResponse


def health(request):
    return JsonResponse({"status": "ok", "timestamp": _now_iso()})


def ready(request):
    problems = readiness_problems()
    return JsonResponse(
        {"status": "ready" if not problems else "degraded",
         "problems": problems, "timestamp": _now_iso()},
        status=503 if problems else 200,
    )


def info(request):
    return JsonResponse({
        "name": APP_NAME, "version": APP_VERSION, "namespace": NAMESPACE,
        "pod": POD_NAME, "replicas": REPLICA_COUNT, "port": PORT,
        "dependencies": {"database": bool(DATABASE_URL), "redis": bool(REDIS_URL)},
    })


def api_status(request):
    return JsonResponse(build_status())


def dashboard(request):
    return HttpResponse(DASHBOARD_HTML, content_type="text/html")
'''

_DJANGO_DOCKERFILE = """\
# syntax=docker/dockerfile:1.7
# Launchpad-generated Django image (gunicorn, non-root USER 10001).
FROM python:3.12-slim AS builder
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M -s /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=10001:10001 . .
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PORT=__PORT__
USER 10001:10001
EXPOSE __PORT__
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:__PORT__/health'); sys.exit(0)" || exit 1
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT} -w 2 config.wsgi:application"]
"""


def _django_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    views = (_PY_CHECKS + _DJANGO_VIEWS_ROUTES).replace(
        "__DASHBOARD__", repr(_BACKEND_DASHBOARD_HTML)
    )
    reqs = ["Django==5.1.4", "gunicorn==23.0.0"] + _py_deps(deps)
    return {
        "manage.py": _DJANGO_MANAGE,
        "config/__init__.py": "",
        "config/settings.py": _DJANGO_SETTINGS,
        "config/urls.py": _DJANGO_URLS,
        "config/wsgi.py": _DJANGO_WSGI,
        "health/__init__.py": "",
        "health/views.py": views,
        "requirements.txt": "\n".join(reqs) + "\n",
        "Dockerfile": _DJANGO_DOCKERFILE.replace("__PORT__", str(port)),
        ".dockerignore": _PY_DOCKERIGNORE,
        "README.md": _app_readme(app_name, "Django", port, deps),
    }


# --------------------------------------------------------------------------- #
# NestJS backend (compiled with tsc)
# --------------------------------------------------------------------------- #

_NEST_MAIN = """\
import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  await app.listen(parseInt(process.env.PORT || '3000', 10), '0.0.0.0');
}
bootstrap();
"""

_NEST_MODULE = """\
import { Module } from '@nestjs/common';
import { AppController } from './app.controller';

@Module({ controllers: [AppController] })
export class AppModule {}
"""

_NEST_CONTROLLER = r"""import { Controller, Get, Header, Res } from '@nestjs/common';
import { Response } from 'express';
import { buildStatus, readinessProblems, appInfo, DASHBOARD_HTML } from './status';

@Controller()
export class AppController {
  @Get('health')
  health() {
    return { status: 'ok', timestamp: new Date().toISOString() };
  }

  @Get('ready')
  async ready(@Res() res: Response) {
    const problems = await readinessProblems();
    res.status(problems.length ? 503 : 200).json({
      status: problems.length ? 'degraded' : 'ready',
      problems,
      timestamp: new Date().toISOString(),
    });
  }

  @Get('info')
  info() {
    return appInfo();
  }

  @Get('api/status')
  async apiStatus() {
    return await buildStatus();
  }

  @Get()
  @Header('Content-Type', 'text/html')
  dashboard() {
    return DASHBOARD_HTML;
  }
}
"""

_NEST_STATUS = r"""/* Shared health + dependency checks for the NestJS service. */
const APP_NAME = process.env.ENVIRONMENT_NAME || 'launchpad-app';
const APP_VERSION = process.env.APP_VERSION || '1.0.0';
const NAMESPACE = process.env.POD_NAMESPACE || 'default';
const POD_NAME = process.env.POD_NAME || require('os').hostname();
const REPLICA_COUNT = process.env.REPLICA_COUNT || '1';
const PORT = parseInt(process.env.PORT || '3000', 10);
const DATABASE_URL =
  process.env.DATABASE_URL || process.env.MYSQL_URL || process.env.MARIADB_URL || process.env.MONGODB_URI || null;
const REDIS_URL = process.env.REDIS_URL || null;
const STARTED_AT = Date.now();

let lastDbSuccess: string | null = null;
let lastRedisSuccess: string | null = null;

export const DASHBOARD_HTML = __DASHBOARD__;

function nowIso() { return new Date().toISOString(); }

export async function checkDatabase(): Promise<any> {
  const url = DATABASE_URL;
  if (!url) return { configured: false, connected: false, error: null, kind: null, lastSuccess: lastDbSuccess };
  try {
    if (url.startsWith('postgres')) {
      const { Client } = require('pg');
      const client = new Client({ connectionString: url, connectionTimeoutMillis: 3000 });
      await client.connect(); await client.query('SELECT 1'); await client.end();
      lastDbSuccess = nowIso();
      return { configured: true, connected: true, error: null, kind: 'postgresql', lastSuccess: lastDbSuccess };
    } else if (url.startsWith('mysql') || url.startsWith('mariadb')) {
      const mysql = require('mysql2/promise');
      const conn = await mysql.createConnection(url.replace(/^mariadb:/, 'mysql:'));
      await conn.query('SELECT 1'); await conn.end();
      lastDbSuccess = nowIso();
      return { configured: true, connected: true, error: null, kind: 'mysql', lastSuccess: lastDbSuccess };
    } else if (url.startsWith('mongodb')) {
      const { MongoClient } = require('mongodb');
      const client = new MongoClient(url, { serverSelectionTimeoutMS: 3000 });
      await client.connect(); await client.db().command({ ping: 1 }); await client.close();
      lastDbSuccess = nowIso();
      return { configured: true, connected: true, error: null, kind: 'mongodb', lastSuccess: lastDbSuccess };
    }
    return { configured: true, connected: false, error: 'unsupported database scheme', kind: null, lastSuccess: lastDbSuccess };
  } catch (err: any) {
    return { configured: true, connected: false, error: String(err.message || err).slice(0, 200), kind: null, lastSuccess: lastDbSuccess };
  }
}

export async function checkRedis(): Promise<any> {
  if (!REDIS_URL) return { configured: false, connected: false, error: null, latencyMs: null, lastSuccess: lastRedisSuccess };
  let client: any;
  try {
    const redis = require('redis');
    client = redis.createClient({ url: REDIS_URL, socket: { connectTimeout: 3000 } });
    client.on('error', () => {});
    await client.connect();
    const start = process.hrtime.bigint();
    await client.ping();
    const latency = Number(process.hrtime.bigint() - start) / 1e6;
    await client.quit();
    lastRedisSuccess = nowIso();
    return { configured: true, connected: true, error: null, latencyMs: Math.round(latency * 100) / 100, lastSuccess: lastRedisSuccess };
  } catch (err: any) {
    try { if (client) await client.disconnect(); } catch (e) {}
    return { configured: true, connected: false, error: String(err.message || err).slice(0, 200), latencyMs: null, lastSuccess: lastRedisSuccess };
  }
}

export async function buildStatus(): Promise<any> {
  const [database, redisStatus] = await Promise.all([checkDatabase(), checkRedis()]);
  return {
    app: { name: APP_NAME, version: APP_VERSION, status: 'healthy', uptimeSeconds: Math.round((Date.now() - STARTED_AT) / 100) / 10 },
    kubernetes: { namespace: NAMESPACE, pod: POD_NAME, replicas: REPLICA_COUNT, deployment: 'app' },
    database,
    redis: redisStatus,
    timestamp: nowIso(),
  };
}

export async function readinessProblems(): Promise<string[]> {
  const status = await buildStatus();
  const problems: string[] = [];
  if (status.database.configured && !status.database.connected) problems.push('database');
  if (status.redis.configured && !status.redis.connected) problems.push('redis');
  return problems;
}

export function appInfo() {
  return {
    name: APP_NAME, version: APP_VERSION, namespace: NAMESPACE, pod: POD_NAME,
    replicas: REPLICA_COUNT, port: PORT,
    dependencies: { database: !!DATABASE_URL, redis: !!REDIS_URL },
  };
}
"""

_NEST_TSCONFIG = """\
{
  "compilerOptions": {
    "module": "commonjs",
    "target": "ES2021",
    "outDir": "./dist",
    "rootDir": "./src",
    "experimentalDecorators": true,
    "emitDecoratorMetadata": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "strict": false,
    "sourceMap": false
  },
  "include": ["src/**/*"]
}
"""

_NEST_DOCKERFILE = """\
# syntax=docker/dockerfile:1.7
# Launchpad-generated NestJS image (multi-stage tsc build, non-root USER 10001).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
RUN apk add --no-cache wget && addgroup -g 10001 -S app && adduser -u 10001 -S -G app app
COPY --from=build --chown=10001:10001 /app/node_modules ./node_modules
COPY --from=build --chown=10001:10001 /app/dist ./dist
COPY --chown=10001:10001 package.json ./
ENV NODE_ENV=production PORT=__PORT__
USER 10001:10001
EXPOSE __PORT__
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:__PORT__/health || exit 1
CMD ["node", "dist/main.js"]
"""


def _nest_package_json(app_name: str, deps: WorkloadDependenciesConfig) -> str:
    dependencies = {
        "@nestjs/common": "^10.4.4",
        "@nestjs/core": "^10.4.4",
        "@nestjs/platform-express": "^10.4.4",
        "reflect-metadata": "^0.2.2",
        "rxjs": "^7.8.1",
    }
    if deps.postgres.enabled:
        dependencies["pg"] = "^8.13.1"
    if deps.mysql.enabled or deps.mariadb.enabled:
        dependencies["mysql2"] = "^3.11.5"
    if deps.mongodb.enabled:
        dependencies["mongodb"] = "^6.12.0"
    if deps.redis.enabled:
        dependencies["redis"] = "^4.7.0"
    payload = {
        "name": app_name,
        "version": APP_VERSION,
        "private": True,
        "scripts": {"build": "tsc -p tsconfig.json", "start": "node dist/main.js"},
        "dependencies": dependencies,
        "devDependencies": {
            "typescript": "^5.6.3",
            "@types/node": "^22.7.0",
            "@types/express": "^5.0.0",
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def _nest_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    status_ts = _NEST_STATUS.replace("__DASHBOARD__", json.dumps(_BACKEND_DASHBOARD_HTML))
    return {
        "package.json": _nest_package_json(app_name, deps),
        "tsconfig.json": _NEST_TSCONFIG,
        "src/main.ts": _NEST_MAIN,
        "src/app.module.ts": _NEST_MODULE,
        "src/app.controller.ts": _NEST_CONTROLLER,
        "src/status.ts": status_ts,
        "Dockerfile": _NEST_DOCKERFILE.replace("__PORT__", str(port)),
        ".dockerignore": _NODE_DOCKERIGNORE + "dist/\n",
        "README.md": _app_readme(app_name, "NestJS", port, deps),
    }


# --------------------------------------------------------------------------- #
# Go backend (net/http)
# --------------------------------------------------------------------------- #

_GO_MAIN = r'''package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	_ "github.com/go-sql-driver/mysql"
	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
	"go.mongodb.org/mongo-driver/mongo/readpref"
)

var (
	appName        = env("ENVIRONMENT_NAME", "launchpad-app")
	appVersion     = env("APP_VERSION", "1.0.0")
	namespace      = env("POD_NAMESPACE", "default")
	podName        = env("POD_NAME", hostname())
	replicaCount   = env("REPLICA_COUNT", "1")
	listenPort     = env("PORT", "8080")
	databaseURL    = firstNonEmpty(os.Getenv("DATABASE_URL"), os.Getenv("MYSQL_URL"), os.Getenv("MARIADB_URL"), os.Getenv("MONGODB_URI"))
	redisURL       = os.Getenv("REDIS_URL")
	startedAt      = time.Now()
	lastDBSuccess  string
	lastRdsSuccess string
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func hostname() string { h, _ := os.Hostname(); return h }

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if v != "" {
			return v
		}
	}
	return ""
}

func nowIso() string { return time.Now().UTC().Format(time.RFC3339) }

func mysqlDSN(raw string) (string, error) {
	raw = strings.Replace(raw, "mariadb://", "mysql://", 1)
	u, err := url.Parse(raw)
	if err != nil {
		return "", err
	}
	pass, _ := u.User.Password()
	host := u.Host
	db := strings.TrimPrefix(u.Path, "/")
	return u.User.Username() + ":" + pass + "@tcp(" + host + ")/" + db + "?timeout=3s", nil
}

func checkDatabase() map[string]interface{} {
	res := map[string]interface{}{"configured": false, "connected": false, "error": nil, "kind": nil, "lastSuccess": nullable(lastDBSuccess)}
	if databaseURL == "" {
		return res
	}
	res["configured"] = true
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	var err error
	var kind string
	switch {
	case strings.HasPrefix(databaseURL, "postgres"):
		var db *sql.DB
		db, err = sql.Open("postgres", databaseURL)
		if err == nil {
			defer db.Close()
			err = db.PingContext(ctx)
		}
		kind = "postgresql"
	case strings.HasPrefix(databaseURL, "mysql"), strings.HasPrefix(databaseURL, "mariadb"):
		var dsn string
		dsn, err = mysqlDSN(databaseURL)
		if err == nil {
			var db *sql.DB
			db, err = sql.Open("mysql", dsn)
			if err == nil {
				defer db.Close()
				err = db.PingContext(ctx)
			}
		}
		kind = "mysql"
	case strings.HasPrefix(databaseURL, "mongodb"):
		var client *mongo.Client
		client, err = mongo.Connect(ctx, options.Client().ApplyURI(databaseURL))
		if err == nil {
			defer client.Disconnect(context.Background())
			err = client.Ping(ctx, readpref.Primary())
		}
		kind = "mongodb"
	default:
		res["error"] = "unsupported database scheme"
		return res
	}
	if err != nil {
		res["error"] = truncate(err.Error())
		return res
	}
	lastDBSuccess = nowIso()
	res["connected"] = true
	res["kind"] = kind
	res["lastSuccess"] = lastDBSuccess
	return res
}

func checkRedis() map[string]interface{} {
	res := map[string]interface{}{"configured": false, "connected": false, "error": nil, "latencyMs": nil, "lastSuccess": nullable(lastRdsSuccess)}
	if redisURL == "" {
		return res
	}
	res["configured"] = true
	opt, err := redis.ParseURL(redisURL)
	if err != nil {
		res["error"] = truncate(err.Error())
		return res
	}
	client := redis.NewClient(opt)
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	start := time.Now()
	if err := client.Ping(ctx).Err(); err != nil {
		res["error"] = truncate(err.Error())
		return res
	}
	lastRdsSuccess = nowIso()
	res["connected"] = true
	res["latencyMs"] = float64(time.Since(start).Microseconds()) / 1000.0
	res["lastSuccess"] = lastRdsSuccess
	return res
}

func truncate(s string) string {
	if len(s) > 200 {
		return s[:200]
	}
	return s
}

func nullable(s string) interface{} {
	if s == "" {
		return nil
	}
	return s
}

func buildStatus() map[string]interface{} {
	return map[string]interface{}{
		"app": map[string]interface{}{
			"name": appName, "version": appVersion, "status": "healthy",
			"uptimeSeconds": time.Since(startedAt).Seconds(),
		},
		"kubernetes": map[string]interface{}{
			"namespace": namespace, "pod": podName, "replicas": replicaCount, "deployment": "app",
		},
		"database":  checkDatabase(),
		"redis":     checkRedis(),
		"timestamp": nowIso(),
	}
}

func writeJSON(w http.ResponseWriter, code int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]string{"status": "ok", "timestamp": nowIso()})
	})
	mux.HandleFunc("/ready", func(w http.ResponseWriter, r *http.Request) {
		status := buildStatus()
		problems := []string{}
		if db, ok := status["database"].(map[string]interface{}); ok && db["configured"] == true && db["connected"] == false {
			problems = append(problems, "database")
		}
		if rd, ok := status["redis"].(map[string]interface{}); ok && rd["configured"] == true && rd["connected"] == false {
			problems = append(problems, "redis")
		}
		code := 200
		state := "ready"
		if len(problems) > 0 {
			code = 503
			state = "degraded"
		}
		writeJSON(w, code, map[string]interface{}{"status": state, "problems": problems, "timestamp": nowIso()})
	})
	mux.HandleFunc("/info", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]interface{}{
			"name": appName, "version": appVersion, "namespace": namespace, "pod": podName,
			"replicas": replicaCount, "port": listenPort,
			"dependencies": map[string]bool{"database": databaseURL != "", "redis": redisURL != ""},
		})
	})
	mux.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, buildStatus())
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html")
		w.Write([]byte(dashboardHTML))
	})
	http.ListenAndServe(":"+listenPort, mux)
}
'''

_GO_DASHBOARD = 'package main\n\nconst dashboardHTML = __DASHBOARD__\n'

_GO_MOD = """\
module launchpad/app

go 1.23

require (
	github.com/go-sql-driver/mysql v1.8.1
	github.com/lib/pq v1.10.9
	github.com/redis/go-redis/v9 v9.7.0
	go.mongodb.org/mongo-driver v1.17.1
)
"""

_GO_DOCKERFILE = """\
# syntax=docker/dockerfile:1.7
# Launchpad-generated Go image (multi-stage, non-root).
FROM golang:1.23-alpine AS build
WORKDIR /src
RUN apk add --no-cache git ca-certificates
ENV GOFLAGS=-mod=mod CGO_ENABLED=0
COPY go.mod ./
COPY . .
RUN go build -trimpath -ldflags="-s -w" -o /out/app .

FROM alpine:3.21 AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache ca-certificates wget
COPY --from=build --chown=10001:10001 /out/app /app/app
ENV PORT=__PORT__
USER 10001:10001
EXPOSE __PORT__
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:__PORT__/health || exit 1
CMD ["/app/app"]
"""

_GO_DOCKERIGNORE = """\
.git/
.gitignore
*.md
/out/
"""


def _go_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    dashboard_go = _GO_DASHBOARD.replace("__DASHBOARD__", _go_raw_string(_BACKEND_DASHBOARD_HTML))
    return {
        "go.mod": _GO_MOD,
        "main.go": _GO_MAIN,
        "dashboard.go": dashboard_go,
        "Dockerfile": _GO_DOCKERFILE.replace("__PORT__", str(port)),
        ".dockerignore": _GO_DOCKERIGNORE,
        "README.md": _app_readme(app_name, "Go", port, deps),
    }


def _go_raw_string(text: str) -> str:
    """Encode text as a Go raw string literal (backtick-delimited)."""
    # Go raw strings cannot contain backticks; the dashboard HTML has none.
    return "`" + text.replace("`", "` + \"`\" + `") + "`"


# --------------------------------------------------------------------------- #
# Spring Boot backend
# --------------------------------------------------------------------------- #

_SPRING_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.3.5</version>
    <relativePath/>
  </parent>
  <groupId>com.launchpad</groupId>
  <artifactId>app</artifactId>
  <version>1.0.0</version>
  <properties>
    <java.version>21</java.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>org.postgresql</groupId>
      <artifactId>postgresql</artifactId>
      <scope>runtime</scope>
    </dependency>
    <dependency>
      <groupId>com.mysql</groupId>
      <artifactId>mysql-connector-j</artifactId>
      <scope>runtime</scope>
    </dependency>
    <dependency>
      <groupId>redis.clients</groupId>
      <artifactId>jedis</artifactId>
    </dependency>
    <dependency>
      <groupId>org.mongodb</groupId>
      <artifactId>mongodb-driver-sync</artifactId>
    </dependency>
  </dependencies>
  <build>
    <finalName>app</finalName>
    <plugins>
      <plugin>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-maven-plugin</artifactId>
      </plugin>
    </plugins>
  </build>
</project>
"""

_SPRING_APP_PROPS = """\
server.port=${PORT:8080}
server.address=0.0.0.0
spring.main.banner-mode=off
"""

_SPRING_APPLICATION = """\
package com.launchpad.app;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class Application {
    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
"""

_SPRING_CONTROLLER = r"""package com.launchpad.app;

import java.net.URI;
import java.sql.Connection;
import java.sql.DriverManager;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class StatusController {

    private static final String APP_NAME = env("ENVIRONMENT_NAME", "launchpad-app");
    private static final String APP_VERSION = env("APP_VERSION", "1.0.0");
    private static final String NAMESPACE = env("POD_NAMESPACE", "default");
    private static final String POD_NAME = env("POD_NAME", "unknown");
    private static final String REPLICA_COUNT = env("REPLICA_COUNT", "1");
    private static final String DATABASE_URL = firstNonEmpty(
            System.getenv("DATABASE_URL"), System.getenv("MYSQL_URL"),
            System.getenv("MARIADB_URL"), System.getenv("MONGODB_URI"));
    private static final String REDIS_URL = System.getenv("REDIS_URL");
    private static final long STARTED_AT = System.currentTimeMillis();

    private static volatile String lastDbSuccess = null;
    private static volatile String lastRedisSuccess = null;

    private static String env(String k, String d) {
        String v = System.getenv(k);
        return (v == null || v.isEmpty()) ? d : v;
    }

    private static String firstNonEmpty(String... values) {
        for (String v : values) {
            if (v != null && !v.isEmpty()) return v;
        }
        return null;
    }

    private static String nowIso() { return Instant.now().toString(); }

    private Map<String, Object> checkDatabase() {
        Map<String, Object> r = new HashMap<>();
        r.put("configured", false); r.put("connected", false);
        r.put("error", null); r.put("kind", null); r.put("lastSuccess", lastDbSuccess);
        if (DATABASE_URL == null) return r;
        r.put("configured", true);
        try {
            if (DATABASE_URL.startsWith("postgres")) {
                URI u = URI.create(DATABASE_URL.replaceFirst("postgres(ql)?://", "http://"));
                String jdbc = "jdbc:postgresql://" + u.getHost() + ":" + (u.getPort() < 0 ? 5432 : u.getPort()) + u.getPath();
                DriverManager.setLoginTimeout(3);
                try (Connection c = DriverManager.getConnection(jdbc, userOf(u), passOf(u))) {
                    c.isValid(3);
                }
                r.put("kind", "postgresql");
            } else if (DATABASE_URL.startsWith("mysql") || DATABASE_URL.startsWith("mariadb")) {
                URI u = URI.create(DATABASE_URL.replaceFirst("(mysql|mariadb)://", "http://"));
                String jdbc = "jdbc:mysql://" + u.getHost() + ":" + (u.getPort() < 0 ? 3306 : u.getPort()) + u.getPath() + "?connectTimeout=3000";
                DriverManager.setLoginTimeout(3);
                try (Connection c = DriverManager.getConnection(jdbc, userOf(u), passOf(u))) {
                    c.isValid(3);
                }
                r.put("kind", "mysql");
            } else if (DATABASE_URL.startsWith("mongodb")) {
                try (com.mongodb.client.MongoClient client = com.mongodb.client.MongoClients.create(DATABASE_URL)) {
                    client.getDatabase("admin").runCommand(new org.bson.Document("ping", 1));
                }
                r.put("kind", "mongodb");
            } else {
                r.put("error", "unsupported database scheme");
                return r;
            }
            lastDbSuccess = nowIso();
            r.put("connected", true);
            r.put("lastSuccess", lastDbSuccess);
        } catch (Exception e) {
            r.put("error", trunc(e.getMessage()));
        }
        return r;
    }

    private Map<String, Object> checkRedis() {
        Map<String, Object> r = new HashMap<>();
        r.put("configured", false); r.put("connected", false);
        r.put("error", null); r.put("latencyMs", null); r.put("lastSuccess", lastRedisSuccess);
        if (REDIS_URL == null) return r;
        r.put("configured", true);
        try {
            URI u = URI.create(REDIS_URL);
            int port = u.getPort() < 0 ? 6379 : u.getPort();
            long start = System.nanoTime();
            try (redis.clients.jedis.Jedis jedis = new redis.clients.jedis.Jedis(u.getHost(), port, 3000)) {
                jedis.ping();
            }
            double latency = (System.nanoTime() - start) / 1_000_000.0;
            lastRedisSuccess = nowIso();
            r.put("connected", true);
            r.put("latencyMs", Math.round(latency * 100.0) / 100.0);
            r.put("lastSuccess", lastRedisSuccess);
        } catch (Exception e) {
            r.put("error", trunc(e.getMessage()));
        }
        return r;
    }

    private static String userOf(URI u) {
        String ui = u.getUserInfo();
        return ui == null ? null : ui.split(":", 2)[0];
    }

    private static String passOf(URI u) {
        String ui = u.getUserInfo();
        String[] parts = ui == null ? new String[0] : ui.split(":", 2);
        return parts.length > 1 ? parts[1] : null;
    }

    private static String trunc(String s) {
        if (s == null) return "error";
        return s.length() > 200 ? s.substring(0, 200) : s;
    }

    private Map<String, Object> buildStatus() {
        Map<String, Object> app = new HashMap<>();
        app.put("name", APP_NAME); app.put("version", APP_VERSION); app.put("status", "healthy");
        app.put("uptimeSeconds", (System.currentTimeMillis() - STARTED_AT) / 1000.0);
        Map<String, Object> k8s = new HashMap<>();
        k8s.put("namespace", NAMESPACE); k8s.put("pod", POD_NAME);
        k8s.put("replicas", REPLICA_COUNT); k8s.put("deployment", "app");
        Map<String, Object> out = new HashMap<>();
        out.put("app", app); out.put("kubernetes", k8s);
        out.put("database", checkDatabase()); out.put("redis", checkRedis());
        out.put("timestamp", nowIso());
        return out;
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        Map<String, Object> m = new HashMap<>();
        m.put("status", "ok"); m.put("timestamp", nowIso());
        return m;
    }

    @GetMapping("/ready")
    public ResponseEntity<Map<String, Object>> ready() {
        Map<String, Object> status = buildStatus();
        List<String> problems = new java.util.ArrayList<>();
        Map<?, ?> db = (Map<?, ?>) status.get("database");
        Map<?, ?> rd = (Map<?, ?>) status.get("redis");
        if (Boolean.TRUE.equals(db.get("configured")) && Boolean.FALSE.equals(db.get("connected"))) problems.add("database");
        if (Boolean.TRUE.equals(rd.get("configured")) && Boolean.FALSE.equals(rd.get("connected"))) problems.add("redis");
        Map<String, Object> body = new HashMap<>();
        body.put("status", problems.isEmpty() ? "ready" : "degraded");
        body.put("problems", problems); body.put("timestamp", nowIso());
        return ResponseEntity.status(problems.isEmpty() ? 200 : 503).body(body);
    }

    @GetMapping("/info")
    public Map<String, Object> info() {
        Map<String, Object> deps = new HashMap<>();
        deps.put("database", DATABASE_URL != null); deps.put("redis", REDIS_URL != null);
        Map<String, Object> m = new HashMap<>();
        m.put("name", APP_NAME); m.put("version", APP_VERSION); m.put("namespace", NAMESPACE);
        m.put("pod", POD_NAME); m.put("replicas", REPLICA_COUNT); m.put("dependencies", deps);
        return m;
    }

    @GetMapping("/api/status")
    public Map<String, Object> apiStatus() {
        return buildStatus();
    }
}
"""

_SPRING_DOCKERFILE = """\
# syntax=docker/dockerfile:1.7
# Launchpad-generated Spring Boot image (multi-stage Maven build, non-root).
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml ./
RUN mvn -q -DskipTests dependency:go-offline || true
COPY . .
RUN mvn -q -DskipTests package

FROM eclipse-temurin:21-jre-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S -G app app \\
  && apk add --no-cache wget
COPY --from=build --chown=10001:10001 /src/target/app.jar /app/app.jar
ENV PORT=__PORT__
USER 10001:10001
EXPOSE __PORT__
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:__PORT__/health || exit 1
CMD ["java", "-jar", "/app/app.jar"]
"""


def _springboot_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    return {
        "pom.xml": _SPRING_POM,
        "src/main/resources/application.properties": _SPRING_APP_PROPS,
        "src/main/resources/static/index.html": _BACKEND_DASHBOARD_HTML,
        "src/main/java/com/launchpad/app/Application.java": _SPRING_APPLICATION,
        "src/main/java/com/launchpad/app/StatusController.java": _SPRING_CONTROLLER,
        "Dockerfile": _SPRING_DOCKERFILE.replace("__PORT__", str(port)),
        ".dockerignore": "target/\n.git/\n.gitignore\n*.md\n",
        "README.md": _app_readme(app_name, "Spring Boot", port, deps),
    }


# --------------------------------------------------------------------------- #
# .NET backend (ASP.NET Core minimal API)
# --------------------------------------------------------------------------- #

_DOTNET_CSPROJ = """\
<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <AssemblyName>app</AssemblyName>
    <RootNamespace>LaunchpadApp</RootNamespace>
    <InvariantGlobalization>true</InvariantGlobalization>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Npgsql" Version="8.0.5" />
    <PackageReference Include="MySqlConnector" Version="2.3.7" />
    <PackageReference Include="StackExchange.Redis" Version="2.8.16" />
    <PackageReference Include="MongoDB.Driver" Version="2.28.0" />
  </ItemGroup>
</Project>
"""

_DOTNET_PROGRAM = r"""using System.Diagnostics;
using MongoDB.Driver;
using MySqlConnector;
using Npgsql;
using StackExchange.Redis;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.UseDefaultFiles();
app.UseStaticFiles();

string AppName = Environment.GetEnvironmentVariable("ENVIRONMENT_NAME") ?? "launchpad-app";
string AppVersion = Environment.GetEnvironmentVariable("APP_VERSION") ?? "1.0.0";
string Namespace = Environment.GetEnvironmentVariable("POD_NAMESPACE") ?? "default";
string PodName = Environment.GetEnvironmentVariable("POD_NAME") ?? Environment.MachineName;
string ReplicaCount = Environment.GetEnvironmentVariable("REPLICA_COUNT") ?? "1";
string? DatabaseUrl = Environment.GetEnvironmentVariable("DATABASE_URL")
    ?? Environment.GetEnvironmentVariable("MYSQL_URL")
    ?? Environment.GetEnvironmentVariable("MARIADB_URL")
    ?? Environment.GetEnvironmentVariable("MONGODB_URI");
string? RedisUrl = Environment.GetEnvironmentVariable("REDIS_URL");
var startedAt = DateTime.UtcNow;
string? lastDbSuccess = null;
string? lastRedisSuccess = null;

string NowIso() => DateTime.UtcNow.ToString("o");

async Task<Dictionary<string, object?>> CheckDatabase()
{
    var r = new Dictionary<string, object?> {
        ["configured"] = false, ["connected"] = false, ["error"] = null, ["kind"] = null, ["lastSuccess"] = lastDbSuccess };
    if (string.IsNullOrEmpty(DatabaseUrl)) return r;
    r["configured"] = true;
    try
    {
        if (DatabaseUrl.StartsWith("postgres"))
        {
            var uri = new Uri(DatabaseUrl);
            var ui = uri.UserInfo.Split(':', 2);
            var cs = $"Host={uri.Host};Port={(uri.Port < 0 ? 5432 : uri.Port)};Username={ui[0]};Password={(ui.Length > 1 ? ui[1] : "")};Database={uri.AbsolutePath.TrimStart('/')};Timeout=3";
            await using var conn = new NpgsqlConnection(cs);
            await conn.OpenAsync();
            r["kind"] = "postgresql";
        }
        else if (DatabaseUrl.StartsWith("mysql") || DatabaseUrl.StartsWith("mariadb"))
        {
            var uri = new Uri(DatabaseUrl.Replace("mariadb://", "mysql://"));
            var ui = uri.UserInfo.Split(':', 2);
            var cs = $"Server={uri.Host};Port={(uri.Port < 0 ? 3306 : uri.Port)};User ID={ui[0]};Password={(ui.Length > 1 ? ui[1] : "")};Database={uri.AbsolutePath.TrimStart('/')};ConnectionTimeout=3";
            await using var conn = new MySqlConnection(cs);
            await conn.OpenAsync();
            r["kind"] = "mysql";
        }
        else if (DatabaseUrl.StartsWith("mongodb"))
        {
            var client = new MongoClient(DatabaseUrl);
            await client.GetDatabase("admin").RunCommandAsync<MongoDB.Bson.BsonDocument>(new MongoDB.Bson.BsonDocument("ping", 1));
            r["kind"] = "mongodb";
        }
        else
        {
            r["error"] = "unsupported database scheme";
            return r;
        }
        lastDbSuccess = NowIso();
        r["connected"] = true;
        r["lastSuccess"] = lastDbSuccess;
    }
    catch (Exception e)
    {
        r["error"] = e.Message.Length > 200 ? e.Message[..200] : e.Message;
    }
    return r;
}

async Task<Dictionary<string, object?>> CheckRedis()
{
    var r = new Dictionary<string, object?> {
        ["configured"] = false, ["connected"] = false, ["error"] = null, ["latencyMs"] = null, ["lastSuccess"] = lastRedisSuccess };
    if (string.IsNullOrEmpty(RedisUrl)) return r;
    r["configured"] = true;
    try
    {
        var uri = new Uri(RedisUrl);
        var cfg = new ConfigurationOptions {
            EndPoints = { { uri.Host, uri.Port < 0 ? 6379 : uri.Port } },
            ConnectTimeout = 3000, AbortOnConnectFail = false };
        using var mux = await ConnectionMultiplexer.ConnectAsync(cfg);
        var sw = Stopwatch.StartNew();
        await mux.GetDatabase().PingAsync();
        sw.Stop();
        lastRedisSuccess = NowIso();
        r["connected"] = true;
        r["latencyMs"] = Math.Round(sw.Elapsed.TotalMilliseconds, 2);
        r["lastSuccess"] = lastRedisSuccess;
    }
    catch (Exception e)
    {
        r["error"] = e.Message.Length > 200 ? e.Message[..200] : e.Message;
    }
    return r;
}

async Task<Dictionary<string, object?>> BuildStatus() => new()
{
    ["app"] = new Dictionary<string, object?> {
        ["name"] = AppName, ["version"] = AppVersion, ["status"] = "healthy",
        ["uptimeSeconds"] = Math.Round((DateTime.UtcNow - startedAt).TotalSeconds, 1) },
    ["kubernetes"] = new Dictionary<string, object?> {
        ["namespace"] = Namespace, ["pod"] = PodName, ["replicas"] = ReplicaCount, ["deployment"] = "app" },
    ["database"] = await CheckDatabase(),
    ["redis"] = await CheckRedis(),
    ["timestamp"] = NowIso(),
};

app.MapGet("/health", () => Results.Json(new { status = "ok", timestamp = NowIso() }));

app.MapGet("/ready", async () =>
{
    var status = await BuildStatus();
    var problems = new List<string>();
    var db = (Dictionary<string, object?>)status["database"]!;
    var rd = (Dictionary<string, object?>)status["redis"]!;
    if ((bool)db["configured"]! && !(bool)db["connected"]!) problems.Add("database");
    if ((bool)rd["configured"]! && !(bool)rd["connected"]!) problems.Add("redis");
    var body = new { status = problems.Count == 0 ? "ready" : "degraded", problems, timestamp = NowIso() };
    return Results.Json(body, statusCode: problems.Count == 0 ? 200 : 503);
});

app.MapGet("/info", () => Results.Json(new {
    name = AppName, version = AppVersion, @namespace = Namespace, pod = PodName,
    replicas = ReplicaCount,
    dependencies = new { database = !string.IsNullOrEmpty(DatabaseUrl), redis = !string.IsNullOrEmpty(RedisUrl) } }));

app.MapGet("/api/status", async () => Results.Json(await BuildStatus()));

app.Run();
"""

_DOTNET_DOCKERFILE = """\
# syntax=docker/dockerfile:1.7
# Launchpad-generated .NET (ASP.NET Core) image (multi-stage, non-root).
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY *.csproj ./
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \\
  && rm -rf /var/lib/apt/lists/*
COPY --from=build /app ./
ENV ASPNETCORE_URLS=http://0.0.0.0:__PORT__ PORT=__PORT__
USER $APP_UID
EXPOSE __PORT__
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \\
  CMD curl -fsS http://127.0.0.1:__PORT__/health || exit 1
CMD ["dotnet", "app.dll"]
"""


def _dotnet_files(app_name: str, port: int, deps: WorkloadDependenciesConfig) -> dict[str, str]:
    return {
        "app.csproj": _DOTNET_CSPROJ,
        "Program.cs": _DOTNET_PROGRAM,
        "wwwroot/index.html": _BACKEND_DASHBOARD_HTML,
        "Dockerfile": _DOTNET_DOCKERFILE.replace("__PORT__", str(port)),
        ".dockerignore": "bin/\nobj/\n.git/\n.gitignore\n*.md\n",
        "README.md": _app_readme(app_name, ".NET", port, deps),
    }


# --------------------------------------------------------------------------- #
# Stack -> file-builder dispatch
# --------------------------------------------------------------------------- #

_STACK_BUILDERS = {
    # static frontends
    ProjectStack.REACT_VITE: _react_files,
    ProjectStack.VUEJS: _vue_files,
    ProjectStack.SVELTE: _svelte_files,
    ProjectStack.ANGULAR: _angular_files,
    # SSR frontends
    ProjectStack.NEXTJS: _next_files,
    ProjectStack.NUXTJS: _nuxt_files,
    # backends
    ProjectStack.FASTAPI: _fastapi_files,
    ProjectStack.FLASK: _flask_files,
    ProjectStack.DJANGO: _django_files,
    ProjectStack.EXPRESS: _node_files,
    ProjectStack.NODE: _node_files,
    ProjectStack.NESTJS: _nest_files,
    ProjectStack.GO: _go_files,
    ProjectStack.SPRINGBOOT: _springboot_files,
    ProjectStack.JAVA: _springboot_files,
    ProjectStack.DOTNET: _dotnet_files,
}

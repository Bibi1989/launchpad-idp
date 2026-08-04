"""Tests for the workspace/preview lifecycle fixes.

Covers: postgres subPath/PGDATA/fsGroup (#1), dynamic Service/Ingress port
alignment (#2), and the readiness-wait 3-minute cap + crash-loop fast-fail (#3).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import yaml

from app.schemas.cloud import (
    DataStoreDependency,
    KubernetesPackaging,
    KubernetesWorkloadOptions,
    WorkloadDependenciesConfig,
)
from app.schemas.dockerfile_schema import ProjectStack
from app.services.app_scaffold import CoreScaffold
from app.services.k8s_bundle import write_kubernetes_layout
from app.services.kubernetes import (
    PREVIEW_READY_TIMEOUT_CAP_SECONDS,
    KubernetesProvisioner,
    _workload_ready_timeout_seconds,
)


# --------------------------------------------------------------------------- #
# #1 PostgreSQL init fix
# --------------------------------------------------------------------------- #


def _render(deps: WorkloadDependenciesConfig, *, workload=None) -> Path:
    tmp = Path(tempfile.mkdtemp())
    write_kubernetes_layout(
        tmp,
        name="demo",
        packaging=KubernetesPackaging.RAW_MANIFESTS,
        options=KubernetesWorkloadOptions(service=True, ingress=True, config_map=True, secret=True),
        dependencies=deps,
        workload=workload,
    )
    return tmp / "infra/k8s/manifests"


def test_postgres_has_pgdata_subpath_and_fsgroup() -> None:
    m = _render(WorkloadDependenciesConfig(postgres=DataStoreDependency(enabled=True)))
    pg = yaml.safe_load((m / "postgres-deployment.yaml").read_text())
    pod = pg["spec"]["template"]["spec"]
    container = pod["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"] if "value" in e}

    assert env["PGDATA"] == "/var/lib/postgresql/data/pgdata"
    assert container["volumeMounts"][0]["subPath"] == "pgdata"
    assert container["volumeMounts"][0]["mountPath"] == "/var/lib/postgresql/data"
    assert pod["securityContext"]["fsGroup"] == 999
    # Mandatory env always populated.
    assert env["POSTGRES_USER"] == "launchpad"
    assert "POSTGRES_DB" in env
    pw = next(e for e in container["env"] if e["name"] == "POSTGRES_PASSWORD")
    assert pw["valueFrom"]["secretKeyRef"]["key"] == "POSTGRES_PASSWORD"


# --------------------------------------------------------------------------- #
# #2 Dynamic Service / Ingress port alignment
# --------------------------------------------------------------------------- #


def test_service_and_ingress_ports_match_app_container_port() -> None:
    deps = WorkloadDependenciesConfig()
    spec = CoreScaffold(stack=ProjectStack.FASTAPI, app_name="api", port=8000, dependencies=deps).image_spec()
    m = _render(deps, workload=spec)
    svc = yaml.safe_load((m / "service.yaml").read_text())
    ing = yaml.safe_load((m / "ingress.yaml").read_text())
    assert svc["spec"]["ports"][0]["port"] == 8000
    assert ing["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["port"] == {"number": 8000}


def test_nginx_default_service_port_unchanged() -> None:
    # No workload spec -> legacy nginx defaults (port 80) preserved.
    m = _render(WorkloadDependenciesConfig())
    svc = yaml.safe_load((m / "service.yaml").read_text())
    ing = yaml.safe_load((m / "ingress.yaml").read_text())
    assert svc["spec"]["ports"][0]["port"] == 80
    assert ing["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["port"] == {"number": 80}


# --------------------------------------------------------------------------- #
# #3 Readiness wait: 3-minute cap + crash-loop fast-fail
# --------------------------------------------------------------------------- #


def test_ready_timeout_capped_at_three_minutes() -> None:
    assert PREVIEW_READY_TIMEOUT_CAP_SECONDS == 180.0
    # Non-nginx app image would otherwise get 240s - now capped at 180.
    assert _workload_ready_timeout_seconds(image="api:latest", base_timeout_seconds=240.0) == 180.0
    # A configured base above the cap is still clamped.
    assert _workload_ready_timeout_seconds(image="nginx:1.27-alpine", base_timeout_seconds=300.0) == 180.0
    # A small nginx base is respected.
    assert _workload_ready_timeout_seconds(image="nginx:1.27-alpine", base_timeout_seconds=120.0) == 120.0


def _fake_pod(*, name: str, container: str, reason: str, restarts: int, term_reason=None, exit_code=1):
    waiting = SimpleNamespace(reason=reason, message=f"{reason} detail")
    terminated = (
        SimpleNamespace(reason=term_reason, exit_code=exit_code, message=None)
        if term_reason
        else None
    )
    status = SimpleNamespace(
        name=container,
        restart_count=restarts,
        state=SimpleNamespace(waiting=waiting),
        last_state=SimpleNamespace(terminated=terminated),
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(container_statuses=[status]),
    )


class _FakeCore:
    def __init__(self, pods):
        self._pods = pods

    def list_namespaced_pod(self, namespace, label_selector=None):
        return SimpleNamespace(items=self._pods)


def _provisioner_with_pods(pods) -> KubernetesProvisioner:
    prov = KubernetesProvisioner.__new__(KubernetesProvisioner)
    prov._core = _FakeCore(pods)  # type: ignore[attr-defined]
    return prov


def test_crash_loop_backoff_detected_after_restarts() -> None:
    prov = _provisioner_with_pods([
        _fake_pod(name="postgres-abc", container="postgres", reason="CrashLoopBackOff",
                  restarts=3, term_reason="Error", exit_code=1),
    ])
    err = prov._first_pod_crash_error(namespace="lp-demo")
    assert err is not None
    assert "crash-looping" in err
    assert "postgres" in err


def test_single_restart_not_flagged() -> None:
    # One restart during slow init must NOT fast-fail.
    prov = _provisioner_with_pods([
        _fake_pod(name="app-abc", container="app", reason="CrashLoopBackOff", restarts=1),
    ])
    assert prov._first_pod_crash_error(namespace="lp-demo") is None


def test_create_container_config_error_fails_immediately() -> None:
    prov = _provisioner_with_pods([
        _fake_pod(name="app-abc", container="app", reason="CreateContainerConfigError", restarts=0),
    ])
    err = prov._first_pod_crash_error(namespace="lp-demo")
    assert err is not None
    assert "failed to start" in err


def test_healthy_pod_returns_none() -> None:
    prov = _provisioner_with_pods([
        _fake_pod(name="app-abc", container="app", reason="ContainerCreating", restarts=0),
    ])
    assert prov._first_pod_crash_error(namespace="lp-demo") is None

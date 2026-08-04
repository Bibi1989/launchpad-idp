#!/usr/bin/env python3
"""Reconcile Launch Preview NodePort selectors so previews are reachable.

Background: a multi-stack (fullstack) workspace deploys per-stack ``launch-*``
Deployments whose pods are labelled ``app: nextjs`` / ``app: fastapi`` etc. — never
``app: app``. Older provisioning code created the preview ``app`` NodePort Service
with a hardcoded ``app: app`` selector, so it had no endpoints and the browser hit
ERR_CONNECTION_RESET.

The fix lives in ``manifest_deploy._assign_node_port`` (selects the exposed
preview-target workload), but it only applies to previews created *after* the
Celery worker is restarted. This script repairs previews that already exist:
for every ``launchpad-env-*`` namespace whose ``app`` NodePort Service has no
endpoints, it re-points the Service selector (and targetPort) at the exposed
workload — the ``launchpad.io/preview-target: "true"`` Deployment, else ``app``,
else the first non-datastore Deployment.

Usage:
    python scripts/reconcile-preview-nodeports.py [--context kind-launchpad] [--dry-run]

Requires ``kubectl`` on PATH.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

_DATASTORES = {"postgres", "mysql", "mariadb", "mongodb", "redis"}


def kubectl(ctx: str, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = ["kubectl"]
    if ctx:
        cmd += ["--context", ctx]
    return subprocess.run([*cmd, *args], capture_output=True, text=True)


def _is_datastore(dep: dict) -> bool:
    name = dep.get("metadata", {}).get("name", "").lower()
    if name in _DATASTORES:
        return True
    comp = (dep.get("metadata", {}).get("labels") or {}).get("launchpad.io/component", "")
    return str(comp).lower() == "datastore"


def _pod_app_and_port(dep: dict) -> tuple[str | None, int | None]:
    tmpl = dep.get("spec", {}).get("template", {})
    app = (tmpl.get("metadata", {}).get("labels") or {}).get("app")
    conts = tmpl.get("spec", {}).get("containers") or []
    port = None
    if conts and conts[0].get("ports"):
        port = conts[0]["ports"][0].get("containerPort")
    return app, port


def _resolve_exposed(deps: list[dict]) -> dict | None:
    app_deps = [d for d in deps if not _is_datastore(d)]
    for d in app_deps:
        ann = d.get("metadata", {}).get("annotations") or {}
        if str(ann.get("launchpad.io/preview-target", "")).lower() == "true":
            return d
    for d in app_deps:
        if d.get("metadata", {}).get("name") == "app":
            return d
    return app_deps[0] if app_deps else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", default="", help="kubectl context (default: current)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    ctx = args.context

    ns_out = kubectl(ctx, "get", "ns", "-o", "jsonpath={.items[*].metadata.name}")
    if ns_out.returncode != 0:
        print(f"kubectl error: {ns_out.stderr.strip()}", file=sys.stderr)
        return 1
    envs = [n for n in ns_out.stdout.split() if n.startswith("launchpad-env-")]

    fixed = skipped = 0
    for ns in envs:
        svc = kubectl(ctx, "get", "svc", "app", "-n", ns, "-o", "json")
        if svc.returncode != 0:
            continue
        s = json.loads(svc.stdout)
        if s.get("spec", {}).get("type") != "NodePort":
            continue
        eps = kubectl(
            ctx, "get", "endpoints", "app", "-n", ns,
            "-o", "jsonpath={.subsets[*].addresses[*].ip}",
        ).stdout.strip()
        if eps:
            skipped += 1
            continue  # already reachable

        deps_out = kubectl(ctx, "get", "deploy", "-n", ns, "-o", "json")
        deps = json.loads(deps_out.stdout or '{"items":[]}').get("items", [])
        exposed = _resolve_exposed(deps)
        if not exposed:
            print(f"[skip] {ns}: no app deployment")
            continue
        app_label, port = _pod_app_and_port(exposed)
        if not app_label:
            print(f"[skip] {ns}: exposed deployment has no app label")
            continue

        node_port = s["spec"]["ports"][0]["nodePort"]
        patch = {"spec": {"selector": {"app": app_label, "launchpad.io/managed-by": "launchpad-idp"}}}
        if port:
            patch["spec"]["ports"] = [{
                "name": "http", "port": 80, "targetPort": port,
                "nodePort": node_port, "protocol": "TCP",
            }]
        exposed_name = exposed["metadata"]["name"]
        print(f"[fix ] {ns}: app -> selector app:{app_label} (from {exposed_name}), "
              f"targetPort {port}, nodePort {node_port}")
        if not args.dry_run:
            r = kubectl(ctx, "patch", "svc", "app", "-n", ns, "--type", "merge", "-p", json.dumps(patch))
            if r.returncode != 0:
                print(f"       patch failed: {r.stderr.strip()[:200]}", file=sys.stderr)
                continue
        fixed += 1

    print(f"\nReconciled {fixed} preview(s); {skipped} already reachable."
          + (" (dry-run)" if args.dry_run else ""))
    print("Durable fix: restart the Celery provisioning worker so new previews "
          "bind the NodePort to the exposed workload automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

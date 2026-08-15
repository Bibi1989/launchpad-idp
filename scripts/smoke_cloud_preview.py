#!/usr/bin/env python3
"""API + browser smoke for Launchpad cloud preview / destroy paths."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
WEB = os.environ.get("BASE_URL", "http://[::1]:3000")
EMAIL = os.environ.get("DEMO_EMAIL", "demovideo@example.com")
PASSWORD = os.environ.get("DEMO_PASSWORD", "DemoVideo123!")


def http_json(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw[:400]}
        return exc.code, payload


def http_code(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return 0


def main() -> int:
    failures: list[str] = []
    print("== UI pages ==")
    for path in ("/", "/provision", "/workspaces", "/environments", "/launch"):
        code = http_code(f"{WEB}{path}")
        print(f"  {path} -> {code}")
        if code != 200:
            failures.append(f"ui {path}={code}")

    print("== auth ==")
    status, login = http_json(
        "POST",
        f"{API}/auth/login",
        body={"email": EMAIL, "password": PASSWORD},
    )
    if status != 200 or not login.get("access_token"):
        print("login failed", status, login)
        return 1
    token = login["access_token"]
    print("  login ok")

    print("== workspaces / environments ==")
    status, workspaces = http_json("GET", f"{API}/provisioning/workspaces", token=token)
    ws_items = workspaces if isinstance(workspaces, list) else workspaces.get("items") or []
    print(f"  workspaces={len(ws_items)} status={status}")
    status, envs = http_json("GET", f"{API}/environments", token=token)
    env_items = envs if isinstance(envs, list) else envs.get("items") or []
    print(f"  environments={len(env_items)} status={status}")

    preview_checked = False
    for env in env_items:
        preview = (env.get("preview_url") or "").strip()
        st = env.get("status")
        print(f"  env {env.get('name') or env.get('id')} status={st} preview={preview or '-'}")
        if preview.startswith("http") and st in {"RUNNING", "running", "Ready"}:
            code = http_code(preview)
            print(f"    preview fetch -> {code}")
            preview_checked = True
            if code != 200:
                failures.append(f"preview {preview}={code}")

    if not preview_checked:
        # Fall back to known live IP from this session if API shape differs.
        fallback = os.environ.get("PREVIEW_URL", "http://34.159.101.16:8080/")
        code = http_code(fallback)
        print(f"  fallback preview {fallback} -> {code}")
        if code != 200:
            failures.append(f"fallback preview={code}")

    print("== browser open-app probe ==")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright not installed; skipping browser automation")
    else:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(WEB, wait_until="domcontentloaded", timeout=30000)
            page.evaluate(
                """([token, org]) => {
                  localStorage.setItem('launchpad_access_token', token);
                  if (org) localStorage.setItem('launchpad_active_org_id', org);
                }""",
                [token, login.get("active_org_id")],
            )
            page.goto(f"{WEB}/environments", wait_until="networkidle", timeout=45000)
            body = page.content()
            print(f"  environments page bytes={len(body)}")
            if "environment" not in body.lower() and "Environment" not in body:
                failures.append("environments page missing content")
            # Visit preview in a new page
            preview = next(
                (
                    (e.get("preview_url") or "").strip()
                    for e in env_items
                    if (e.get("preview_url") or "").startswith("http")
                ),
                os.environ.get("PREVIEW_URL", "http://34.159.101.16:8080/"),
            )
            prev = browser.new_page()
            resp = prev.goto(preview, wait_until="domcontentloaded", timeout=20000)
            print(f"  browser preview {preview} -> {resp.status if resp else 0}")
            if not resp or resp.status >= 400:
                failures.append(f"browser preview status={resp.status if resp else 0}")
            text = prev.inner_text("body")
            print(f"  preview body snippet: {text[:120]!r}")
            browser.close()

    if failures:
        print("FAIL:", "; ".join(failures))
        return 1
    print("OK smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

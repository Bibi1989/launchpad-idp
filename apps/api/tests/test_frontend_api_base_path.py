"""Frontend->backend API base is the backend BASE (no /api) unless a path is set.

The backend does not live under ``/api``; the operator sets an optional path on the
frontend->backend connector. Blank = base URL only.
"""

from __future__ import annotations

from app.services.manifest_deploy import (
    _nginx_api_proxy_conf,
    frontend_api_env_from_backend_url,
    normalize_api_path,
    same_origin_frontend_api_env,
)
from app.services.service_connection_env import frontend_api_path_from_snapshot


def test_normalize_api_path() -> None:
    assert normalize_api_path("") == ""
    assert normalize_api_path(None) == ""
    assert normalize_api_path("/") == ""
    assert normalize_api_path("api") == "/api"
    assert normalize_api_path("/api/") == "/api"
    assert normalize_api_path("v1/graphql") == "/v1/graphql"


def test_same_origin_env_default_is_base() -> None:
    env = same_origin_frontend_api_env("")
    assert set(env.values()) == {""}  # base URL, not /api
    assert env["VITE_API_URL"] == ""
    assert env["NEXT_PUBLIC_API_URL"] == ""


def test_same_origin_env_with_path() -> None:
    env = same_origin_frontend_api_env("api")
    assert env["VITE_API_URL"] == "/api"


def test_connector_api_path_from_snapshot() -> None:
    # No path set -> base ("").
    assert frontend_api_path_from_snapshot(
        {"service_connections": [{"source": "web", "target": "api", "kind": "service"}]}
    ) == ""
    # Explicit path is normalized.
    assert frontend_api_path_from_snapshot(
        {"service_connections": [{"source": "web", "target": "api", "kind": "service", "api_path": "v1"}]}
    ) == "/v1"
    # CORS connectors are ignored for the API path.
    assert frontend_api_path_from_snapshot(
        {"service_connections": [{"source": "web", "target": "api", "kind": "cors", "api_path": "/nope"}]}
    ) == ""


def test_nginx_conf_base_is_catch_all_proxy() -> None:
    conf = _nginx_api_proxy_conf(backend_host="launch-test-backend", backend_port=3333, api_path="")
    # Static-first, then proxy everything else to the backend (no /api prefix).
    assert "try_files $uri $uri/ @backend" in conf
    assert "location @backend" in conf
    assert "http://launch-test-backend:3333" in conf
    assert "location /api/" not in conf


def test_nginx_conf_with_path_uses_prefix() -> None:
    conf = _nginx_api_proxy_conf(backend_host="be", backend_port=3333, api_path="/api")
    assert "location /api/" in conf
    assert "try_files $uri $uri/ /index.html" in conf


def test_same_origin_env_with_full_backend_url() -> None:
    env = same_origin_frontend_api_env("http://backend:8080")
    assert env["VITE_API_URL"] == "http://backend:8080"
    assert env["NEXT_PUBLIC_API_URL"] == "http://backend:8080"


def test_frontend_api_env_from_backend_url() -> None:
    env = frontend_api_env_from_backend_url("http://backend-svc:8000/api")
    assert env["VITE_API_URL"] == "http://backend-svc:8000/api"
    assert env["NEXT_PUBLIC_API_URL"] == "http://backend-svc:8000/api"
    assert frontend_api_env_from_backend_url(None) == {}



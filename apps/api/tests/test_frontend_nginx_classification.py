"""A Node/Vite frontend image must NOT be given an ``exec nginx`` command override.

Regression for CrashLoopBackOff exit 127 ("nginx: not found"): the nginx /api proxy
+ command override was applied to a frontend named ``*-frontend`` on port 8080 even
though its built image ran a Node entrypoint (no nginx). The image config is now
authoritative when inspectable.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services import manifest_deploy as md

_NODE_CFG = '["docker-entrypoint.sh"]|["sh","-c","if [ -f server.js ]; then exec node server.js; else exec npm start; fi"]'
_NGINX_CFG = '["/docker-entrypoint.sh"]|["nginx","-g","daemon off;"]'


def _reset_cache() -> None:
    md._nginx_image_cache.clear()


def test_node_frontend_image_is_not_nginx() -> None:
    _reset_cache()
    with patch.object(md, "_docker_inspect_format", return_value=_NODE_CFG):
        assert md._is_nginx_image("launch-test-frontend:latest") is False
        assert (
            md._should_mount_nginx_api_proxy(
                image="launch-test-frontend:latest",
                dep_name="launch-test-frontend",
                listen_port=8080,
            )
            is False
        )


def test_nginx_static_image_is_nginx() -> None:
    _reset_cache()
    with patch.object(md, "_docker_inspect_format", return_value=_NGINX_CFG):
        assert md._is_nginx_image("launch-test-frontend:latest") is True
        assert (
            md._should_mount_nginx_api_proxy(
                image="launch-test-frontend:latest",
                dep_name="launch-test-frontend",
                listen_port=8080,
            )
            is True
        )


def test_nginx_by_name_short_circuits() -> None:
    _reset_cache()
    # An image whose name contains nginx is nginx without inspection.
    with patch.object(md, "_docker_inspect_format", return_value=None):
        assert md._is_nginx_image("nginxinc/nginx-unprivileged:alpine") is True


def test_uninspectable_frontend_falls_back_to_name_port_heuristic() -> None:
    _reset_cache()
    # Config not inspectable (None) + only 8080 exposed + frontend name -> assume the
    # platform SPA nginx runtime (best-effort fallback preserved).
    with (
        patch.object(md, "_docker_inspect_format", return_value=None),
        patch.object(md, "inspect_image_exposed_ports", return_value=[8080]),
    ):
        assert md._is_nginx_image("reg/acme-web:latest") is True


def test_uninspectable_non_frontend_is_not_nginx() -> None:
    _reset_cache()
    with (
        patch.object(md, "_docker_inspect_format", return_value=None),
        patch.object(md, "inspect_image_exposed_ports", return_value=[3000]),
    ):
        assert md._is_nginx_image("reg/acme-api:latest") is False

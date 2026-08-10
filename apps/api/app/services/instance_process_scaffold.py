"""Scaffold VM / local process strategy files for ``running_instance`` workspaces."""

from __future__ import annotations

from pathlib import Path

from app.schemas.cloud import (
    InstanceProcessStrategy,
    InstanceReverseProxy,
    RunningInstanceConfig,
    RunningInstanceKind,
)

INSTANCE_DIR = Path("infra") / "instance"


def write_instance_process_scaffold(
    workspace_dir: Path,
    *,
    name: str,
    running_instance: RunningInstanceConfig | None = None,
    start_command: str | None = None,
) -> list[str]:
    """Write README + docker/pm2/systemd/proxy stubs under ``infra/instance/``.

    Live Launch preview still uses Docker attach for ``process_strategy=docker``.
    PM2/systemd/nginx/caddy artifacts are for VM apply (Ansible/SSH) or manual ops.
    """
    cfg = running_instance or RunningInstanceConfig()
    if cfg.kind == RunningInstanceKind.SERVERLESS:
        return []

    port = int(cfg.listen_port or 8080)
    strategy = cfg.process_strategy
    proxy = cfg.reverse_proxy
    cmd = (start_command or "npm start").strip() or "npm start"
    unit = _sanitize_unit(name)

    out = workspace_dir / INSTANCE_DIR
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    readme = out / "README.md"
    readme.write_text(
        _readme(
            name=name,
            strategy=strategy,
            proxy=proxy,
            port=port,
            kind=cfg.kind,
        ),
        encoding="utf-8",
    )
    written.append(str(INSTANCE_DIR / "README.md"))

    if strategy == InstanceProcessStrategy.DOCKER:
        script = out / "docker-run.sh"
        script.write_text(
            _docker_run_script(name=unit, port=port),
            encoding="utf-8",
        )
        try:
            script.chmod(0o755)
        except OSError:
            pass
        written.append(str(INSTANCE_DIR / "docker-run.sh"))
    elif strategy == InstanceProcessStrategy.SYSTEMD:
        unit_path = out / f"{unit}.service"
        unit_path.write_text(
            _systemd_unit(unit=unit, port=port, start_command=cmd),
            encoding="utf-8",
        )
        written.append(str(INSTANCE_DIR / f"{unit}.service"))
    elif strategy == InstanceProcessStrategy.PM2:
        eco = workspace_dir / "ecosystem.config.cjs"
        eco.write_text(_pm2_ecosystem(name=unit, port=port, start_command=cmd), encoding="utf-8")
        written.append("ecosystem.config.cjs")
        tip = out / "pm2.md"
        tip.write_text(
            "# PM2\n\n"
            "```bash\n"
            "npm i -g pm2\n"
            "pm2 start ecosystem.config.cjs\n"
            "pm2 save && pm2 startup\n"
            "```\n",
            encoding="utf-8",
        )
        written.append(str(INSTANCE_DIR / "pm2.md"))

    if proxy == InstanceReverseProxy.NGINX:
        nginx = out / "nginx.conf"
        nginx.write_text(_nginx_conf(port=port), encoding="utf-8")
        written.append(str(INSTANCE_DIR / "nginx.conf"))
    elif proxy == InstanceReverseProxy.CADDY:
        caddy = out / "Caddyfile"
        caddy.write_text(_caddyfile(port=port), encoding="utf-8")
        written.append(str(INSTANCE_DIR / "Caddyfile"))

    meta = out / "process.json"
    meta.write_text(
        (
            "{\n"
            f'  "name": "{name}",\n'
            f'  "process_strategy": "{strategy.value}",\n'
            f'  "reverse_proxy": "{proxy.value}",\n'
            f'  "listen_port": {port},\n'
            f'  "kind": "{cfg.kind.value}"\n'
            "}\n"
        ),
        encoding="utf-8",
    )
    written.append(str(INSTANCE_DIR / "process.json"))
    return written


def ansible_deploy_mode_for_strategy(strategy: InstanceProcessStrategy) -> str:
    if strategy == InstanceProcessStrategy.SYSTEMD:
        return "systemd"
    if strategy == InstanceProcessStrategy.PM2:
        return "pm2"
    return "docker_run"


def _sanitize_unit(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name.lower())
    return cleaned.strip("-_")[:48] or "launchpad-app"


def _readme(
    *,
    name: str,
    strategy: InstanceProcessStrategy,
    proxy: InstanceReverseProxy,
    port: int,
    kind: RunningInstanceKind,
) -> str:
    lines = [
        f"# Instance process plan: {name}",
        "",
        f"- Compute: `{kind.value}`",
        f"- Process strategy: `{strategy.value}`",
        f"- Reverse proxy: `{proxy.value}`",
        f"- App listen port: `{port}`",
        "",
        "## Recommendations",
        "",
        "- **Docker** (default): same artifact as Compose/K8s; use for most apps.",
        "- **systemd**: preferred native supervisor on Linux VMs (boot, logs, restart).",
        "- **PM2**: Node-only alternative when the team already standardizes on PM2.",
        "- Put **nginx** or **Caddy** in front for TLS and path routing; do not run the app as nginx.",
        "",
    ]
    if strategy == InstanceProcessStrategy.DOCKER:
        lines.extend(
            [
                "## Docker",
                "",
                "Launch preview uses Docker attach on local/VM targets.",
                "Or run `./infra/instance/docker-run.sh` after building the image.",
                "",
            ]
        )
    elif strategy == InstanceProcessStrategy.SYSTEMD:
        lines.extend(
            [
                "## systemd",
                "",
                f"Copy `{_sanitize_unit(name)}.service` to `/etc/systemd/system/`, then:",
                "",
                "```bash",
                "sudo systemctl daemon-reload",
                f"sudo systemctl enable --now {_sanitize_unit(name)}",
                "```",
                "",
                "Live Launch preview still requires Docker today; use Ansible/SSH to apply this unit.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## PM2",
                "",
                "See `ecosystem.config.cjs` at the workspace root and `infra/instance/pm2.md`.",
                "Live Launch preview still requires Docker today; use PM2 on the VM for native Node.",
                "",
            ]
        )
    if proxy != InstanceReverseProxy.NONE:
        lines.extend(
            [
                "## Reverse proxy",
                "",
                f"Config is under `infra/instance/` (`{proxy.value}`).",
                "Point DNS / TLS at the proxy; keep the app bound to localhost or the Docker network.",
                "",
            ]
        )
    return "\n".join(lines)


def _docker_run_script(*, name: str, port: int) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'IMAGE="${{IMAGE:-{name}:latest}}"\n'
        f'NAME="${{NAME:-lp-{name}}}"\n'
        f'PORT="${{PORT:-{port}}}"\n'
        'docker rm -f "$NAME" >/dev/null 2>&1 || true\n'
        'docker run -d --name "$NAME" --restart unless-stopped '
        '-p "${PORT}:${PORT}" -e "PORT=${PORT}" -e "HOST=0.0.0.0" "$IMAGE"\n'
        'echo "Listening on http://127.0.0.1:${PORT}"\n'
    )


def _systemd_unit(*, unit: str, port: int, start_command: str) -> str:
    # Escape only what systemd ExecStart needs for a simple shell wrapper.
    return (
        "[Unit]\n"
        f"Description=Launchpad app ({unit})\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "WorkingDirectory=/opt/launchpad/app\n"
        "EnvironmentFile=-/opt/launchpad/app/.env\n"
        f"Environment=PORT={port}\n"
        "Environment=HOST=0.0.0.0\n"
        f"ExecStart=/bin/bash -lc '{start_command}'\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "User=deploy\n"
        "Group=deploy\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _pm2_ecosystem(*, name: str, port: int, start_command: str) -> str:
    # Prefer npm start style; allow override via start_command.
    script = start_command.replace("\\", "\\\\").replace("'", "\\'")
    return (
        "/** Launchpad PM2 ecosystem - generated for running_instance */\n"
        "module.exports = {\n"
        "  apps: [\n"
        "    {\n"
        f"      name: '{name}',\n"
        "      cwd: '.',\n"
        f"      script: 'bash',\n"
        f"      args: ['-lc', '{script}'],\n"
        "      instances: 1,\n"
        "      autorestart: true,\n"
        "      max_memory_restart: '512M',\n"
        "      env: {\n"
        f"        PORT: '{port}',\n"
        "        HOST: '0.0.0.0',\n"
        "        NODE_ENV: 'production',\n"
        "      },\n"
        "    },\n"
        "  ],\n"
        "};\n"
    )


def _nginx_conf(*, port: int) -> str:
    return (
        "# Launchpad nginx reverse proxy (TLS termination typically via certbot / LB)\n"
        "server {\n"
        "  listen 80;\n"
        "  server_name _;\n"
        "\n"
        "  location / {\n"
        f"    proxy_pass http://127.0.0.1:{port};\n"
        "    proxy_http_version 1.1;\n"
        "    proxy_set_header Host $host;\n"
        "    proxy_set_header X-Real-IP $remote_addr;\n"
        "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        "    proxy_set_header X-Forwarded-Proto $scheme;\n"
        "    proxy_set_header Upgrade $http_upgrade;\n"
        "    proxy_set_header Connection \"upgrade\";\n"
        "  }\n"
        "}\n"
    )


def _caddyfile(*, port: int) -> str:
    return (
        "# Launchpad Caddyfile - replace :80 with your domain for automatic HTTPS\n"
        f":80 {{\n"
        f"  reverse_proxy 127.0.0.1:{port}\n"
        "}\n"
    )

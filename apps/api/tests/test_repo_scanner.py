"""Zero-config auto-discovery: Dockerfile EXPOSE + docker-compose port scanning."""

from __future__ import annotations

from pathlib import Path

from app.services.repo_scanner import parse_expose_ports, scan_repo


def test_parse_expose_ports_multi_and_proto() -> None:
    text = "FROM node:20\nEXPOSE 8080\nEXPOSE 9090/tcp 3000\nEXPOSE ${PORT}\n"
    assert parse_expose_ports(text) == [8080, 9090, 3000]


def test_scan_dockerfile_only(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM nginx\nEXPOSE 8080\n", encoding="utf-8")
    result = scan_repo(tmp_path)
    assert result.dockerfiles == ["Dockerfile"]
    svc = next(s for s in result.services if s.source == "dockerfile")
    assert svc.name == "app"
    assert svc.ports == [8080]


def test_scan_compose_services_and_ports(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  web:\n"
        "    image: nginx\n"
        "    ports:\n"
        '      - "8080:80"\n'
        "  api:\n"
        "    image: acme/api\n"
        "    ports:\n"
        "      - target: 3000\n"
        "        published: 3000\n"
        "    expose:\n"
        '      - "9000"\n'
        "  db:\n"
        "    image: postgres:16\n"
        '    ports: ["127.0.0.1:5432:5432"]\n',
        encoding="utf-8",
    )
    result = scan_repo(tmp_path)
    assert result.compose_file == "docker-compose.yml"
    by_name = {s.name: s for s in result.services}
    assert by_name["web"].ports == [80]
    assert by_name["api"].ports == [3000, 9000]
    assert by_name["db"].ports == [5432]
    assert by_name["web"].image == "nginx"
    assert set(result.all_ports) == {80, 3000, 9000, 5432}


def test_manual_ports_override_wins(tmp_path: Path) -> None:
    # Existing manually configured repo: auto-discovery must not override it.
    (tmp_path / "Dockerfile").write_text("EXPOSE 8080\n", encoding="utf-8")
    result = scan_repo(tmp_path, manual_ports=[1234])
    assert len(result.services) == 1
    assert result.services[0].source == "manual"
    assert result.services[0].ports == [1234]


def test_include_flags_disable_sources(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("EXPOSE 8080\n", encoding="utf-8")
    (tmp_path / "compose.yml").write_text(
        "services:\n  web:\n    image: nginx\n    ports: ['80:80']\n", encoding="utf-8"
    )
    only_compose = scan_repo(tmp_path, include_dockerfile=False)
    assert all(s.source == "compose" for s in only_compose.services)
    only_docker = scan_repo(tmp_path, include_compose=False)
    assert all(s.source == "dockerfile" for s in only_docker.services)


def test_monorepo_app_dockerfiles(tmp_path: Path) -> None:
    web = tmp_path / "apps" / "web"
    api = tmp_path / "apps" / "api"
    web.mkdir(parents=True)
    api.mkdir(parents=True)
    (web / "Dockerfile").write_text("EXPOSE 3000\n", encoding="utf-8")
    (api / "Dockerfile").write_text("EXPOSE 8000\n", encoding="utf-8")
    result = scan_repo(tmp_path)
    by_name = {s.name: s for s in result.services}
    assert by_name["web"].ports == [3000]
    assert by_name["api"].ports == [8000]


def test_malformed_compose_is_safe(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.yml").write_text(": not: valid: yaml: [", encoding="utf-8")
    # Must not raise.
    result = scan_repo(tmp_path)
    assert result.services == []

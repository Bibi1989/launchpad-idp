"""Ansible scaffold writer for instance / Compose host configuration."""

from pathlib import Path

from app.schemas.cloud import AnsibleAppDeployMode, AnsibleConfig, WorkspaceRuntimeMode
from app.services.ansible_scaffold import write_ansible_scaffold


def test_ansible_scaffold_writes_inventory_roles_and_playbook(tmp_path: Path) -> None:
    cfg = AnsibleConfig(
        enabled=True,
        hosts="10.0.0.5\n10.0.0.6",
        inventory_group="app_servers",
        ssh_user="ubuntu",
        ssh_private_key_path="~/.ssh/id_ed25519",
        install_docker=True,
        enable_ufw=True,
        create_deploy_user=True,
        app_deploy_mode=AnsibleAppDeployMode.DOCKER_RUN,
        app_listen_port=3000,
    )
    written = write_ansible_scaffold(
        tmp_path,
        name="demo-app",
        config=cfg,
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
    )
    assert "infra/ansible/playbooks/site.yml" in written
    assert "infra/ansible/inventory/hosts.yml" in written
    assert "infra/ansible/requirements.yml" in written

    inventory = (tmp_path / "infra/ansible/inventory/hosts.yml").read_text(encoding="utf-8")
    assert "10.0.0.5:" in inventory
    assert "ansible_user: ubuntu" in inventory
    assert "ansible_ssh_private_key_file: ~/.ssh/id_ed25519" in inventory

    vars_yaml = (tmp_path / "infra/ansible/group_vars/all.yml").read_text(encoding="utf-8")
    assert "app_listen_port: 3000" in vars_yaml
    assert "app_deploy_mode: docker_run" in vars_yaml
    assert "reverse_proxy: none" in vars_yaml
    assert "app_start_command:" in vars_yaml

    site = (tmp_path / "infra/ansible/playbooks/site.yml").read_text(encoding="utf-8")
    assert "hosts: app_servers" in site
    assert "- docker" in site
    assert "- app" in site
    assert "reverse_proxy" not in site or "- reverse_proxy" not in site

    app_tasks = (tmp_path / "infra/ansible/roles/app/tasks/main.yml").read_text(
        encoding="utf-8",
    )
    assert "community.docker.docker_container" in app_tasks
    assert "docker_compose_v2" in app_tasks
    assert "systemd" in app_tasks


def test_ansible_scaffold_compose_runtime_skips_app_when_none(tmp_path: Path) -> None:
    cfg = AnsibleConfig(
        enabled=True,
        hosts="127.0.0.1",
        install_docker=False,
        enable_ufw=False,
        enable_fail2ban=False,
        enable_unattended_upgrades=False,
        create_deploy_user=False,
        app_deploy_mode=AnsibleAppDeployMode.NONE,
    )
    write_ansible_scaffold(
        tmp_path,
        name="compose-host",
        config=cfg,
        runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
    )
    site = (tmp_path / "infra/ansible/playbooks/site.yml").read_text(encoding="utf-8")
    assert "- common" in site
    assert "- app" not in site
    assert not (tmp_path / "infra/ansible/roles/app").exists()


def test_ansible_scaffold_pm2_with_nginx_proxy(tmp_path: Path) -> None:
    from app.schemas.cloud import InstanceReverseProxy

    cfg = AnsibleConfig(
        enabled=True,
        hosts="10.0.0.8",
        install_docker=False,
        create_deploy_user=True,
        app_deploy_mode=AnsibleAppDeployMode.PM2,
        reverse_proxy=InstanceReverseProxy.NGINX,
        app_start_command="npm run start:prod",
        app_listen_port=3000,
    )
    write_ansible_scaffold(
        tmp_path,
        name="pm2-app",
        config=cfg,
        runtime_mode=WorkspaceRuntimeMode.RUNNING_INSTANCE,
    )
    vars_yaml = (tmp_path / "infra/ansible/group_vars/all.yml").read_text(encoding="utf-8")
    assert "app_deploy_mode: pm2" in vars_yaml
    assert "reverse_proxy: nginx" in vars_yaml
    assert "install_docker: false" in vars_yaml
    assert "npm run start:prod" in vars_yaml

    site = (tmp_path / "infra/ansible/playbooks/site.yml").read_text(encoding="utf-8")
    assert "- reverse_proxy" in site
    assert "- app" in site
    assert "- docker" not in site

    proxy = (tmp_path / "infra/ansible/roles/reverse_proxy/tasks/main.yml").read_text(
        encoding="utf-8",
    )
    assert "nginx" in proxy
    assert "proxy_pass" in proxy

    app = (tmp_path / "infra/ansible/roles/app/tasks/main.yml").read_text(encoding="utf-8")
    assert "pm2" in app
    assert "app_start_command" in app

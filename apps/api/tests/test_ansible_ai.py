"""Heuristic Ansible AI refine (no Gemini required)."""

from app.core.config import get_settings
from app.services.ansible_ai import AnsibleAiService


def test_refine_heuristic_switches_to_pm2_and_nginx() -> None:
    settings = get_settings().model_copy(update={"gemini_api_key": None})
    service = AnsibleAiService(settings=settings)
    files, summary, source = service.refine(
        prompt="Use PM2 with nginx reverse proxy",
        app_deploy_mode="docker_run",
        reverse_proxy="none",
        workspace_name="demo",
        current_files=[
            {
                "path": "infra/ansible/group_vars/all.yml",
                "content": (
                    "launchpad_workspace: demo\n"
                    "app_deploy_mode: docker_run\n"
                    "reverse_proxy: none\n"
                    "install_docker: true\n"
                ),
            },
            {
                "path": "infra/ansible/playbooks/site.yml",
                "content": "---\n- name: site\n  hosts: app_servers\n  roles:\n    - common\n",
            },
        ],
    )
    assert source == "heuristic"
    assert "pm2" in summary.lower() or "app_deploy_mode=pm2" in summary
    by_path = {f["path"]: f["content"] for f in files}
    assert "app_deploy_mode: pm2" in by_path["infra/ansible/group_vars/all.yml"]
    assert "reverse_proxy: nginx" in by_path["infra/ansible/group_vars/all.yml"]
    assert "install_docker: false" in by_path["infra/ansible/group_vars/all.yml"]
    assert "reverse_proxy" in by_path["infra/ansible/playbooks/site.yml"]

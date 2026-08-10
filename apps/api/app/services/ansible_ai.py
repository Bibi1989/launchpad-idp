"""Gemini (or heuristic) refinement of Ansible workspace files from a user prompt."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_ANSIBLE_FILES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["files", "summary"],
    "properties": {
        "summary": {"type": "string"},
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path under infra/ansible/...",
                    },
                    "content": {"type": "string"},
                },
            },
        },
    },
}

_SYSTEM = """You are Launchpad's Ansible assistant.
Update Ansible YAML for a Linux host deploy. Respect process strategy and reverse proxy.
Return ONLY structured JSON matching the schema (files + summary).
Rules:
- Keep valid Ansible YAML (---, proper indentation).
- Prefer paths under infra/ansible/ (inventory, playbooks, group_vars, roles/*/tasks).
- Honor app_deploy_mode: docker_run | docker_compose | systemd | pm2 | none.
- Honor reverse_proxy: none | nginx | caddy.
- Do not invent cloud credentials or secrets.
- Preserve existing inventory hosts unless the user asks to change them.
- Keep changes minimal and production-minded.
"""


class AnsibleAiService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def gemini_configured(self) -> bool:
        return bool(self._settings.gemini_api_key)

    def refine(
        self,
        *,
        prompt: str,
        app_deploy_mode: str,
        reverse_proxy: str,
        workspace_name: str,
        current_files: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], str, str]:
        """Return (files, summary, source) where source is gemini|heuristic."""
        cleaned = prompt.strip()
        if not cleaned:
            raise ValueError("prompt is required")

        if self.gemini_configured:
            try:
                files, summary = self._refine_gemini(
                    prompt=cleaned,
                    app_deploy_mode=app_deploy_mode,
                    reverse_proxy=reverse_proxy,
                    workspace_name=workspace_name,
                    current_files=current_files,
                )
                return files, summary, "gemini"
            except Exception as exc:
                logger.warning("ansible_ai_gemini_failed", error=str(exc))

        files, summary = self._refine_heuristic(
            prompt=cleaned,
            app_deploy_mode=app_deploy_mode,
            reverse_proxy=reverse_proxy,
            workspace_name=workspace_name,
            current_files=current_files,
        )
        return files, summary, "heuristic"

    def _refine_gemini(
        self,
        *,
        prompt: str,
        app_deploy_mode: str,
        reverse_proxy: str,
        workspace_name: str,
        current_files: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], str]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._settings.gemini_api_key)
        payload = {
            "workspace_name": workspace_name,
            "app_deploy_mode": app_deploy_mode,
            "reverse_proxy": reverse_proxy,
            "user_prompt": prompt,
            "current_files": current_files[:12],
        }
        response = client.models.generate_content(
            model=self._settings.gemini_model,
            contents=json.dumps(payload, indent=2),
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                temperature=0.2,
                response_mime_type="application/json",
                response_schema=_ANSIBLE_FILES_SCHEMA,
            ),
        )
        text = (response.text or "").strip()
        data = json.loads(text)
        files = _normalize_files(data.get("files") or [])
        summary = str(data.get("summary") or "Updated Ansible files").strip()
        if not files:
            raise RuntimeError("Gemini returned no Ansible files")
        return files, summary

    def _refine_heuristic(
        self,
        *,
        prompt: str,
        app_deploy_mode: str,
        reverse_proxy: str,
        workspace_name: str,
        current_files: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], str]:
        """Deterministic tweak of group_vars + playbook comments from the prompt."""
        by_path = {f["path"]: f["content"] for f in current_files if f.get("path")}
        group_path = "infra/ansible/group_vars/all.yml"
        play_path = "infra/ansible/playbooks/site.yml"
        group = by_path.get(group_path, "")
        play = by_path.get(play_path, "")

        mode = app_deploy_mode
        proxy = reverse_proxy
        lower = prompt.lower()
        if "pm2" in lower:
            mode = "pm2"
        elif "systemd" in lower:
            mode = "systemd"
        elif "compose" in lower:
            mode = "docker_compose"
        elif "docker" in lower:
            mode = "docker_run"
        if "caddy" in lower:
            proxy = "caddy"
        elif "nginx" in lower:
            proxy = "nginx"
        elif re.search(r"\bno\s+proxy\b|\bwithout\s+proxy\b|\bdisable\s+proxy\b", lower):
            proxy = "none"

        if group:
            group = _upsert_yaml_key(group, "app_deploy_mode", mode)
            group = _upsert_yaml_key(group, "reverse_proxy", proxy)
            install_docker = "true" if mode in {"docker_run", "docker_compose"} else "false"
            group = _upsert_yaml_key(group, "install_docker", install_docker)
            by_path[group_path] = group
        else:
            by_path[group_path] = (
                f"# Generated by Launchpad for workspace `{workspace_name}`\n"
                f"launchpad_workspace: {workspace_name}\n"
                f"app_deploy_mode: {mode}\n"
                f"reverse_proxy: {proxy}\n"
                f"install_docker: {'true' if mode in {'docker_run', 'docker_compose'} else 'false'}\n"
            )

        banner = (
            f"# Site playbook for {workspace_name}\n"
            f"# app_deploy_mode={mode} reverse_proxy={proxy}\n"
            f"# AI note: {prompt.strip()[:180]}\n"
        )
        if play.startswith("---"):
            rest = play.split("\n", 1)[1] if "\n" in play else ""
            play = "---\n" + banner + rest.lstrip()
        else:
            play = "---\n" + banner + play
        # Ensure reverse_proxy role is referenced
        if "role: reverse_proxy" not in play and proxy != "none":
            play = play.rstrip() + (
                "\n    - role: reverse_proxy\n"
                '      when: reverse_proxy | default("none") != "none"\n'
            )
        by_path[play_path] = play

        files = [{"path": p, "content": c} for p, c in sorted(by_path.items())]
        summary = (
            f"Heuristic update: app_deploy_mode={mode}, reverse_proxy={proxy} "
            f"(set GEMINI_API_KEY for richer AI edits)."
        )
        return files, summary


def _normalize_files(raw: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip().replace("\\", "/")
        content = item.get("content")
        if not path or not isinstance(content, str):
            continue
        if ".." in path.split("/"):
            continue
        if not path.startswith("infra/ansible/"):
            path = f"infra/ansible/{path.lstrip('/')}"
        out.append({"path": path, "content": content})
    return out


def _upsert_yaml_key(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(key)}:\s*.*$")
    line = f"{key}: {value}"
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    return text.rstrip() + f"\n{line}\n"

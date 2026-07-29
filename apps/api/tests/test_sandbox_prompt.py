from pathlib import Path

from app.services.sandbox_runner import SandboxRunner, _SANDBOX_PS1


def test_sandbox_ps1_includes_full_cwd() -> None:
    assert r"\w" in _SANDBOX_PS1
    assert r"\W" not in _SANDBOX_PS1


def test_interactive_shell_command_exports_path_prompt() -> None:
    runner = SandboxRunner()
    cmd = runner._interactive_shell_command({}, docker=False)
    assert "export PS1=" in cmd
    assert r"\w" in cmd
    assert "bash --norc --noprofile -i" in cmd


def test_local_spawn_script_exports_ps1_before_exec() -> None:
    """Non-interactive bash -lc strips PS1; the spawn script must re-export it."""
    source = Path(__file__).resolve().parents[1] / "app" / "services" / "sandbox_runner.py"
    text = source.read_text(encoding="utf-8")
    assert "export PS1=" in text
    assert "bash --norc --noprofile -i" in text

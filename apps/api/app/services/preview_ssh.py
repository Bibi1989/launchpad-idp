"""Ephemeral SSH keys for cloud preview VM attach deploy."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def preview_ssh_dir(settings: Settings | None = None) -> Path:
    root = Path((settings or get_settings()).iac_workspace_root).expanduser().resolve().parent
    return root / "preview-ssh"


def ensure_preview_ssh_keypair(
    environment_id: str,
    *,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """Return ``(private_key_path, public_key_openssh_line)`` for an environment."""
    cfg = settings or get_settings()
    directory = preview_ssh_dir(cfg)
    directory.mkdir(parents=True, exist_ok=True)
    safe_id = environment_id.replace("/", "-")
    key_path = directory / f"{safe_id}.pem"
    pub_path = directory / f"{safe_id}.pub"

    if key_path.is_file() and pub_path.is_file():
        return str(key_path), pub_path.read_text(encoding="utf-8").strip()

    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    priv_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    key_path.write_bytes(priv_bytes)
    key_path.chmod(0o600)
    pub_line = f"{pub_bytes.decode('ascii').strip()} launchpad-preview"
    pub_path.write_text(pub_line + "\n", encoding="utf-8")
    logger.info("preview_ssh_key_created", environment_id=environment_id)
    return str(key_path), pub_line


def resolve_preview_ssh_key_path(
    environment_id: str,
    *,
    settings: Settings | None = None,
) -> str | None:
    cfg = settings or get_settings()
    key_path = preview_ssh_dir(cfg) / f"{environment_id.replace('/', '-')}.pem"
    return str(key_path) if key_path.is_file() else None


def authorized_keys_user_data_snippet(public_key_line: str, *, user: str = "ec2-user") -> str:
    """Bash fragment to install a Launchpad preview SSH public key for ``user``."""
    line = (public_key_line or "").strip()
    if not line:
        return ""
    escaped = line.replace("'", "'\"'\"'")
    return (
        f"mkdir -p /home/{user}/.ssh\n"
        f"touch /home/{user}/.ssh/authorized_keys\n"
        f"grep -qxF '{escaped}' /home/{user}/.ssh/authorized_keys "
        f"|| echo '{escaped}' >> /home/{user}/.ssh/authorized_keys\n"
        f"chmod 700 /home/{user}/.ssh\n"
        f"chmod 600 /home/{user}/.ssh/authorized_keys\n"
        f"chown -R {user}:{user} /home/{user}/.ssh\n"
    )

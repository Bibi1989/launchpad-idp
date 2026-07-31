from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import select
import signal
import struct
import tempfile
import termios
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.secrets import credentials_to_env, mask_terminal_output
from app.models.domain import ExecutionStage

logger = get_logger(__name__)

OutputCallback = Callable[[bytes], None]

_CONTAINER_OIDC_ROOT = "/tmp/launchpad-oidc"

_TF_DIR = Path("infra") / "terraform"
_K8S_MANIFESTS_DIR = Path("infra") / "k8s" / "manifests"
_HELM_CHART_DIR = Path("infra") / "helm" / "app-chart"
_INGRESS_NGINX_VALUES = Path("infra") / "k8s" / "addons" / "ingress-nginx-values.yaml"

# Full cwd (\w) so the prompt always shows where you are after cd.
_SANDBOX_PS1 = r"\[\e[32m\]launchpad\[\e[0m\]:\[\e[36m\]\w\[\e[0m\]\$ "


def _stage_echo(stage: ExecutionStage, message: str, *, log_level: str = "INFO") -> str:
    """Emit a structured JSON log line for the terminal / SSE consumers."""
    payload = {
        "stage": stage.value,
        "log_level": log_level,
        "timestamp": datetime.now(UTC).isoformat(),
        "message": message,
    }
    encoded = json.dumps(payload, separators=(",", ":"))
    return f"echo '{encoded}'"


def build_provision_bootstrap(workspace_path: Path, *, engine: str) -> str | None:
    """Build the sandboxed provision pipeline for a workspace.

    Streams through the interactive PTY using structured stages:
    INIT → VALIDATE → PLAN → APPLY for terraform / helm / kubectl.
    """
    root = workspace_path.resolve()
    steps: list[str] = []

    steps.append(_stage_echo(ExecutionStage.INIT, "starting provision pipeline"))

    kind_meta = root / "infra" / "kind" / "README.md"
    if kind_meta.is_file():
        steps.append(
            f"{_stage_echo(ExecutionStage.INIT, 'kind / local Kubernetes context')} && "
            "if ! command -v kubectl >/dev/null 2>&1; then "
            f"{_stage_echo(ExecutionStage.INIT, 'kubectl not installed — install kubectl and re-open the terminal', log_level='WARN')}; "
            "elif ! kubectl cluster-info >/dev/null 2>&1; then "
            f"{_stage_echo(ExecutionStage.INIT, 'kubectl cannot reach a cluster — run make kind-up', log_level='WARN')}; "
            "else "
            "echo -n '[launchpad] current context: ' && kubectl config current-context; "
            "fi"
        )

    tf_dir = root / _TF_DIR
    if engine in {"terraform", "opentofu"} and tf_dir.is_dir():
        cli = "tofu" if engine == "opentofu" else "terraform"
        label = "OpenTofu" if engine == "opentofu" else "terraform"
        steps.append(
            f"{_stage_echo(ExecutionStage.INIT, f'cloud infra provision — {label} init (working-directory: infra/terraform)')} && "
            "cd infra/terraform && "
            f"if ! command -v {cli} >/dev/null 2>&1; then "
            f"{_stage_echo(ExecutionStage.INIT, f'{cli} not installed — skipping apply', log_level='WARN')}; "
            "cd \"$OLDPWD\" >/dev/null 2>&1 || true; "
            "else "
            f"{cli} init -input=false && "
            f"{_stage_echo(ExecutionStage.VALIDATE, f'{label} validate')} && "
            f"{cli} validate && "
            f"{_stage_echo(ExecutionStage.PLAN, f'{label} plan')} && "
            f"{cli} plan -out=tfplan -input=false && "
            f"{_stage_echo(ExecutionStage.APPLY, f'{label} apply')} && "
            f"{cli} apply -auto-approve -input=false tfplan; "
            "_tf_rc=$?; "
            "cd \"$OLDPWD\" >/dev/null 2>&1 || true; "
            "if test \"${_tf_rc}\" -ne 0; then "
            f"{_stage_echo(ExecutionStage.APPLY, f'{label} apply failed', log_level='ERROR')}; "
            "fi; "
            "test \"${_tf_rc}\" -eq 0; "
            "fi"
        )
    elif engine == "pulumi":
        steps.append(
            f"{_stage_echo(ExecutionStage.INIT, 'pulumi workspace bootstrap (npm install)')} && "
            "if command -v npm >/dev/null 2>&1; then npm install; "
            f"else {_stage_echo(ExecutionStage.INIT, 'npm not installed — skipping', log_level='WARN')}; fi"
        )

    ingress_values = root / _INGRESS_NGINX_VALUES
    if ingress_values.is_file():
        steps.append(
            f"{_stage_echo(ExecutionStage.APPLY, 'helm install ingress-nginx (official chart)')} && "
            "if ! command -v helm >/dev/null 2>&1; then "
            f"{_stage_echo(ExecutionStage.APPLY, 'helm not installed — skipping ingress-nginx', log_level='WARN')}; "
            "else "
            "helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null 2>&1 || true && "
            "helm repo update ingress-nginx >/dev/null 2>&1 || true && "
            "helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx "
            "--namespace ingress-nginx --create-namespace "
            f"-f {_INGRESS_NGINX_VALUES.as_posix()} --wait=false; "
            "fi"
        )

    helm_dir = root / _HELM_CHART_DIR
    k8s_dir = root / _K8S_MANIFESTS_DIR
    if helm_dir.is_dir() and (helm_dir / "Chart.yaml").is_file():
        steps.append(
            f"{_stage_echo(ExecutionStage.VALIDATE, 'helm lint app-chart')} && "
            "if ! command -v helm >/dev/null 2>&1; then "
            f"{_stage_echo(ExecutionStage.APPLY, 'helm not installed — skipping chart deploy', log_level='WARN')}; "
            "else "
            "helm lint infra/helm/app-chart/ >/dev/null 2>&1 || true && "
            f"{_stage_echo(ExecutionStage.APPLY, 'helm upgrade --install app-chart')} && "
            "helm upgrade --install app-chart infra/helm/app-chart/ --wait=false; "
            "fi"
        )
    elif k8s_dir.is_dir() and any(k8s_dir.glob("*.y*ml")):
        steps.append(
            f"{_stage_echo(ExecutionStage.VALIDATE, 'kubectl client / cluster reachability')} && "
            "if ! command -v kubectl >/dev/null 2>&1; then "
            f"{_stage_echo(ExecutionStage.APPLY, 'kubectl not installed — skipping manifests', log_level='WARN')}; "
            "else "
            f"{_stage_echo(ExecutionStage.APPLY, 'manifest deploy — kubectl apply -f infra/k8s/manifests/')} && "
            "kubectl apply -f infra/k8s/manifests/; "
            "fi"
        )

    if len(steps) <= 1:
        return None

    # Run phases sequentially; do not abort the interactive shell on failure.
    joined = " ; ".join(
        f"( {step} ) || {_stage_echo(ExecutionStage.APPLY, 'step finished with errors', log_level='ERROR')}"
        for step in steps
    )
    return (
        joined
        + " ; "
        + _stage_echo(ExecutionStage.APPLY, "provision pipeline finished — interactive shell ready")
    )


@dataclass
class SandboxSession:
    session_id: str
    workspace_id: str
    workspace_path: Path
    mode: str
    container_id: str | None = None
    master_fd: int | None = None
    pid: int | None = None
    cols: int = 120
    rows: int = 40
    alive: bool = True
    env_keys: set[str] = field(default_factory=set)
    pending_bootstrap: str | None = None
    bootstrap_started: bool = False


class SandboxRunner:
    """
    Spawns an isolated interactive PTY sandbox.

    Modes:
      - docker: ephemeral container with workspace bind-mount
      - local: interactive login shell in the workspace (dev default)

    Optional provision bootstrap is deferred until ``read_loop`` attaches so CLI
    output (terraform / kubectl / helm) streams live on the terminal channel.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._sessions: dict[str, SandboxSession] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        *,
        workspace_id: str,
        workspace_path: Path,
        credentials: dict[str, str | None],
        bootstrap_command: str | None = None,
        cols: int = 120,
        rows: int = 40,
    ) -> SandboxSession:
        session_id = str(uuid.uuid4())
        env = credentials_to_env(credentials, workspace_id=workspace_id)
        env_keys = set(env.keys())


        logger.info(
            "sandbox_session_create",
            session_id=session_id,
            workspace_id=workspace_id,
            credential_keys=sorted(env_keys),
            docker_enabled=self._settings.sandbox_docker_enabled,
            has_bootstrap=bool(bootstrap_command),
        )

        if self._settings.sandbox_docker_enabled:
            session = await asyncio.to_thread(
                self._spawn_docker,
                session_id,
                workspace_id,
                workspace_path,
                env,
                cols,
                rows,
            )
        else:
            session = await asyncio.to_thread(
                self._spawn_local_pty,
                session_id,
                workspace_id,
                workspace_path,
                env,
                cols,
                rows,
            )

        session.env_keys = env_keys
        session.pending_bootstrap = bootstrap_command
        async with self._lock:
            self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> SandboxSession | None:
        return self._sessions.get(session_id)

    async def write(self, session_id: str, data: bytes) -> None:
        session = self._sessions.get(session_id)
        if session is None or not session.alive or session.master_fd is None:
            raise RuntimeError("Sandbox session is not active")
        await asyncio.to_thread(os.write, session.master_fd, data)

    async def resize(self, session_id: str, cols: int, rows: int) -> None:
        session = self._sessions.get(session_id)
        if session is None or not session.alive or session.master_fd is None:
            return
        session.cols = max(20, cols)
        session.rows = max(5, rows)
        await asyncio.to_thread(self._set_winsize, session.master_fd, session.rows, session.cols)
        if session.pid:
            try:
                os.kill(session.pid, signal.SIGWINCH)
            except ProcessLookupError:
                pass

    async def read_loop(self, session_id: str, on_output: OutputCallback) -> None:
        session = self._sessions.get(session_id)
        if session is None or session.master_fd is None:
            return

        loop = asyncio.get_running_loop()
        fd = session.master_fd
        self._set_nonblocking(fd)

        await self._kickoff_bootstrap(session_id)

        while session.alive:
            ready = await loop.run_in_executor(
                None, lambda: select.select([fd], [], [], 0.05)
            )
            if not ready[0]:
                if session.pid and self._process_exited(session.pid):
                    session.alive = False
                    break
                continue
            try:
                chunk = await loop.run_in_executor(None, os.read, fd, 8192)
            except BlockingIOError:
                continue
            except OSError:
                session.alive = False
                break
            if not chunk:
                session.alive = False
                break
            masked = mask_terminal_output(chunk.decode("utf-8", errors="replace"))
            on_output(masked.encode("utf-8"))

    async def _kickoff_bootstrap(self, session_id: str) -> None:
        """Inject the deferred provision pipeline so output streams on this channel."""
        session = self._sessions.get(session_id)
        if session is None or session.bootstrap_started:
            return
        command = session.pending_bootstrap
        session.pending_bootstrap = None
        session.bootstrap_started = True
        if not command:
            return

        # Allow the interactive shell banner/prompt to appear first.
        await asyncio.sleep(0.2)
        # Run via bash -lc so multi-statement pipelines execute atomically, then
        # return control to the already-running interactive shell.
        payload = (
            "echo; "
            f"bash -lc {self._shell_quote(command)} ; "
            "echo\n"
        )
        try:
            await self.write(session_id, payload.encode("utf-8"))
            logger.info(
                "sandbox_bootstrap_kicked_off",
                session_id=session_id,
                workspace_id=session.workspace_id,
            )
        except RuntimeError:
            logger.warning("sandbox_bootstrap_kickoff_failed", session_id=session_id)

    @staticmethod
    def _shell_quote(value: str) -> str:
        """POSIX single-quote escaping for embedding a script in ``bash -lc``."""
        return "'" + value.replace("'", "'\"'\"'") + "'"

    async def kill(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.alive = False
        await asyncio.to_thread(self._cleanup_session, session)
        async with self._lock:
            self._sessions.pop(session_id, None)
        logger.info("sandbox_session_killed", session_id=session_id)

    def _spawn_docker(
        self,
        session_id: str,
        workspace_id: str,
        workspace_path: Path,
        env: dict[str, str],
        cols: int,
        rows: int,
    ) -> SandboxSession:
        try:
            import docker
        except ImportError as exc:
            raise RuntimeError("docker package is required when sandbox_docker_enabled=true") from exc

        client = docker.from_env()
        image = self._settings.sandbox_image
        environment, oidc_volumes = self._docker_oidc_env(env, workspace_id)
        environment = {
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "PS1": _SANDBOX_PS1,
            **environment,
        }
        if "GCP_SA_KEY" in environment and "GOOGLE_APPLICATION_CREDENTIALS" not in environment:
            environment["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/gcp-sa.json"

        volumes: dict[str, dict[str, str]] = {
            str(workspace_path.resolve()): {"bind": "/workspace", "mode": "rw"},
            **oidc_volumes,
        }

        # Interactive shell only — provision bootstrap is injected from read_loop
        # so terraform/kubectl/helm output streams on the attached PTY channel.
        shell_cmd = self._interactive_shell_command(environment, docker=True)
        container = client.containers.run(
            image,
            command=["bash", "-lc", shell_cmd],
            detach=True,
            tty=True,
            stdin_open=True,
            working_dir="/workspace",
            volumes=volumes,
            environment=environment,
            network_mode=self._settings.sandbox_network_mode,
            mem_limit=self._settings.sandbox_memory_limit,
            nano_cpus=int(self._settings.sandbox_cpu_limit * 1e9),
            labels={
                "launchpad.io/session-id": session_id,
                "launchpad.io/workspace-id": workspace_id,
                "launchpad.io/managed-by": "launchpad-idp",
            },
            auto_remove=False,
        )

        master_fd, slave_fd = pty.openpty()
        self._set_winsize(master_fd, rows, cols)
        pid = os.fork()
        if pid == 0:
            os.close(master_fd)
            os.setsid()
            try:
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            except OSError:
                pass
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            # --norc/--noprofile keeps our PS1 (full cwd) from being overwritten.
            os.execvp(
                "docker",
                [
                    "docker",
                    "exec",
                    "-it",
                    "-e",
                    f"PS1={_SANDBOX_PS1}",
                    container.id,
                    "bash",
                    "--norc",
                    "--noprofile",
                    "-i",
                ],
            )
        os.close(slave_fd)
        return SandboxSession(
            session_id=session_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            mode="docker",
            container_id=container.id,
            master_fd=master_fd,
            pid=pid,
            cols=cols,
            rows=rows,
        )

    def _spawn_local_pty(
        self,
        session_id: str,
        workspace_id: str,
        workspace_path: Path,
        env: dict[str, str],
        cols: int,
        rows: int,
    ) -> SandboxSession:
        master_fd, slave_fd = pty.openpty()
        self._set_winsize(master_fd, rows, cols)
        pid = os.fork()
        if pid == 0:
            os.close(master_fd)
            os.setsid()
            try:
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            except OSError:
                pass
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)

            os.chdir(workspace_path)
            child_env = os.environ.copy()
            child_env.update(env)
            child_env["TERM"] = "xterm-256color"
            child_env["COLORTERM"] = "truecolor"
            # Full path (\w) so cwd remains visible after cd into infra/, etc.
            child_env["PS1"] = _SANDBOX_PS1
            if "GCP_SA_KEY" in env and "GOOGLE_APPLICATION_CREDENTIALS" not in env:
                key_path = workspace_path / ".launchpad" / "gcp-sa.json"
                key_path.parent.mkdir(parents=True, exist_ok=True)
                key_path.write_text(env["GCP_SA_KEY"], encoding="utf-8")
                os.chmod(key_path, 0o600)
                child_env["GOOGLE_APPLICATION_CREDENTIALS"] = str(key_path)

            # Prefer bash for a predictable PS1; skip rc/profile so they cannot wipe it.
            # Re-export PS1 inside -lc: non-interactive bash strips PS1 from the inherited env.
            interactive_shell = "/bin/bash" if Path("/bin/bash").exists() else "/bin/sh"
            ps1_export = f"export PS1={SandboxRunner._shell_quote(_SANDBOX_PS1)}"
            rc_lines = [
                ps1_export,
                "echo '[launchpad] interactive sandbox ready'",
                "echo '[launchpad] workspace: '\"$PWD\"",
                f"exec {interactive_shell} --norc --noprofile -i",
            ]
            os.execvpe(
                "/bin/bash",
                ["bash", "-lc", "; ".join(rc_lines)],
                child_env,
            )

        os.close(slave_fd)
        return SandboxSession(
            session_id=session_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            mode="local",
            master_fd=master_fd,
            pid=pid,
            cols=cols,
            rows=rows,
        )

    @staticmethod
    def _docker_oidc_env(
        env: dict[str, str],
        workspace_id: str,
    ) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        """Remap host OIDC files into the container and mount the workdir."""
        environment = dict(env)
        volumes: dict[str, dict[str, str]] = {}
        host_root = Path(tempfile.gettempdir()) / "launchpad-oidc" / workspace_id
        if not host_root.is_dir():
            return environment, volumes

        volumes[str(host_root.resolve())] = {
            "bind": _CONTAINER_OIDC_ROOT,
            "mode": "ro",
        }

        gac = environment.get("GOOGLE_APPLICATION_CREDENTIALS")
        if gac:
            host_cfg = Path(gac)
            if host_cfg.is_file():
                try:
                    data = json.loads(host_cfg.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
                if isinstance(data, dict):
                    cred_src = data.get("credential_source")
                    if isinstance(cred_src, dict) and isinstance(cred_src.get("file"), str):
                        token_name = Path(cred_src["file"]).name
                        data = {
                            **data,
                            "credential_source": {
                                **cred_src,
                                "file": f"{_CONTAINER_OIDC_ROOT}/{token_name}",
                            },
                        }
                    docker_cfg = host_root / "gcp_credential_config.docker.json"
                    docker_cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    try:
                        os.chmod(docker_cfg, 0o600)
                    except OSError:
                        pass
                    environment["GOOGLE_APPLICATION_CREDENTIALS"] = (
                        f"{_CONTAINER_OIDC_ROOT}/gcp_credential_config.docker.json"
                    )

        aws_token = environment.get("AWS_WEB_IDENTITY_TOKEN_FILE")
        if aws_token:
            environment["AWS_WEB_IDENTITY_TOKEN_FILE"] = (
                f"{_CONTAINER_OIDC_ROOT}/{Path(aws_token).name}"
            )

        return environment, volumes

    def _interactive_shell_command(
        self,
        env: dict[str, str],
        *,
        docker: bool,
    ) -> str:
        parts: list[str] = []
        if (
            docker
            and "GCP_SA_KEY" in env
            and env.get("GOOGLE_APPLICATION_CREDENTIALS") == "/tmp/gcp-sa.json"
        ):
            parts.append('printf "%s" "$GCP_SA_KEY" > /tmp/gcp-sa.json && chmod 600 /tmp/gcp-sa.json')
        parts.append(f"export PS1={self._shell_quote(_SANDBOX_PS1)}")
        parts.append("echo '[launchpad] interactive sandbox ready'")
        parts.append("echo '[launchpad] workspace: '\"$PWD\"")
        # --norc/--noprofile keeps the path-aware PS1 from being overwritten by bashrc.
        parts.append("exec bash --norc --noprofile -i")
        return "; ".join(parts)

    def _cleanup_session(self, session: SandboxSession) -> None:
        if session.pid:
            try:
                os.killpg(os.getpgid(session.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(session.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if session.master_fd is not None:
            try:
                os.close(session.master_fd)
            except OSError:
                pass
            session.master_fd = None
        if session.container_id and self._settings.sandbox_docker_enabled:
            try:
                import docker

                client = docker.from_env()
                container = client.containers.get(session.container_id)
                container.remove(force=True)
            except Exception:
                logger.exception(
                    "sandbox_container_cleanup_failed",
                    session_id=session.session_id,
                )
        sa_path = session.workspace_path / ".launchpad" / "gcp-sa.json"
        if sa_path.exists():
            sa_path.unlink(missing_ok=True)

    @staticmethod
    def _set_winsize(fd: int, rows: int, cols: int) -> None:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    @staticmethod
    def _set_nonblocking(fd: int) -> None:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    @staticmethod
    def _process_exited(pid: int) -> bool:
        try:
            waited = os.waitpid(pid, os.WNOHANG)
            return waited[0] != 0
        except ChildProcessError:
            return True


_sandbox_runner: SandboxRunner | None = None


def get_sandbox_runner() -> SandboxRunner:
    global _sandbox_runner
    if _sandbox_runner is None:
        _sandbox_runner = SandboxRunner()
    return _sandbox_runner

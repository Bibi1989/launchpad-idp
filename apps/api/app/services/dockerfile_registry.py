"""Build container images and push to Docker Hub, AWS ECR, or GCP Artifact Registry."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger, sanitize_log_message
from app.schemas.dockerfile_schema import (
    AwsEcrCredentials,
    DockerHubCredentials,
    GcpArtifactRegistryCredentials,
    RegistryProvider,
    RegistryTarget,
)

logger = get_logger(__name__)


class DockerfileRegistryError(RuntimeError):
    """Registry authentication, build, or push failed."""


@dataclass(frozen=True, slots=True)
class RegistryBuildResult:
    image_refs: list[str]
    logs: list[str]


def resolve_image_refs(registry: RegistryTarget, tags: list[str]) -> list[str]:
    """Build fully-qualified image references for the selected registry + tags."""
    clean_tags = [_sanitize_tag(t) for t in tags]
    if not clean_tags:
        raise DockerfileRegistryError("At least one image tag is required")

    if registry.provider == RegistryProvider.DOCKER_HUB:
        creds = registry.docker_hub
        if creds is None:
            raise DockerfileRegistryError("docker_hub credentials are required")
        repo = creds.repository.strip().strip("/")
        return [f"{repo}:{tag}" for tag in clean_tags]

    if registry.provider == RegistryProvider.AWS_ECR:
        creds = registry.aws_ecr
        if creds is None:
            raise DockerfileRegistryError("aws_ecr credentials are required")
        host = f"{creds.account_id}.dkr.ecr.{creds.region}.amazonaws.com"
        repo = creds.repository.strip().strip("/")
        return [f"{host}/{repo}:{tag}" for tag in clean_tags]

    if registry.provider == RegistryProvider.GCP_ARTIFACT_REGISTRY:
        creds = registry.gcp_artifact_registry
        if creds is None:
            raise DockerfileRegistryError("gcp_artifact_registry credentials are required")
        host = f"{creds.location}-docker.pkg.dev"
        path = (
            f"{creds.project_id}/{creds.repository.strip().strip('/')}"
            f"/{creds.image_name.strip().strip('/')}"
        )
        return [f"{host}/{path}:{tag}" for tag in clean_tags]

    raise DockerfileRegistryError(f"Unsupported registry provider: {registry.provider}")


def build_and_push_sync(
    *,
    context: Path,
    dockerfile_relpath: str,
    registry: RegistryTarget,
    tags: list[str],
    dockerfile_content_override: str | None = None,
) -> RegistryBuildResult:
    """Blocking docker build + registry login + push for all tags."""
    logs: list[str] = []
    image_refs = resolve_image_refs(registry, tags)

    if not _docker_available():
        raise DockerfileRegistryError(
            "Docker is not available - start Docker Desktop or the Docker daemon"
        )

    work_context = context
    dockerfile_name = dockerfile_relpath
    tmp_root: tempfile.TemporaryDirectory[str] | None = None

    try:
        if dockerfile_content_override is not None:
            tmp_root = tempfile.TemporaryDirectory(prefix="launchpad-df-")
            work_context = Path(tmp_root.name) / "ctx"
            shutil.copytree(
                context,
                work_context,
                ignore=shutil.ignore_patterns(".git", "node_modules", "venv", ".venv"),
                dirs_exist_ok=True,
            )
            override_path = work_context / "Dockerfile.launchpad"
            override_path.write_text(dockerfile_content_override, encoding="utf-8")
            dockerfile_name = "Dockerfile.launchpad"

        dockerfile_path = work_context / dockerfile_name
        if not dockerfile_path.is_file():
            raise DockerfileRegistryError(
                f"Dockerfile not found at {dockerfile_name} relative to build context"
            )

        primary = image_refs[0]
        logs.append(f"Building {primary}")
        _docker_build(
            context=work_context,
            dockerfile=dockerfile_name,
            tag=primary,
            logs=logs,
        )

        for extra in image_refs[1:]:
            _docker_tag(source=primary, target=extra, logs=logs)

        _registry_login(registry, logs=logs)

        for ref in image_refs:
            logs.append(f"Pushing {ref}")
            _registry_push(tag=ref, logs=logs)

        logger.info(
            "dockerfile_registry_push_ok",
            image_count=len(image_refs),
            provider=registry.provider.value,
        )
        return RegistryBuildResult(image_refs=image_refs, logs=logs)
    finally:
        if tmp_root is not None:
            tmp_root.cleanup()


def _sanitize_tag(tag: str) -> str:
    cleaned = tag.strip()
    if not cleaned:
        raise DockerfileRegistryError("Empty image tag")
    if len(cleaned) > 128:
        raise DockerfileRegistryError("Image tag exceeds 128 characters")
    if any(ch.isspace() for ch in cleaned):
        raise DockerfileRegistryError(f"Invalid image tag: {cleaned}")
    return cleaned


def _docker_available() -> bool:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def _docker_build(
    *,
    context: Path,
    dockerfile: str,
    tag: str,
    logs: list[str],
) -> None:
    import docker

    from app.core.config import get_settings

    client = docker.from_env()
    pull_base = bool(get_settings().preview_build_pull_base)
    try:
        _, build_logs = client.images.build(
            path=str(context),
            tag=tag,
            dockerfile=dockerfile,
            rm=True,
            pull=pull_base,
        )
        for chunk in build_logs:
            if "stream" in chunk:
                line = str(chunk["stream"]).strip()
                if line:
                    safe = sanitize_log_message(line)[:500]
                    logs.append(safe)
                    logger.info("dockerfile_build_docker", line=safe)
    except docker.errors.BuildError as exc:
        detail = sanitize_log_message(str(exc))
        raise DockerfileRegistryError(f"docker build failed: {detail[:800]}") from exc
    except Exception as exc:
        raise DockerfileRegistryError(
            f"docker build failed: {sanitize_log_message(str(exc))[:800]}"
        ) from exc


def _docker_tag(*, source: str, target: str, logs: list[str]) -> None:
    import docker

    client = docker.from_env()
    image = client.images.get(source)
    repo, _, tag = target.rpartition(":")
    if not repo or not tag:
        raise DockerfileRegistryError(f"Invalid image reference: {target}")
    image.tag(repo, tag)
    logs.append(f"Tagged {target}")


def _registry_login(registry: RegistryTarget, *, logs: list[str]) -> None:
    import docker

    client = docker.from_env()

    if registry.provider == RegistryProvider.DOCKER_HUB:
        creds = _require(registry.docker_hub, "docker_hub")
        try:
            client.login(
                username=creds.username,
                password=creds.password_or_token,
                registry="https://index.docker.io/v1/",
            )
        except Exception as exc:
            raise DockerfileRegistryError(
                f"Docker Hub login failed: {sanitize_log_message(str(exc))[:400]}"
            ) from exc
        logs.append("Authenticated to Docker Hub")
        return

    if registry.provider == RegistryProvider.AWS_ECR:
        creds = _require(registry.aws_ecr, "aws_ecr")
        password = _ecr_password(creds)
        registry_url = f"https://{creds.account_id}.dkr.ecr.{creds.region}.amazonaws.com"
        try:
            client.login(
                username="AWS",
                password=password,
                registry=registry_url,
            )
        except Exception as exc:
            raise DockerfileRegistryError(
                f"ECR login failed: {sanitize_log_message(str(exc))[:400]}"
            ) from exc
        logs.append(f"Authenticated to ECR ({creds.region})")
        return

    if registry.provider == RegistryProvider.GCP_ARTIFACT_REGISTRY:
        creds = _require(registry.gcp_artifact_registry, "gcp_artifact_registry")
        registry_host = f"{creds.location}-docker.pkg.dev"
        try:
            client.login(
                username="_json_key",
                password=creds.service_account_json,
                registry=f"https://{registry_host}",
            )
        except Exception as exc:
            raise DockerfileRegistryError(
                f"Artifact Registry login failed: {sanitize_log_message(str(exc))[:400]}"
            ) from exc
        logs.append(f"Authenticated to Artifact Registry ({creds.location})")
        return

    raise DockerfileRegistryError(f"Unsupported registry provider: {registry.provider}")


def _ecr_password(creds: AwsEcrCredentials) -> str:
    """Obtain an ECR authorization token via AWS CLI (no boto3 hard dependency)."""
    import os

    aws = shutil.which("aws")
    if aws is None:
        raise DockerfileRegistryError(
            "AWS CLI is required for ECR authentication (install `aws` and retry)"
        )
    env = {
        **os.environ,
        "AWS_ACCESS_KEY_ID": creds.access_key_id,
        "AWS_SECRET_ACCESS_KEY": creds.secret_access_key,
        "AWS_DEFAULT_REGION": creds.region,
    }
    if creds.session_token:
        env["AWS_SESSION_TOKEN"] = creds.session_token

    proc = subprocess.run(
        [
            aws,
            "ecr",
            "get-login-password",
            "--region",
            creds.region,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        detail = sanitize_log_message((proc.stderr or proc.stdout or "ecr auth failed").strip())
        raise DockerfileRegistryError(f"ECR get-login-password failed: {detail[:400]}")
    password = proc.stdout.strip()
    if not password:
        raise DockerfileRegistryError("ECR returned an empty login password")
    return password


def _registry_push(*, tag: str, logs: list[str]) -> None:
    import docker

    client = docker.from_env()
    try:
        push_logs = client.images.push(tag, stream=True, decode=True)
        for chunk in push_logs:
            status = chunk.get("status") or chunk.get("error")
            if status:
                safe = sanitize_log_message(str(status))[:300]
                logs.append(safe)
            if chunk.get("error"):
                raise DockerfileRegistryError(sanitize_log_message(str(chunk["error"]))[:800])
    except DockerfileRegistryError:
        raise
    except Exception as exc:
        raise DockerfileRegistryError(
            f"registry push failed: {sanitize_log_message(str(exc))[:800]}"
        ) from exc


def _require(value: DockerHubCredentials | AwsEcrCredentials | GcpArtifactRegistryCredentials | None, name: str) -> (
    DockerHubCredentials | AwsEcrCredentials | GcpArtifactRegistryCredentials
):
    if value is None:
        raise DockerfileRegistryError(f"{name} credentials are required")
    return value

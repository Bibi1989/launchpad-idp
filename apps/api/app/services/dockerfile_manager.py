"""Orchestrate Dockerfile scan, scaffold, review, GitHub push, and registry builds."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from github import GithubException, InputGitTreeElement

from app.core.logging import get_logger
from app.schemas.dockerfile_schema import (
    DockerfileBuildEnqueueResponse,
    DockerfileBuildJobStatus,
    DockerfileBuildRequest,
    DockerfilePushRequest,
    DockerfilePushResponse,
    DockerfileReviewRequest,
    DockerfileReviewResponse,
    DockerfileScaffoldRequest,
    DockerfileScaffoldResponse,
    DockerfileScanRequest,
    DockerfileScanResponse,
    ProjectStack,
    RepoPushBundleRequest,
    RepoPushBundleResponse,
)
from app.services.dockerfile_jobs import create_build_job
from app.services.dockerfile_scaffold import (
    detect_stack,
    dockerfile_path_for_service,
    scaffold_dockerfile,
)
from app.services.dockerfile_scanner import (
    DockerfileScannerError,
    list_root_file_names,
    scan_repository_dockerfiles,
)
from app.services.dockerfile_security import DockerfileSecurityError, DockerfileSecurityService
from app.services.github_app import get_installation_client

logger = get_logger(__name__)

_DOCKERS_PREFIX = "dockers/"
_SAFE_PATH = re.compile(r"^[a-zA-Z0-9._/-]+$")


class DockerfileManagerError(ValueError):
    """User-facing orchestration error."""


class DockerfileManagerService:
    def __init__(self, security: DockerfileSecurityService | None = None) -> None:
        self._security = security or DockerfileSecurityService()

    async def scan(self, request: DockerfileScanRequest) -> DockerfileScanResponse:
        try:
            dockerfiles, stack, markers, resolved_ref = await asyncio.to_thread(
                scan_repository_dockerfiles,
                installation_id=request.installation_id,
                full_name=request.full_name,
                ref=request.ref,
            )
        except DockerfileScannerError as exc:
            raise DockerfileManagerError(str(exc)) from exc

        return DockerfileScanResponse(
            full_name=request.full_name,
            ref=resolved_ref,
            dockerfiles=dockerfiles,
            detected_stack=stack,
            scaffold_suggested=len(dockerfiles) == 0,
            root_markers=markers,
        )

    async def scaffold(self, request: DockerfileScaffoldRequest) -> DockerfileScaffoldResponse:
        stack = request.stack
        markers: list[str] = []

        if stack is None:
            if request.installation_id and request.full_name:
                try:
                    names, _ = await asyncio.to_thread(
                        list_root_file_names,
                        installation_id=request.installation_id,
                        full_name=request.full_name,
                        ref=request.ref,
                    )
                except DockerfileScannerError as exc:
                    raise DockerfileManagerError(str(exc)) from exc
                stack, markers = detect_stack(names)
            else:
                stack = ProjectStack.UNKNOWN

        app_name = request.app_name or (
            request.full_name.split("/")[-1] if request.full_name else "app"
        )
        content = scaffold_dockerfile(
            stack,
            app_name=app_name,
            listen_port=request.listen_port,
        )
        rel_path = dockerfile_path_for_service(app_name)
        return DockerfileScaffoldResponse(
            stack=stack,
            path=rel_path,
            content=content,
            detected_from=markers,
        )

    async def review(
        self,
        request: DockerfileReviewRequest,
        *,
        correlation_id: str | None = None,
    ) -> DockerfileReviewResponse:
        try:
            report = await self._security.review(
                request.dockerfile_content,
                stack=request.stack,
                source_path=request.source_path,
                correlation_id=correlation_id,
            )
        except DockerfileSecurityError as exc:
            raise DockerfileManagerError(str(exc)) from exc
        return DockerfileReviewResponse(report=report, source_path=request.source_path)

    async def push(self, request: DockerfilePushRequest) -> DockerfilePushResponse:
        path = _normalize_dockers_path(request.path)
        try:
            result = await asyncio.to_thread(
                _commit_dockerfile,
                installation_id=request.installation_id,
                full_name=request.full_name,
                path=path,
                content=request.dockerfile_content,
                commit_message=request.commit_message,
                branch=request.branch,
            )
        except (GithubException, DockerfileManagerError) as exc:
            message = str(exc)
            if isinstance(exc, GithubException):
                message = _friendly_github(exc)
            raise DockerfileManagerError(message) from exc

        return result

    async def push_bundle(self, request: RepoPushBundleRequest) -> RepoPushBundleResponse:
        normalized: dict[str, str] = {}
        for item in request.files:
            path = _normalize_scaffold_path(item.path)
            normalized[path] = item.content
        try:
            result = await asyncio.to_thread(
                _commit_scaffold_bundle,
                installation_id=request.installation_id,
                full_name=request.full_name,
                files=normalized,
                commit_message=request.commit_message,
                branch=request.branch,
            )
        except (GithubException, DockerfileManagerError) as exc:
            message = str(exc)
            if isinstance(exc, GithubException):
                message = _friendly_github(exc)
            raise DockerfileManagerError(message) from exc
        return result

    async def enqueue_build(
        self,
        request: DockerfileBuildRequest,
    ) -> DockerfileBuildEnqueueResponse:
        _validate_registry(request)
        path = request.dockerfile_path.strip().removeprefix("./")
        if path.startswith("/"):
            path = path.lstrip("/")
        if not path or ".." in path.split("/"):
            raise DockerfileManagerError("Invalid dockerfile_path")

        job = await create_build_job()
        # Import locally to avoid circular imports at module load.
        from app.workers.tasks import enqueue_dockerfile_build

        enqueue_dockerfile_build(job.job_id, request.model_dump(mode="json"))
        logger.info(
            "dockerfile_build_enqueued",
            job_id=job.job_id,
            full_name=request.full_name,
            provider=request.registry.provider.value,
            tags=request.tags,
        )
        return DockerfileBuildEnqueueResponse(
            job_id=job.job_id,
            status=DockerfileBuildJobStatus.QUEUED,
        )


def _normalize_dockers_path(path: str) -> str:
    cleaned = path.strip().removeprefix("./")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if cleaned.startswith("/"):
        cleaned = cleaned.lstrip("/")
    if not cleaned:
        raise DockerfileManagerError("path is required")
    parts = cleaned.split("/")
    if any(part == ".." for part in parts):
        raise DockerfileManagerError("path must not contain '..'")
    if not _SAFE_PATH.match(cleaned):
        raise DockerfileManagerError("path contains invalid characters")
    if not cleaned.startswith(_DOCKERS_PREFIX):
        cleaned = f"{_DOCKERS_PREFIX}{cleaned}"
    # Ensure we always write a Dockerfile-like file under dockers/
    name = Path(cleaned).name
    if not name.lower().startswith("dockerfile") and name.lower() != "containerfile":
        cleaned = f"{_DOCKERS_PREFIX}Dockerfile"
    return cleaned


def _normalize_scaffold_path(path: str) -> str:
    cleaned = path.strip().removeprefix("./")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if cleaned.startswith("/"):
        cleaned = cleaned.lstrip("/")
    if not cleaned:
        raise DockerfileManagerError("path is required")
    parts = cleaned.split("/")
    if any(part == ".." for part in parts):
        raise DockerfileManagerError("path must not contain '..'")
    if not _SAFE_PATH.match(cleaned):
        raise DockerfileManagerError("path contains invalid characters")
    allowed = (
        cleaned.startswith("dockers/")
        or cleaned.startswith("infra/")
        or cleaned.startswith("ci/")
        or cleaned in {"docker-compose.yml", "docker-compose.yaml"}
    )
    if not allowed:
        raise DockerfileManagerError(
            "path must be under dockers/, infra/, ci/, or docker-compose.yml"
        )
    return cleaned


def _commit_scaffold_bundle(
    *,
    installation_id: int,
    full_name: str,
    files: dict[str, str],
    commit_message: str,
    branch: str | None,
) -> RepoPushBundleResponse:
    client, resolved_installation_id, _, _ = get_installation_client(
        installation_id=installation_id,
    )
    try:
        repo = client.get_repo(full_name)
    except GithubException as exc:
        raise DockerfileManagerError(
            f"Unable to open repository {full_name}: {_friendly_github(exc)}"
        ) from exc

    target_branch = (branch or repo.default_branch or "main").strip()
    try:
        try:
            ref = repo.get_git_ref(f"heads/{target_branch}")
        except GithubException as missing:
            if getattr(missing, "status", None) != 404:
                raise
            default_branch = str(repo.default_branch or "main")
            if target_branch == default_branch:
                raise
            base_ref = repo.get_git_ref(f"heads/{default_branch}")
            repo.create_git_ref(f"refs/heads/{target_branch}", base_ref.object.sha)
            ref = repo.get_git_ref(f"heads/{target_branch}")
        base_sha = ref.object.sha
        base_tree = repo.get_git_tree(base_sha)
        elements: list[InputGitTreeElement] = []
        for path, content in sorted(files.items()):
            blob = repo.create_git_blob(content, "utf-8")
            elements.append(
                InputGitTreeElement(path=path, mode="100644", type="blob", sha=blob.sha),
            )
        tree = repo.create_git_tree(elements, base_tree)
        parent = repo.get_git_commit(base_sha)
        commit = repo.create_git_commit(commit_message, tree, [parent])
        ref.edit(commit.sha)
    except GithubException as exc:
        raise DockerfileManagerError(
            f"Failed to push scaffold bundle: {_friendly_github(exc)}"
        ) from exc

    paths = sorted(files.keys())
    logger.info(
        "repo_scaffold_bundle_push_ok",
        full_name=repo.full_name,
        paths=paths,
        branch=target_branch,
        installation_id=resolved_installation_id,
    )
    return RepoPushBundleResponse(
        full_name=repo.full_name,
        html_url=repo.html_url,
        default_branch=target_branch,
        paths=paths,
        commit_message=commit_message,
        installation_id=resolved_installation_id,
    )


def _commit_dockerfile(
    *,
    installation_id: int,
    full_name: str,
    path: str,
    content: str,
    commit_message: str,
    branch: str | None,
) -> DockerfilePushResponse:
    client, resolved_installation_id, _, _ = get_installation_client(
        installation_id=installation_id,
    )
    try:
        repo = client.get_repo(full_name)
    except GithubException as exc:
        raise DockerfileManagerError(
            f"Unable to open repository {full_name}: {_friendly_github(exc)}"
        ) from exc

    target_branch = (branch or repo.default_branch or "main").strip()
    try:
        try:
            ref = repo.get_git_ref(f"heads/{target_branch}")
        except GithubException as missing:
            if getattr(missing, "status", None) != 404:
                raise
            default_branch = str(repo.default_branch or "main")
            if target_branch == default_branch:
                raise
            base_ref = repo.get_git_ref(f"heads/{default_branch}")
            repo.create_git_ref(f"refs/heads/{target_branch}", base_ref.object.sha)
            ref = repo.get_git_ref(f"heads/{target_branch}")
        base_sha = ref.object.sha
        base_tree = repo.get_git_tree(base_sha)
        blob = repo.create_git_blob(content, "utf-8")
        tree = repo.create_git_tree(
            [InputGitTreeElement(path=path, mode="100644", type="blob", sha=blob.sha)],
            base_tree,
        )
        parent = repo.get_git_commit(base_sha)
        commit = repo.create_git_commit(commit_message, tree, [parent])
        ref.edit(commit.sha)
    except GithubException as exc:
        raise DockerfileManagerError(
            f"Failed to push Dockerfile: {_friendly_github(exc)}"
        ) from exc

    logger.info(
        "dockerfile_github_push_ok",
        full_name=repo.full_name,
        path=path,
        branch=target_branch,
        installation_id=resolved_installation_id,
    )
    return DockerfilePushResponse(
        full_name=repo.full_name,
        html_url=repo.html_url,
        default_branch=target_branch,
        path=path,
        commit_message=commit_message,
        installation_id=resolved_installation_id,
    )


def _validate_registry(request: DockerfileBuildRequest) -> None:
    from app.schemas.dockerfile_schema import RegistryProvider

    registry = request.registry
    if registry.provider == RegistryProvider.DOCKER_HUB and registry.docker_hub is None:
        raise DockerfileManagerError("docker_hub credentials are required")
    if registry.provider == RegistryProvider.AWS_ECR and registry.aws_ecr is None:
        raise DockerfileManagerError("aws_ecr credentials are required")
    if (
        registry.provider == RegistryProvider.GCP_ARTIFACT_REGISTRY
        and registry.gcp_artifact_registry is None
    ):
        raise DockerfileManagerError("gcp_artifact_registry credentials are required")
    if not request.tags:
        raise DockerfileManagerError("At least one tag is required")


def _friendly_github(exc: GithubException) -> str:
    if isinstance(exc.data, dict):
        message = exc.data.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return str(exc)

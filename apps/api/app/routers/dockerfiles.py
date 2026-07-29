"""Dockerfile management API — scan, scaffold, AI review, GitHub push, registry build."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.deps.auth import CurrentUser
from app.deps.org import CurrentOrg
from app.schemas.dockerfile_schema import (
    DockerfileBuildEnqueueResponse,
    DockerfileBuildJobResponse,
    DockerfileBuildRequest,
    DockerfilePushRequest,
    DockerfilePushResponse,
    DockerfileReviewRequest,
    DockerfileReviewResponse,
    DockerfileScaffoldRequest,
    DockerfileScaffoldResponse,
    DockerfileScanRequest,
    DockerfileScanResponse,
)
from app.services.dockerfile_jobs import get_build_job
from app.services.dockerfile_manager import DockerfileManagerError, DockerfileManagerService

router = APIRouter(prefix="/dockerfiles", tags=["dockerfiles"])


def get_dockerfile_manager() -> DockerfileManagerService:
    return DockerfileManagerService()


@router.post("/scan", response_model=DockerfileScanResponse)
async def scan_dockerfiles(
    payload: DockerfileScanRequest,
    user: CurrentUser,
    org: CurrentOrg,
    service: DockerfileManagerService = Depends(get_dockerfile_manager),
) -> DockerfileScanResponse:
    _ = user, org
    try:
        return await service.scan(payload)
    except DockerfileManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "dockerfile_scan_failed", "message": str(exc)},
        ) from exc


@router.post("/scaffold", response_model=DockerfileScaffoldResponse)
async def scaffold_dockerfile(
    payload: DockerfileScaffoldRequest,
    user: CurrentUser,
    org: CurrentOrg,
    service: DockerfileManagerService = Depends(get_dockerfile_manager),
) -> DockerfileScaffoldResponse:
    _ = user, org
    try:
        return await service.scaffold(payload)
    except DockerfileManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "dockerfile_scaffold_failed", "message": str(exc)},
        ) from exc


@router.post("/review", response_model=DockerfileReviewResponse)
async def review_dockerfile(
    payload: DockerfileReviewRequest,
    request: Request,
    user: CurrentUser,
    org: CurrentOrg,
    service: DockerfileManagerService = Depends(get_dockerfile_manager),
) -> DockerfileReviewResponse:
    _ = user, org
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    try:
        return await service.review(payload, correlation_id=correlation_id)
    except DockerfileManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "dockerfile_review_failed", "message": str(exc)},
        ) from exc


@router.post("/push", response_model=DockerfilePushResponse)
async def push_dockerfile(
    payload: DockerfilePushRequest,
    user: CurrentUser,
    org: CurrentOrg,
    service: DockerfileManagerService = Depends(get_dockerfile_manager),
) -> DockerfilePushResponse:
    _ = user, org
    try:
        return await service.push(payload)
    except DockerfileManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "dockerfile_push_failed", "message": str(exc)},
        ) from exc


@router.post(
    "/build",
    response_model=DockerfileBuildEnqueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def build_and_push_image(
    payload: DockerfileBuildRequest,
    user: CurrentUser,
    org: CurrentOrg,
    service: DockerfileManagerService = Depends(get_dockerfile_manager),
) -> DockerfileBuildEnqueueResponse:
    _ = user, org
    try:
        return await service.enqueue_build(payload)
    except DockerfileManagerError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "dockerfile_build_failed", "message": str(exc)},
        ) from exc


@router.get("/build/{job_id}", response_model=DockerfileBuildJobResponse)
async def get_build_status(
    job_id: str,
    user: CurrentUser,
    org: CurrentOrg,
) -> DockerfileBuildJobResponse:
    _ = user, org
    job = await get_build_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "dockerfile_build_not_found", "message": "Build job not found"},
        )
    return job

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db_session
from app.core.events import WebhookAcceptResponse
from app.core.logging import get_logger
from app.services.webhook import GitHubWebhookService, GitLabWebhookService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)


def get_webhook_service(
    session: AsyncSession = Depends(get_db_session),
) -> GitHubWebhookService:
    return GitHubWebhookService(session)


def get_gitlab_webhook_service(
    session: AsyncSession = Depends(get_db_session),
) -> GitLabWebhookService:
    return GitLabWebhookService(session)


@router.post("/github", response_model=WebhookAcceptResponse)
async def github_webhook(
    request: Request,
    service: GitHubWebhookService = Depends(get_webhook_service),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
) -> WebhookAcceptResponse:
    settings = get_settings()
    secret = (settings.webhook_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "webhook_not_configured",
                "message": "WEBHOOK_SECRET is not configured on the API",
            },
        )

    body = await request.body()
    if not service.verify_signature(
        body=body,
        signature_header=x_hub_signature_256,
        secret=secret,
    ):
        logger.warning(
            "webhook_signature_rejected",
            has_signature=bool(x_hub_signature_256),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_webhook_signature",
                "message": "Invalid X-Hub-Signature-256",
            },
        )

    try:
        payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_webhook_payload",
                "message": "Request body must be valid JSON",
            },
        ) from exc

    event_name = (x_github_event or "").strip() or "unknown"
    correlation_id = getattr(request.state, "correlation_id", "webhook")
    result = await service.process_event(
        event_name=event_name,
        payload=payload,
        correlation_id=correlation_id,
    )
    return WebhookAcceptResponse(
        accepted=result.accepted,
        event=result.event,
        matched_environments=[str(env_id) for env_id in result.matched_environment_ids],
        message=result.message,
    )


@router.post("/gitlab", response_model=WebhookAcceptResponse)
async def gitlab_webhook(
    request: Request,
    service: GitLabWebhookService = Depends(get_gitlab_webhook_service),
    x_gitlab_token: str | None = Header(default=None, alias="X-Gitlab-Token"),
) -> WebhookAcceptResponse:
    settings = get_settings()
    secret = (settings.webhook_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "webhook_not_configured",
                "message": "WEBHOOK_SECRET is not configured on the API",
            },
        )

    body = await request.body()
    if not service.verify_signature(
        body=body,
        signature_header=x_gitlab_token,
        secret=secret,
    ):
        logger.warning(
            "webhook_signature_rejected",
            has_signature=bool(x_gitlab_token),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_webhook_signature",
                "message": "Invalid X-Gitlab-Token",
            },
        )

    try:
        payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_webhook_payload",
                "message": "Request body must be valid JSON",
            },
        ) from exc

    correlation_id = getattr(request.state, "correlation_id", "webhook")
    result = await service.process_event(
        event_name="push",
        payload=payload,
        correlation_id=correlation_id,
    )

    return WebhookAcceptResponse(
        accepted=result.accepted,
        event=result.event,
        matched_environments=[str(env_id) for env_id in result.matched_environment_ids],
        message=result.message,
    )

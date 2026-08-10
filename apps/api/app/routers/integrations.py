"""Org-scoped Slack and Jira integration endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.schemas.integrations import (
    JiraIntegrationStatus,
    JiraIntegrationUpdate,
    JiraIssueCreateRequest,
    JiraIssueRead,
    SlackIntegrationStatus,
    SlackIntegrationUpdate,
)
from app.services.environment import EnvironmentService
from app.services.integrations.notifier import IntegrationNotifier
from app.services.org_integrations import OrgIntegrationsService

router = APIRouter(prefix="/integrations", tags=["integrations"])


def get_org_integrations_service(
    session: AsyncSession = Depends(get_db_session),
) -> OrgIntegrationsService:
    return OrgIntegrationsService(session)


def get_environment_service(
    session: AsyncSession = Depends(get_db_session),
) -> EnvironmentService:
    return EnvironmentService(session)


@router.get("/orgs/{org_id}/slack", response_model=SlackIntegrationStatus)
async def get_slack_integration(
    org_id: UUID,
    user: CurrentUser,
    service: OrgIntegrationsService = Depends(get_org_integrations_service),
) -> SlackIntegrationStatus:
    return await service.get_slack(user=user, org_id=org_id)


@router.put("/orgs/{org_id}/slack", response_model=SlackIntegrationStatus)
async def upsert_slack_integration(
    org_id: UUID,
    payload: SlackIntegrationUpdate,
    user: CurrentUser,
    service: OrgIntegrationsService = Depends(get_org_integrations_service),
    session: AsyncSession = Depends(get_db_session),
) -> SlackIntegrationStatus:
    result = await service.upsert_slack(user=user, org_id=org_id, payload=payload)
    await session.commit()
    return result


@router.delete("/orgs/{org_id}/slack", response_model=SlackIntegrationStatus)
async def disconnect_slack_integration(
    org_id: UUID,
    user: CurrentUser,
    service: OrgIntegrationsService = Depends(get_org_integrations_service),
    session: AsyncSession = Depends(get_db_session),
) -> SlackIntegrationStatus:
    result = await service.disconnect_slack(user=user, org_id=org_id)
    await session.commit()
    return result


@router.get("/orgs/{org_id}/jira", response_model=JiraIntegrationStatus)
async def get_jira_integration(
    org_id: UUID,
    user: CurrentUser,
    service: OrgIntegrationsService = Depends(get_org_integrations_service),
) -> JiraIntegrationStatus:
    return await service.get_jira(user=user, org_id=org_id)


@router.put("/orgs/{org_id}/jira", response_model=JiraIntegrationStatus)
async def upsert_jira_integration(
    org_id: UUID,
    payload: JiraIntegrationUpdate,
    user: CurrentUser,
    service: OrgIntegrationsService = Depends(get_org_integrations_service),
    session: AsyncSession = Depends(get_db_session),
) -> JiraIntegrationStatus:
    result = await service.upsert_jira(user=user, org_id=org_id, payload=payload)
    await session.commit()
    return result


@router.delete("/orgs/{org_id}/jira", response_model=JiraIntegrationStatus)
async def disconnect_jira_integration(
    org_id: UUID,
    user: CurrentUser,
    service: OrgIntegrationsService = Depends(get_org_integrations_service),
    session: AsyncSession = Depends(get_db_session),
) -> JiraIntegrationStatus:
    result = await service.disconnect_jira(user=user, org_id=org_id)
    await session.commit()
    return result


@router.post(
    "/environments/{environment_id}/jira-issue",
    response_model=JiraIssueRead,
)
async def create_or_link_jira_issue(
    environment_id: UUID,
    payload: JiraIssueCreateRequest,
    user: CurrentUser,
    env_service: EnvironmentService = Depends(get_environment_service),
    session: AsyncSession = Depends(get_db_session),
) -> JiraIssueRead:
    environment = await env_service.get_environment_entity(environment_id, user)
    notifier = IntegrationNotifier(session)
    result = await notifier.create_jira_for_environment(
        environment,
        summary=payload.summary,
        link_only_key=payload.link_only_key,
        error_detail=environment.error_message,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "jira_unavailable",
                "message": "Jira is not connected for this org, or issue creation failed",
            },
        )
    key, url, created = result
    await session.commit()
    return JiraIssueRead(issue_key=key, issue_url=url, created=created)

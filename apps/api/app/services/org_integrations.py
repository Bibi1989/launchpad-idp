"""CRUD for org-scoped Slack and Jira integration settings."""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secrets import encrypt_secret
from app.models.domain import OrgIntegration, OrgRole, User
from app.schemas.integrations import (
    JiraIntegrationStatus,
    JiraIntegrationUpdate,
    SlackIntegrationStatus,
    SlackIntegrationUpdate,
)
from app.services.orgs import OrganizationService, role_at_least


class OrgIntegrationsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orgs = OrganizationService(session)

    async def get_slack(self, *, user: User, org_id: UUID) -> SlackIntegrationStatus:
        await self._orgs.resolve_context(user=user, org_id=org_id)
        row = await self._get_row(org_id)
        return self._slack_status(row)

    async def upsert_slack(
        self,
        *,
        user: User,
        org_id: UUID,
        payload: SlackIntegrationUpdate,
    ) -> SlackIntegrationStatus:
        ctx = await self._orgs.resolve_context(user=user, org_id=org_id)
        self._require_admin(ctx.role)
        row = await self._get_or_create(org_id)

        if payload.clear_webhook:
            row.encrypted_slack_webhook_url = None
        elif payload.webhook_url:
            row.encrypted_slack_webhook_url = encrypt_secret(payload.webhook_url)

        if payload.notify_ready is not None:
            row.slack_notify_ready = payload.notify_ready
        if payload.notify_failed is not None:
            row.slack_notify_failed = payload.notify_failed
        if payload.notify_ttl_warning is not None:
            row.slack_notify_ttl_warning = payload.notify_ttl_warning
        if payload.notify_cost_cap is not None:
            row.slack_notify_cost_cap = payload.notify_cost_cap
        if payload.project_ids is not None:
            row.slack_project_ids_json = json.dumps([str(pid) for pid in payload.project_ids])

        await self._session.flush()
        await self._session.refresh(row)
        return self._slack_status(row)

    async def disconnect_slack(self, *, user: User, org_id: UUID) -> SlackIntegrationStatus:
        ctx = await self._orgs.resolve_context(user=user, org_id=org_id)
        self._require_admin(ctx.role)
        row = await self._get_row(org_id)
        if row is not None:
            row.encrypted_slack_webhook_url = None
            await self._session.flush()
            await self._session.refresh(row)
        return self._slack_status(row)

    async def get_jira(self, *, user: User, org_id: UUID) -> JiraIntegrationStatus:
        await self._orgs.resolve_context(user=user, org_id=org_id)
        row = await self._get_row(org_id)
        return self._jira_status(row)

    async def upsert_jira(
        self,
        *,
        user: User,
        org_id: UUID,
        payload: JiraIntegrationUpdate,
    ) -> JiraIntegrationStatus:
        ctx = await self._orgs.resolve_context(user=user, org_id=org_id)
        self._require_admin(ctx.role)
        row = await self._get_or_create(org_id)

        if payload.clear:
            row.jira_site_url = None
            row.jira_email = None
            row.encrypted_jira_api_token = None
            row.jira_project_key = None
            row.jira_issue_type = "Bug"
            row.jira_auto_create_on_failure = False
            await self._session.flush()
            await self._session.refresh(row)
            return self._jira_status(row)

        if payload.site_url is not None:
            row.jira_site_url = payload.site_url
        if payload.email is not None:
            row.jira_email = payload.email
        if payload.api_token is not None:
            row.encrypted_jira_api_token = encrypt_secret(payload.api_token)
        if payload.project_key is not None:
            row.jira_project_key = payload.project_key
        if payload.issue_type is not None and payload.issue_type.strip():
            row.jira_issue_type = payload.issue_type.strip()
        if payload.auto_create_on_failure is not None:
            row.jira_auto_create_on_failure = payload.auto_create_on_failure

        if not (
            row.jira_site_url
            and row.jira_email
            and row.encrypted_jira_api_token
            and row.jira_project_key
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "jira_incomplete",
                    "message": "Jira requires site URL, email, API token, and project key",
                },
            )

        await self._session.flush()
        await self._session.refresh(row)
        return self._jira_status(row)

    async def disconnect_jira(self, *, user: User, org_id: UUID) -> JiraIntegrationStatus:
        return await self.upsert_jira(
            user=user,
            org_id=org_id,
            payload=JiraIntegrationUpdate(clear=True),
        )

    async def _get_row(self, org_id: UUID) -> OrgIntegration | None:
        result = await self._session.execute(
            select(OrgIntegration).where(OrgIntegration.org_id == org_id)
        )
        return result.scalar_one_or_none()

    async def _get_or_create(self, org_id: UUID) -> OrgIntegration:
        row = await self._get_row(org_id)
        if row is not None:
            return row
        row = OrgIntegration(org_id=org_id)
        self._session.add(row)
        await self._session.flush()
        return row

    @staticmethod
    def _require_admin(role: OrgRole) -> None:
        if not role_at_least(role, OrgRole.ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "forbidden",
                    "message": "Org admin required to manage integrations",
                },
            )

    @staticmethod
    def _slack_status(row: OrgIntegration | None) -> SlackIntegrationStatus:
        if row is None:
            return SlackIntegrationStatus()
        project_ids: list[UUID] = []
        raw = (row.slack_project_ids_json or "").strip()
        if raw:
            try:
                project_ids = [UUID(str(item)) for item in json.loads(raw)]
            except (json.JSONDecodeError, ValueError, TypeError):
                project_ids = []
        connected = bool(row.encrypted_slack_webhook_url)
        return SlackIntegrationStatus(
            connected=connected,
            notify_ready=row.slack_notify_ready,
            notify_failed=row.slack_notify_failed,
            notify_ttl_warning=row.slack_notify_ttl_warning,
            notify_cost_cap=row.slack_notify_cost_cap,
            project_ids=project_ids,
            webhook_configured=connected,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _jira_status(row: OrgIntegration | None) -> JiraIntegrationStatus:
        if row is None:
            return JiraIntegrationStatus()
        connected = bool(
            row.jira_site_url
            and row.jira_email
            and row.encrypted_jira_api_token
            and row.jira_project_key
        )
        return JiraIntegrationStatus(
            connected=connected,
            site_url=row.jira_site_url,
            email=row.jira_email,
            project_key=row.jira_project_key,
            issue_type=row.jira_issue_type or "Bug",
            auto_create_on_failure=row.jira_auto_create_on_failure,
            token_configured=bool(row.encrypted_jira_api_token),
            updated_at=row.updated_at,
        )

"""Fire-and-forget integration notifications for environment lifecycle events."""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.secrets import decrypt_secret
from app.models.domain import Environment, OrgIntegration
from app.repositories.environment import DeploymentLogRepository
from app.services.integrations.jira import add_jira_comment, create_jira_issue, jira_browse_url
from app.services.integrations.slack import build_environment_blocks, post_slack_webhook

logger = get_logger(__name__)

SlackEvent = Literal["ready", "failed", "ttl_warning", "cost_cap", "ttl_expired"]


class IntegrationNotifier:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()

    async def notify_environment_event(
        self,
        environment_id: UUID,
        *,
        event: SlackEvent,
        message: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Notify Slack (and optionally auto-create Jira on failure). Never raises."""
        try:
            await self._notify(environment_id, event=event, message=message, correlation_id=correlation_id)
        except Exception as exc:  # noqa: BLE001 - never fail provision/teardown
            logger.warning(
                "integration_notify_failed",
                environment_id=str(environment_id),
                slack_event=event,
                error=str(exc),
            )

    async def _notify(
        self,
        environment_id: UUID,
        *,
        event: SlackEvent,
        message: str | None,
        correlation_id: str | None,
    ) -> None:
        environment = await self._load_environment(environment_id)
        if environment is None or environment.org_id is None:
            return

        integration = await self._load_integration(environment.org_id)
        if integration is None:
            return

        if not self._project_allowed(integration, environment):
            return

        if event in {"ready", "failed", "ttl_warning", "cost_cap", "ttl_expired"}:
            await self._maybe_slack(
                integration,
                environment,
                event=event,
                message=message,
                correlation_id=correlation_id,
            )

        if event == "failed":
            await self._maybe_auto_jira(integration, environment, message=message)

    async def create_jira_for_environment(
        self,
        environment: Environment,
        *,
        summary: str | None = None,
        link_only_key: str | None = None,
        error_detail: str | None = None,
    ) -> tuple[str, str, bool] | None:
        """Create or link a Jira issue. Returns (key, url, created) or None."""
        if environment.org_id is None:
            return None
        integration = await self._load_integration(environment.org_id)
        if integration is None or not self._jira_connected(integration):
            return None

        site = (integration.jira_site_url or "").rstrip("/")
        if link_only_key:
            key = link_only_key.strip().upper()
            url = jira_browse_url(site, key)
            environment.jira_issue_key = key
            environment.jira_issue_url = url
            await self._session.flush()
            return key, url, False

        if environment.jira_issue_key:
            return (
                environment.jira_issue_key,
                environment.jira_issue_url or jira_browse_url(site, environment.jira_issue_key),
                False,
            )

        token = decrypt_secret(integration.encrypted_jira_api_token or "")
        portal = self._portal_url(environment)
        detail_parts = [
            f"Environment: {environment.name}",
            f"Status: {environment.status.value if hasattr(environment.status, 'value') else environment.status}",
            f"Branch: {environment.git_branch}",
            f"Repo: {environment.git_repo_url}",
        ]
        if environment.latest_commit_sha:
            detail_parts.append(f"Commit: {environment.latest_commit_sha}")
        if portal:
            detail_parts.append(f"Portal: {portal}")
        if error_detail or environment.error_message:
            detail_parts.append(f"Error: {error_detail or environment.error_message}")

        issue_summary = summary or f"[Launchpad] Preview failed: {environment.name}"
        result = create_jira_issue(
            site_url=site,
            email=integration.jira_email or "",
            api_token=token,
            project_key=integration.jira_project_key or "",
            issue_type=integration.jira_issue_type or "Bug",
            summary=issue_summary,
            description="\n".join(detail_parts),
        )
        if result is None:
            return None
        environment.jira_issue_key = result.key
        environment.jira_issue_url = result.url
        await self._session.flush()

        log_snippet = await self._recent_log_snippet(environment.id, limit=40)
        if log_snippet:
            add_jira_comment(
                site_url=site,
                email=integration.jira_email or "",
                api_token=token,
                issue_key=result.key,
                body_text=f"Recent Launchpad deployment logs:\n{log_snippet}",
            )
        return result.key, result.url, True

    async def _recent_log_snippet(self, environment_id: UUID, *, limit: int = 40) -> str:
        log_repo = DeploymentLogRepository(self._session)
        rows = await log_repo.list_for_environment(environment_id, limit=max(limit * 3, limit))
        if not rows:
            return ""
        recent = rows[-limit:]
        lines = [row.message.strip() for row in recent if (row.message or "").strip()]
        return "\n".join(lines)[:3500]

    async def _maybe_slack(
        self,
        integration: OrgIntegration,
        environment: Environment,
        *,
        event: SlackEvent,
        message: str | None,
        correlation_id: str | None,
    ) -> None:
        if not integration.encrypted_slack_webhook_url:
            return
        if event == "ready" and not integration.slack_notify_ready:
            return
        if event == "failed" and not integration.slack_notify_failed:
            return
        if event in {"ttl_warning", "ttl_expired"} and not integration.slack_notify_ttl_warning:
            return
        if event == "cost_cap" and not integration.slack_notify_cost_cap:
            return

        flag_key = {
            "ttl_warning": "ttl_warning",
            "cost_cap": "cost_cap",
            "ttl_expired": "ttl_expired",
        }.get(event)
        if flag_key and self._has_notification_flag(environment, flag_key):
            return

        webhook = decrypt_secret(integration.encrypted_slack_webhook_url)
        titles = {
            "ready": "Preview ready",
            "failed": "Preview failed",
            "ttl_warning": "Preview TTL warning",
            "ttl_expired": "Preview TTL expired",
            "cost_cap": "Preview soft cost cap",
        }
        title = titles.get(event, "Launchpad environment update")
        status = environment.status.value if hasattr(environment.status, "value") else str(environment.status)
        portal = self._portal_url(environment)
        workspace_label = None
        if environment.workspace is not None:
            workspace_label = getattr(environment.workspace, "name", None) or str(
                environment.workspace_id
            )

        blocks = build_environment_blocks(
            title=title,
            env_name=environment.name,
            status=status,
            portal_url=portal,
            preview_url=environment.preview_url,
            workspace_label=workspace_label,
            correlation_id=correlation_id,
            detail=message or environment.error_message,
        )
        text = f"{title}: {environment.name} ({status})"
        ok = post_slack_webhook(webhook, text=text, blocks=blocks)
        if ok and flag_key:
            self._set_notification_flag(environment, flag_key)
            await self._session.flush()
        logger.info(
            "slack_notify",
            environment_id=str(environment.id),
            event=event,
            ok=ok,
        )

    async def _maybe_auto_jira(
        self,
        integration: OrgIntegration,
        environment: Environment,
        *,
        message: str | None,
    ) -> None:
        if not integration.jira_auto_create_on_failure:
            return
        if not self._jira_connected(integration):
            return
        if environment.jira_issue_key:
            return
        result = await self.create_jira_for_environment(
            environment,
            error_detail=message,
        )
        if result:
            logger.info(
                "jira_auto_created",
                environment_id=str(environment.id),
                issue_key=result[0],
            )

    async def _load_environment(self, environment_id: UUID) -> Environment | None:
        result = await self._session.execute(
            select(Environment)
            .where(Environment.id == environment_id)
            .options(selectinload(Environment.workspace))
        )
        return result.scalar_one_or_none()

    async def _load_integration(self, org_id: UUID) -> OrgIntegration | None:
        result = await self._session.execute(
            select(OrgIntegration).where(OrgIntegration.org_id == org_id)
        )
        return result.scalar_one_or_none()

    def _project_allowed(self, integration: OrgIntegration, environment: Environment) -> bool:
        raw = (integration.slack_project_ids_json or "").strip()
        if not raw:
            return True
        try:
            allowed = {str(item) for item in json.loads(raw)}
        except json.JSONDecodeError:
            return True
        if not allowed:
            return True
        workspace = environment.workspace
        if workspace is None and environment.workspace_id is not None:
            return True
        project_id = getattr(workspace, "project_id", None) if workspace else None
        if project_id is None:
            return True
        return str(project_id) in allowed

    def _portal_url(self, environment: Environment) -> str:
        base = self._settings.preview_public_base_url.rstrip("/")
        return f"{base}/p/{environment.id}"

    @staticmethod
    def _jira_connected(integration: OrgIntegration) -> bool:
        return bool(
            integration.jira_site_url
            and integration.jira_email
            and integration.encrypted_jira_api_token
            and integration.jira_project_key
        )

    @staticmethod
    def _flags(environment: Environment) -> dict[str, bool]:
        raw = (environment.notification_flags_json or "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): bool(v) for k, v in data.items()}

    def _has_notification_flag(self, environment: Environment, key: str) -> bool:
        return bool(self._flags(environment).get(key))

    def _set_notification_flag(self, environment: Environment, key: str) -> None:
        flags = self._flags(environment)
        flags[key] = True
        environment.notification_flags_json = json.dumps(flags)


async def notify_environment_event_safe(
    session: AsyncSession,
    environment_id: UUID,
    *,
    event: SlackEvent,
    message: str | None = None,
    correlation_id: str | None = None,
) -> None:
    notifier = IntegrationNotifier(session)
    await notifier.notify_environment_event(
        environment_id,
        event=event,
        message=message,
        correlation_id=correlation_id,
    )

"""Org Slack/Jira integrations + environment Jira issue fields.

Revision ID: 0026_org_integrations_jira
Revises: 0025_env_preview_endpoints
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_org_integrations_jira"
down_revision: str | None = "0025_env_preview_endpoints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "org_integrations" not in tables:
        op.create_table(
            "org_integrations",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("encrypted_slack_webhook_url", sa.Text(), nullable=True),
            sa.Column(
                "slack_notify_ready",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "slack_notify_failed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "slack_notify_ttl_warning",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "slack_notify_cost_cap",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column("slack_project_ids_json", sa.Text(), nullable=True),
            sa.Column("jira_site_url", sa.String(length=512), nullable=True),
            sa.Column("jira_email", sa.String(length=256), nullable=True),
            sa.Column("encrypted_jira_api_token", sa.Text(), nullable=True),
            sa.Column("jira_project_key", sa.String(length=64), nullable=True),
            sa.Column(
                "jira_issue_type",
                sa.String(length=64),
                nullable=False,
                server_default="Bug",
            ),
            sa.Column(
                "jira_auto_create_on_failure",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["org_id"],
                ["organizations.id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("org_id", name="uq_org_integrations_org_id"),
        )
        op.create_index(
            "ix_org_integrations_org_id",
            "org_integrations",
            ["org_id"],
            unique=True,
        )

    env_columns = {col["name"] for col in inspector.get_columns("environments")}
    if "jira_issue_key" not in env_columns:
        op.add_column(
            "environments",
            sa.Column("jira_issue_key", sa.String(length=64), nullable=True),
        )
    if "jira_issue_url" not in env_columns:
        op.add_column(
            "environments",
            sa.Column("jira_issue_url", sa.String(length=512), nullable=True),
        )
    if "notification_flags_json" not in env_columns:
        op.add_column(
            "environments",
            sa.Column("notification_flags_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    env_columns = {col["name"] for col in inspector.get_columns("environments")}
    if "notification_flags_json" in env_columns:
        op.drop_column("environments", "notification_flags_json")
    if "jira_issue_url" in env_columns:
        op.drop_column("environments", "jira_issue_url")
    if "jira_issue_key" in env_columns:
        op.drop_column("environments", "jira_issue_key")

    tables = set(inspector.get_table_names())
    if "org_integrations" in tables:
        op.drop_index("ix_org_integrations_org_id", table_name="org_integrations")
        op.drop_table("org_integrations")

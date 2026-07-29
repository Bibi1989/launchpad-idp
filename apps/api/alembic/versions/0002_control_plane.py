"""Replace initial schema with Environment + DeploymentLog control-plane models.

Revision ID: 0002_control_plane
Revises: 0001_initial
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_control_plane"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_provisioning_jobs_correlation_id", table_name="provisioning_jobs")
    op.drop_index("ix_provisioning_jobs_status", table_name="provisioning_jobs")
    op.drop_index("ix_provisioning_jobs_environment_id", table_name="provisioning_jobs")
    op.drop_table("provisioning_jobs")

    op.drop_index("ix_environments_ttl_expiration", table_name="environments")
    op.drop_index("ix_environments_status", table_name="environments")
    op.drop_index("ix_environments_owner", table_name="environments")
    op.drop_table("environments")

    op.create_table(
        "environments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("git_branch", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("namespace_name", sa.String(length=253), nullable=False),
        sa.Column("ttl_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cost_estimate_hourly", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("namespace_name"),
    )
    op.create_index("ix_environments_status", "environments", ["status"])
    op.create_index("ix_environments_ttl_expires_at", "environments", ["ttl_expires_at"])
    op.create_index("ix_environments_name", "environments", ["name"])

    op.create_table(
        "deployment_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("log_level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deployment_logs_environment_id", "deployment_logs", ["environment_id"])
    op.create_index("ix_deployment_logs_timestamp", "deployment_logs", ["timestamp"])
    op.create_index(
        "ix_deployment_logs_environment_timestamp",
        "deployment_logs",
        ["environment_id", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_logs_environment_timestamp", table_name="deployment_logs")
    op.drop_index("ix_deployment_logs_timestamp", table_name="deployment_logs")
    op.drop_index("ix_deployment_logs_environment_id", table_name="deployment_logs")
    op.drop_table("deployment_logs")

    op.drop_index("ix_environments_name", table_name="environments")
    op.drop_index("ix_environments_ttl_expires_at", table_name="environments")
    op.drop_index("ix_environments_status", table_name="environments")
    op.drop_table("environments")

    op.create_table(
        "environments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("owner", sa.String(length=256), nullable=False),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("namespace", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ttl_expiration", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("namespace"),
    )
    op.create_index("ix_environments_owner", "environments", ["owner"])
    op.create_index("ix_environments_status", "environments", ["status"])
    op.create_index("ix_environments_ttl_expiration", "environments", ["ttl_expiration"])

    op.create_table(
        "provisioning_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("environment_id", sa.UUID(), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("log_output", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provisioning_jobs_environment_id", "provisioning_jobs", ["environment_id"])
    op.create_index("ix_provisioning_jobs_status", "provisioning_jobs", ["status"])
    op.create_index("ix_provisioning_jobs_correlation_id", "provisioning_jobs", ["correlation_id"])

"""Initial environments and provisioning jobs schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_provisioning_jobs_correlation_id", table_name="provisioning_jobs")
    op.drop_index("ix_provisioning_jobs_status", table_name="provisioning_jobs")
    op.drop_index("ix_provisioning_jobs_environment_id", table_name="provisioning_jobs")
    op.drop_table("provisioning_jobs")
    op.drop_index("ix_environments_ttl_expiration", table_name="environments")
    op.drop_index("ix_environments_status", table_name="environments")
    op.drop_index("ix_environments_owner", table_name="environments")
    op.drop_table("environments")

"""Hybrid local/edge agent node registry.

Revision ID: 0027_agent_nodes
Revises: 0026_org_integrations_jira
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_agent_nodes"
down_revision: str | None = "0026_org_integrations_jira"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "agent_nodes" not in tables:
        op.create_table(
            "agent_nodes",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="PENDING",
            ),
            sa.Column("enrollment_token_hash", sa.String(length=64), nullable=True),
            sa.Column("enrollment_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("encrypted_agent_secret", sa.Text(), nullable=True),
            sa.Column("labels_json", sa.Text(), nullable=True),
            sa.Column("hostname", sa.String(length=253), nullable=True),
            sa.Column("platform", sa.String(length=64), nullable=True),
            sa.Column("agent_version", sa.String(length=32), nullable=True),
            sa.Column("cpu_cores", sa.Integer(), nullable=True),
            sa.Column("mem_total_mb", sa.Integer(), nullable=True),
            sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cpu_percent", sa.Numeric(5, 2), nullable=True),
            sa.Column("mem_percent", sa.Numeric(5, 2), nullable=True),
            sa.Column("disk_percent", sa.Numeric(5, 2), nullable=True),
            sa.Column("docker_status", sa.String(length=32), nullable=True),
            sa.Column("containers_json", sa.Text(), nullable=True),
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
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_agent_nodes_org_id", "agent_nodes", ["org_id"])
        op.create_index("ix_agent_nodes_owner_id", "agent_nodes", ["owner_id"])
        op.create_index("ix_agent_nodes_status", "agent_nodes", ["status"])
        op.create_index(
            "ix_agent_nodes_enrollment_token_hash",
            "agent_nodes",
            ["enrollment_token_hash"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "agent_nodes" in tables:
        op.drop_index("ix_agent_nodes_enrollment_token_hash", table_name="agent_nodes")
        op.drop_index("ix_agent_nodes_status", table_name="agent_nodes")
        op.drop_index("ix_agent_nodes_owner_id", table_name="agent_nodes")
        op.drop_index("ix_agent_nodes_org_id", table_name="agent_nodes")
        op.drop_table("agent_nodes")

"""Add provisioning workspaces and terminal session tracking.

Revision ID: 0003_provisioning
Revises: 0002_control_plane
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_provisioning"
down_revision: str | None = "0002_control_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provisioning_workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("root_dir", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("encrypted_credentials", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provisioning_workspaces_status", "provisioning_workspaces", ["status"])

    op.create_table(
        "terminal_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["provisioning_workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_terminal_sessions_workspace_id", "terminal_sessions", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_terminal_sessions_workspace_id", table_name="terminal_sessions")
    op.drop_table("terminal_sessions")
    op.drop_index("ix_provisioning_workspaces_status", table_name="provisioning_workspaces")
    op.drop_table("provisioning_workspaces")

"""Add starred_at to provisioning_workspaces for catalog bookmarks.

Revision ID: 0022_workspace_starred
Revises: 0021_catalog_ws_cascade
Create Date: 2026-08-07
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_workspace_starred"
down_revision: str | None = "0021_catalog_ws_cascade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "provisioning_workspaces",
        sa.Column("starred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_provisioning_workspaces_starred_at",
        "provisioning_workspaces",
        ["starred_at"],
    )
    # Backfill: workspaces already linked from the catalog count as starred.
    op.execute(
        """
        UPDATE provisioning_workspaces AS w
        SET starred_at = COALESCE(w.created_at, CURRENT_TIMESTAMP)
        WHERE EXISTS (
            SELECT 1 FROM catalog_services AS c
            WHERE c.workspace_id = w.id
        )
        AND w.starred_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provisioning_workspaces_starred_at",
        table_name="provisioning_workspaces",
    )
    op.drop_column("provisioning_workspaces", "starred_at")

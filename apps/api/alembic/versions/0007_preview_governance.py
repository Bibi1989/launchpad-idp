"""Add preview governance and runtime metadata fields.

Revision ID: 0007_preview_governance
Revises: 0006_preview_url
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_preview_governance"
down_revision: str | None = "0006_preview_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "environments",
        sa.Column("provider", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "environments",
        sa.Column("workload_image", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "environments",
        sa.Column("node_port", sa.Integer(), nullable=True),
    )
    op.add_column(
        "environments",
        sa.Column("github_pr_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "environments",
        sa.Column("github_pr_url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("environments", "github_pr_url")
    op.drop_column("environments", "github_pr_number")
    op.drop_column("environments", "node_port")
    op.drop_column("environments", "workload_image")
    op.drop_column("environments", "provider")

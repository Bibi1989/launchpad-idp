"""Add preview_url and template_id for one-click preview launches.

Revision ID: 0006_preview_url
Revises: 0005_gitops_rebuild
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_preview_url"
down_revision: str | None = "0005_gitops_rebuild"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "environments",
        sa.Column("preview_url", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "environments",
        sa.Column("template_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("environments", "template_id")
    op.drop_column("environments", "preview_url")

"""Add deploy_mode and manifest_packaging to environments.

Revision ID: 0009_deploy_mode
Revises: 0008_audit_logs
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_deploy_mode"
down_revision: str | None = "0008_audit_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "environments",
        sa.Column("deploy_mode", sa.String(length=16), nullable=False, server_default="preview"),
    )
    op.add_column(
        "environments",
        sa.Column("manifest_packaging", sa.String(length=32), nullable=True),
    )
    op.alter_column("environments", "deploy_mode", server_default=None)


def downgrade() -> None:
    op.drop_column("environments", "manifest_packaging")
    op.drop_column("environments", "deploy_mode")

"""Add GitLab OAuth refresh token storage.

Revision ID: 0023_gitlab_refresh_token
Revises: 0022_workspace_starred
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_gitlab_refresh_token"
down_revision: str | None = "0022_workspace_starred"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gitlab_connections",
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
    )
    op.add_column(
        "gitlab_connections",
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gitlab_connections", "token_expires_at")
    op.drop_column("gitlab_connections", "encrypted_refresh_token")

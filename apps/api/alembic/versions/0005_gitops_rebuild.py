"""Add latest_commit_sha for GitOps rebuild tracking.

Revision ID: 0005_gitops_rebuild
Revises: 0004_auth_ownership
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_gitops_rebuild"
down_revision: str | None = "0004_auth_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "environments",
        sa.Column("latest_commit_sha", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_environments_git_branch",
        "environments",
        ["git_branch"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_environments_git_branch", table_name="environments")
    op.drop_column("environments", "latest_commit_sha")

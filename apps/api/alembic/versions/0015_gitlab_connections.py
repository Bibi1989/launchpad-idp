"""Add gitlab_connections for per-user GitLab OAuth/PAT."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_gitlab_connections"
down_revision = "0014_catalog_services"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gitlab_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("base_url", sa.String(length=256), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("token_type", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gitlab_connections_user_id", "gitlab_connections", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_gitlab_connections_user_id", table_name="gitlab_connections")
    op.drop_table("gitlab_connections")

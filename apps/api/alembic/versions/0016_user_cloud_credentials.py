"""Add user_cloud_credentials vault for account-level cloud keys."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_user_cloud_credentials"
down_revision = "0015_gitlab_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_cloud_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_cloud_credentials_user_id",
        "user_cloud_credentials",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_user_cloud_credentials_user_id", table_name="user_cloud_credentials")
    op.drop_table("user_cloud_credentials")

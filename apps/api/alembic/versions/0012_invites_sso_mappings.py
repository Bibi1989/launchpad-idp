"""Org invites and SSO group→role mappings.

Revision ID: 0012_invites_sso_mappings
Revises: 0011_org_role_lowercase
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_invites_sso_mappings"
down_revision: str | None = "0011_org_role_lowercase"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_invites",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_org_invites_org_id", "org_invites", ["org_id"])
    op.create_index("ix_org_invites_email", "org_invites", ["email"])
    op.create_index("ix_org_invites_token_hash", "org_invites", ["token_hash"], unique=True)

    op.create_table(
        "org_sso_role_mappings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("group_name", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_org_sso_role_mappings_org_group",
        "org_sso_role_mappings",
        ["org_id", "group_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_org_sso_role_mappings_org_group", table_name="org_sso_role_mappings")
    op.drop_table("org_sso_role_mappings")
    op.drop_index("ix_org_invites_token_hash", table_name="org_invites")
    op.drop_index("ix_org_invites_email", table_name="org_invites")
    op.drop_index("ix_org_invites_org_id", table_name="org_invites")
    op.drop_table("org_invites")

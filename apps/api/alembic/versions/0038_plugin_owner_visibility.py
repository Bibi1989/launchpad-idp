"""owner + visibility for plugin_manifests (user vs org, public publish)

Revision ID: 0038_plugin_owner_visibility
Revises: 0037_plugin_manifests
Create Date: 2026-08-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038_plugin_owner_visibility"
down_revision: str | None = "0037_plugin_manifests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plugin_manifests", sa.Column("owner_user_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column(
        "plugin_manifests",
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default="private"),
    )
    op.create_foreign_key(
        "fk_plugin_manifests_owner_user",
        "plugin_manifests",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("plugin_manifests", "org_id", existing_type=sa.Uuid(as_uuid=True), nullable=True)
    op.drop_index("ix_plugin_manifests_org_plugin", table_name="plugin_manifests")
    op.create_index(
        "ix_plugin_manifests_org_plugin",
        "plugin_manifests",
        ["org_id", "plugin_id"],
        unique=True,
        postgresql_where=sa.text("org_id IS NOT NULL"),
        sqlite_where=sa.text("org_id IS NOT NULL"),
    )
    op.create_index(
        "ix_plugin_manifests_user_plugin",
        "plugin_manifests",
        ["owner_user_id", "plugin_id"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
        sqlite_where=sa.text("owner_user_id IS NOT NULL"),
    )
    op.create_index("ix_plugin_manifests_visibility", "plugin_manifests", ["visibility"])


def downgrade() -> None:
    op.drop_index("ix_plugin_manifests_visibility", table_name="plugin_manifests")
    op.drop_index("ix_plugin_manifests_user_plugin", table_name="plugin_manifests")
    op.drop_index("ix_plugin_manifests_org_plugin", table_name="plugin_manifests")
    op.create_index(
        "ix_plugin_manifests_org_plugin",
        "plugin_manifests",
        ["org_id", "plugin_id"],
        unique=True,
    )
    op.drop_constraint("fk_plugin_manifests_owner_user", "plugin_manifests", type_="foreignkey")
    op.alter_column("plugin_manifests", "org_id", existing_type=sa.Uuid(as_uuid=True), nullable=False)
    op.drop_column("plugin_manifests", "visibility")
    op.drop_column("plugin_manifests", "owner_user_id")

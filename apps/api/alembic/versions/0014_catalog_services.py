"""Add catalog_services for golden path service catalog.

Revision ID: 0014_catalog_services
Revises: 0013_release_destroyed_env_names
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_catalog_services"
down_revision: str | None = "0013_release_destroyed_env_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_services",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("provisioning_workspaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("service_owner", sa.String(length=128), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False, server_default="tier-2"),
        sa.Column("slo_target", sa.String(length=16), nullable=False, server_default="99.5"),
        sa.Column("runbook_url", sa.String(length=512), nullable=True),
        sa.Column("on_call", sa.String(length=128), nullable=True),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.String(length=32), nullable=False),
        sa.Column("repository_url", sa.String(length=512), nullable=True),
        sa.Column("compliance_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scorecard_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_catalog_services_org_id", "catalog_services", ["org_id"])
    op.create_index("ix_catalog_services_owner_id", "catalog_services", ["owner_id"])
    op.create_index("ix_catalog_services_name", "catalog_services", ["name"])


def downgrade() -> None:
    op.drop_index("ix_catalog_services_name", table_name="catalog_services")
    op.drop_index("ix_catalog_services_owner_id", table_name="catalog_services")
    op.drop_index("ix_catalog_services_org_id", table_name="catalog_services")
    op.drop_table("catalog_services")

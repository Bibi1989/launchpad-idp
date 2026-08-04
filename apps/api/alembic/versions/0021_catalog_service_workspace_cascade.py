"""Cascade catalog_services.workspace_id when a workspace is destroyed.

Revision ID: 0021_catalog_ws_cascade
Revises: 0020_environment_cost_metering
Create Date: 2026-08-04
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Keep <= 32 chars: alembic_version.version_num is VARCHAR(32).
revision: str = "0021_catalog_ws_cascade"
down_revision: str | None = "0020_environment_cost_metering"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop orphaned catalog rows whose workspace is already gone.
    op.execute(
        """
        DELETE FROM catalog_services
        WHERE workspace_id IS NOT NULL
          AND workspace_id NOT IN (SELECT id FROM provisioning_workspaces)
        """
    )

    bind = op.get_bind()
    # Application destroy_workspace also deletes catalog rows explicitly.
    # Harden the FK on PostgreSQL; SQLite/dev recreate via model metadata.
    if bind.dialect.name != "postgresql":
        return

    inspector = sa.inspect(bind)
    fks = inspector.get_foreign_keys("catalog_services")
    workspace_fk = next(
        (
            fk
            for fk in fks
            if fk.get("referred_table") == "provisioning_workspaces"
            and list(fk.get("constrained_columns") or []) == ["workspace_id"]
        ),
        None,
    )
    if workspace_fk is not None:
        options = workspace_fk.get("options") or {}
        if str(options.get("ondelete", "")).upper() == "CASCADE":
            return
        if workspace_fk.get("name"):
            op.drop_constraint(workspace_fk["name"], "catalog_services", type_="foreignkey")
    op.create_foreign_key(
        "catalog_services_workspace_id_fkey",
        "catalog_services",
        "provisioning_workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.drop_constraint(
        "catalog_services_workspace_id_fkey",
        "catalog_services",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "catalog_services_workspace_id_fkey",
        "catalog_services",
        "provisioning_workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )

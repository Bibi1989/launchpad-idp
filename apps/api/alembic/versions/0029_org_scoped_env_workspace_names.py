"""Scope environment and workspace names uniqueness to organization.

Revision ID: 0029_org_scoped_names
Revises: 0028_env_proj_ttl2h
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_org_scoped_names"
down_revision: str | None = "0028_env_proj_ttl2h"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "environments" in set(inspector.get_table_names()):
        # Drop global unique on name (constraint name varies by dialect/history).
        for uc in inspector.get_unique_constraints("environments"):
            cols = list(uc.get("column_names") or [])
            if cols == ["name"]:
                op.drop_constraint(uc["name"], "environments", type_="unique")
                break
        else:
            # SQLAlchemy sometimes only exposes unique via indexes.
            for ix in inspector.get_indexes("environments"):
                if ix.get("unique") and list(ix.get("column_names") or []) == ["name"]:
                    if ix.get("name") and ix["name"] != "ix_environments_name":
                        op.drop_index(ix["name"], table_name="environments")
                        break

        existing = {
            uc["name"] for uc in inspector.get_unique_constraints("environments") if uc.get("name")
        }
        if "uq_environments_org_id_name" not in existing:
            # Resolve duplicate (org_id, name) before creating the constraint.
            rows = bind.execute(
                sa.text(
                    "SELECT org_id, name, COUNT(*) AS c FROM environments "
                    "GROUP BY org_id, name HAVING COUNT(*) > 1"
                )
            ).fetchall()
            for org_id, name, _count in rows:
                dups = bind.execute(
                    sa.text(
                        "SELECT id FROM environments WHERE name = :name "
                        "AND ((org_id IS NULL AND :org_id IS NULL) OR org_id = :org_id) "
                        "ORDER BY created_at ASC"
                    ),
                    {"name": name, "org_id": org_id},
                ).fetchall()
                for idx, (env_id,) in enumerate(dups):
                    if idx == 0:
                        continue
                    suffix = str(env_id).replace("-", "")[:8]
                    bind.execute(
                        sa.text(
                            "UPDATE environments SET name = :new_name WHERE id = :id"
                        ),
                        {"new_name": f"{name}-{suffix}"[:128], "id": env_id},
                    )
            op.create_unique_constraint(
                "uq_environments_org_id_name",
                "environments",
                ["org_id", "name"],
            )

    if "provisioning_workspaces" in set(inspector.get_table_names()):
        inspector = sa.inspect(bind)
        existing = {
            uc["name"]
            for uc in inspector.get_unique_constraints("provisioning_workspaces")
            if uc.get("name")
        }
        if "uq_provisioning_workspaces_org_id_name" not in existing:
            rows = bind.execute(
                sa.text(
                    "SELECT org_id, name, COUNT(*) AS c FROM provisioning_workspaces "
                    "GROUP BY org_id, name HAVING COUNT(*) > 1"
                )
            ).fetchall()
            for org_id, name, _count in rows:
                dups = bind.execute(
                    sa.text(
                        "SELECT id FROM provisioning_workspaces WHERE name = :name "
                        "AND ((org_id IS NULL AND :org_id IS NULL) OR org_id = :org_id) "
                        "ORDER BY created_at ASC"
                    ),
                    {"name": name, "org_id": org_id},
                ).fetchall()
                for idx, (ws_id,) in enumerate(dups):
                    if idx == 0:
                        continue
                    suffix = str(ws_id).replace("-", "")[:8]
                    bind.execute(
                        sa.text(
                            "UPDATE provisioning_workspaces SET name = :new_name WHERE id = :id"
                        ),
                        {"new_name": f"{name}-{suffix}"[:128], "id": ws_id},
                    )
            op.create_unique_constraint(
                "uq_provisioning_workspaces_org_id_name",
                "provisioning_workspaces",
                ["org_id", "name"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "provisioning_workspaces" in set(inspector.get_table_names()):
        existing = {
            uc["name"]
            for uc in inspector.get_unique_constraints("provisioning_workspaces")
            if uc.get("name")
        }
        if "uq_provisioning_workspaces_org_id_name" in existing:
            op.drop_constraint(
                "uq_provisioning_workspaces_org_id_name",
                "provisioning_workspaces",
                type_="unique",
            )
    if "environments" in set(inspector.get_table_names()):
        existing = {
            uc["name"] for uc in inspector.get_unique_constraints("environments") if uc.get("name")
        }
        if "uq_environments_org_id_name" in existing:
            op.drop_constraint("uq_environments_org_id_name", "environments", type_="unique")
        # Restore global unique on name when possible.
        names = {
            uc["name"] for uc in inspector.get_unique_constraints("environments") if uc.get("name")
        }
        if "environments_name_key" not in names:
            try:
                op.create_unique_constraint("environments_name_key", "environments", ["name"])
            except Exception:
                pass

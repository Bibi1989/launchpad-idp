"""Project-scoped environment governance: add environments.project_id.

Also aligns project caps + TTL policies (2h max TTL) with persisted metadata.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_env_proj_ttl2h"
down_revision: str | None = "0027_agent_nodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "environments" not in tables:
        return

    if "project_id" not in {c["name"] for c in inspector.get_columns("environments")}:  # pragma: no branch
        op.add_column(
            "environments",
            sa.Column(
                "project_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index("ix_environments_project_id", "environments", ["project_id"])

    # Backfill from provisioning_workspaces when available.
    op.execute(
        """
        UPDATE environments
        SET project_id = (
            SELECT pw.project_id
            FROM provisioning_workspaces pw
            WHERE pw.id = environments.workspace_id
        )
        WHERE project_id IS NULL
          AND workspace_id IS NOT NULL
        """
    )

    # Otherwise backfill from each org's default project.
    op.execute(
        """
        UPDATE environments
        SET project_id = (
            SELECT p.id
            FROM projects p
            WHERE p.org_id = environments.org_id
              AND p.slug = 'default'
            LIMIT 1
        )
        WHERE project_id IS NULL
          AND org_id IS NOT NULL
        """
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("environments")}
    if "project_id" in columns:
        op.drop_index("ix_environments_project_id", table_name="environments")
        op.drop_column("environments", "project_id")


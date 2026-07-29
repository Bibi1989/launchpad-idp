"""Release unique names held by already-destroyed environments.

Revision ID: 0013_release_destroyed_env_names
Revises: 0012_invites_sso_mappings
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_release_destroyed_env_names"
down_revision: str | None = "0012_invites_sso_mappings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, name, namespace_name FROM environments "
            "WHERE status = 'DESTROYED' AND name NOT LIKE '%--destroyed-%'"
        )
    ).fetchall()
    for row in rows:
        env_id = str(row[0])
        name = str(row[1])
        suffix = env_id.replace("-", "")[:12]
        base = name[: max(1, 128 - len(suffix) - 12)]
        new_name = f"{base}--destroyed-{suffix}"
        new_ns = f"destroyed-{env_id}"[:253]
        conn.execute(
            sa.text(
                "UPDATE environments SET name = :name, namespace_name = :ns "
                "WHERE id = :id"
            ),
            {"name": new_name, "ns": new_ns, "id": row[0]},
        )


def downgrade() -> None:
    # Irreversible: original names are not retained after release.
    pass

"""Persist attach/cloud teardown context on environments.

Revision ID: 0030_env_teardown_ctx
Revises: 0029_org_scoped_names
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_env_teardown_ctx"
down_revision: str | None = "0029_org_scoped_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "environments" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("environments")}
    if "teardown_context_json" not in cols:
        op.add_column(
            "environments",
            sa.Column("teardown_context_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "environments" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("environments")}
    if "teardown_context_json" in cols:
        op.drop_column("environments", "teardown_context_json")

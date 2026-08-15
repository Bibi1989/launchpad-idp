"""Add failure_summary to environments.

Revision ID: 0033_env_failure_summary
Revises: 0032_k8s_image_scan
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_env_failure_summary"
down_revision: str | None = "0032_k8s_image_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "environments" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("environments")}
    if "failure_summary" not in cols:
        op.add_column(
            "environments",
            sa.Column("failure_summary", sa.Text(), nullable=True),
        )
    if "seed_status" not in cols:
        op.add_column(
            "environments",
            sa.Column("seed_status", sa.String(length=32), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "environments" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("environments")}
    if "seed_status" in cols:
        op.drop_column("environments", "seed_status")
    if "failure_summary" in cols:
        op.drop_column("environments", "failure_summary")

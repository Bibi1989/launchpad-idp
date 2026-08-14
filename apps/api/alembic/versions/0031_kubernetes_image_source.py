"""Add kubernetes_image_source to environments.

Revision ID: 0031_k8s_image_source
Revises: 0030_env_teardown_ctx
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_k8s_image_source"
down_revision: str | None = "0030_env_teardown_ctx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "environments" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("environments")}
    if "kubernetes_image_source" not in cols:
        op.add_column(
            "environments",
            sa.Column("kubernetes_image_source", sa.String(length=32), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "environments" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("environments")}
    if "kubernetes_image_source" in cols:
        op.drop_column("environments", "kubernetes_image_source")

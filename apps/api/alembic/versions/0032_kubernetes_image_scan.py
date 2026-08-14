"""Add kubernetes_image_scan_json to environments.

Revision ID: 0032_k8s_image_scan
Revises: 0031_k8s_image_source
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_k8s_image_scan"
down_revision: str | None = "0031_k8s_image_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "environments" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("environments")}
    if "kubernetes_image_scan_json" not in cols:
        op.add_column(
            "environments",
            sa.Column("kubernetes_image_scan_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "environments" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("environments")}
    if "kubernetes_image_scan_json" in cols:
        op.drop_column("environments", "kubernetes_image_scan_json")

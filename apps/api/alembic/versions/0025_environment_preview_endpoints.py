"""Add environments.preview_endpoints_json for multi-service previews.

Revision ID: 0025_env_preview_endpoints
Revises: 0024_projects_billing
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_env_preview_endpoints"
down_revision: str | None = "0024_projects_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("environments")}
    if "preview_endpoints_json" not in columns:
        op.add_column(
            "environments",
            sa.Column("preview_endpoints_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("environments")}
    if "preview_endpoints_json" in columns:
        op.drop_column("environments", "preview_endpoints_json")

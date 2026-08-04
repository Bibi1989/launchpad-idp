"""Add usage-based cost accrual columns to environments.

Revision ID: 0020_environment_cost_metering
Revises: 0019_environment_status_expired
Create Date: 2026-08-04
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_environment_cost_metering"
down_revision: str | None = "0019_environment_status_expired"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "environments",
        sa.Column(
            "cost_accrued",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "environments",
        sa.Column("cost_sampled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "environments",
        sa.Column("cost_source", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("environments", "cost_source")
    op.drop_column("environments", "cost_sampled_at")
    op.drop_column("environments", "cost_accrued")

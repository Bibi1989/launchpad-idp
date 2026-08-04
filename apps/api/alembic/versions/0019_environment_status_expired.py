"""Add EXPIRED to environment_status enum values.

Revision ID: 0019_environment_status_expired
Revises: 5b6785654c2c
Create Date: 2026-08-04
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_environment_status_expired"
down_revision: str | None = "5b6785654c2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW = (
    "PROVISIONING",
    "RUNNING",
    "PAUSED",
    "EXPIRED",
    "TEARDOWN_PENDING",
    "DESTROYED",
    "FAILED",
)
_OLD = (
    "PROVISIONING",
    "RUNNING",
    "PAUSED",
    "TEARDOWN_PENDING",
    "DESTROYED",
    "FAILED",
)


def upgrade() -> None:
    op.alter_column(
        "environments",
        "status",
        existing_type=sa.Enum(*_OLD, name="environment_status", native_enum=False),
        type_=sa.Enum(*_NEW, name="environment_status", native_enum=False),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE environments SET status = 'PAUSED' WHERE status = 'EXPIRED'"
    )
    op.alter_column(
        "environments",
        "status",
        existing_type=sa.Enum(*_NEW, name="environment_status", native_enum=False),
        type_=sa.Enum(*_OLD, name="environment_status", native_enum=False),
        existing_nullable=False,
    )

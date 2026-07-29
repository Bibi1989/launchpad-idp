"""Normalize org_memberships.role to lowercase enum values.

Revision ID: 0011_org_role_lowercase
Revises: 0010_org_rbac
Create Date: 2026-07-28

SQLAlchemy Enum(OrgRole) previously persisted member names (OWNER) while the
0010 backfill and API use values (owner). Lowercase any uppercase rows so
reads succeed after values_callable is configured on the model.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_org_role_lowercase"
down_revision: str | None = "0010_org_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE org_memberships SET role = lower(role) "
            "WHERE role <> lower(role)"
        )
    )


def downgrade() -> None:
    # Irreversible data normalization — roles remain valid lowercase values.
    pass

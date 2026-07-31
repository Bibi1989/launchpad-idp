"""Add enable_postgres and enable_redis columns to environments."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_ephemeral_datastores"
down_revision = "0017_workspace_wizard_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "environments",
        sa.Column(
            "enable_postgres",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "environments",
        sa.Column(
            "enable_redis",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("environments", "enable_redis")
    op.drop_column("environments", "enable_postgres")

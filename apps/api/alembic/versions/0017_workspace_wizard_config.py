"""Persist wizard_config_json for workspace disk restore."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_workspace_wizard_config"
down_revision = "0016_user_cloud_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provisioning_workspaces",
        sa.Column("wizard_config_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("provisioning_workspaces", "wizard_config_json")

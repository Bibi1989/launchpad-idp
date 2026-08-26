"""add plugin_manifests (user/org declarative cloud plugins)

Revision ID: 0037_plugin_manifests
Revises: 0036_provider_credentials
Create Date: 2026-08-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0037_plugin_manifests'
down_revision: str | None = '0036_provider_credentials'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'plugin_manifests',
        sa.Column('id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('org_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('plugin_id', sa.String(length=128), nullable=False),
        sa.Column('manifest_json', sa.Text(), nullable=False),
        sa.Column('bundle_path', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_plugin_manifests_org_plugin', 'plugin_manifests', ['org_id', 'plugin_id'], unique=True
    )


def downgrade() -> None:
    op.drop_index('ix_plugin_manifests_org_plugin', table_name='plugin_manifests')
    op.drop_table('plugin_manifests')

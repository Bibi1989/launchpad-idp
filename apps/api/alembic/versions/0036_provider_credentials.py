"""add provider_credentials vault

Revision ID: 0036_provider_credentials
Revises: 8f2609e25c30
Create Date: 2026-08-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0036_provider_credentials'
down_revision: str | None = '8f2609e25c30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'provider_credentials',
        sa.Column('id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('encrypted_payload', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_provider_credentials_user_id', 'provider_credentials', ['user_id'], unique=True
    )


def downgrade() -> None:
    op.drop_index('ix_provider_credentials_user_id', table_name='provider_credentials')
    op.drop_table('provider_credentials')

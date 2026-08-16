"""Add lifecycle stages, promotion requests, and org promotion policy.

Revision ID: 0034_lifecycle_promotions
Revises: 0033_env_failure_summary
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_lifecycle_promotions"
down_revision: str | None = "0033_env_failure_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "organizations" in tables:
        org_cols = {c["name"] for c in inspector.get_columns("organizations")}
        if "promotion_staging_requires_approval" not in org_cols:
            op.add_column(
                "organizations",
                sa.Column(
                    "promotion_staging_requires_approval",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            )
        if "promotion_production_requires_approval" not in org_cols:
            op.add_column(
                "organizations",
                sa.Column(
                    "promotion_production_requires_approval",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("true"),
                ),
            )

    if "environments" in tables:
        env_cols = {c["name"] for c in inspector.get_columns("environments")}
        if "lifecycle_stage" not in env_cols:
            op.add_column(
                "environments",
                sa.Column(
                    "lifecycle_stage",
                    sa.String(length=32),
                    nullable=False,
                    server_default="preview",
                ),
            )
        if "promotion_lineage_id" not in env_cols:
            op.add_column(
                "environments",
                sa.Column("promotion_lineage_id", sa.Uuid(), nullable=True),
            )
        if "promoted_from_id" not in env_cols:
            op.add_column(
                "environments",
                sa.Column("promoted_from_id", sa.Uuid(), nullable=True),
            )
        # Make TTL optional (production / permanent staging).
        op.alter_column(
            "environments",
            "ttl_expires_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        indexes = {idx["name"] for idx in inspector.get_indexes("environments")}
        if "ix_environments_lifecycle_stage" not in indexes:
            op.create_index(
                "ix_environments_lifecycle_stage",
                "environments",
                ["lifecycle_stage"],
            )
        if "ix_environments_promotion_lineage_id" not in indexes:
            op.create_index(
                "ix_environments_promotion_lineage_id",
                "environments",
                ["promotion_lineage_id"],
            )

    if "promotion_requests" not in tables:
        op.create_table(
            "promotion_requests",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column(
                "source_environment_id",
                sa.Uuid(),
                sa.ForeignKey("environments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "target_environment_id",
                sa.Uuid(),
                sa.ForeignKey("environments.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("target_stage", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("reviewed_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("review_note", sa.Text(), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_promotion_requests_org_id", "promotion_requests", ["org_id"])
        op.create_index("ix_promotion_requests_status", "promotion_requests", ["status"])
        op.create_index(
            "ix_promotion_requests_source_environment_id",
            "promotion_requests",
            ["source_environment_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "promotion_requests" in tables:
        op.drop_table("promotion_requests")

    if "environments" in tables:
        indexes = {idx["name"] for idx in inspector.get_indexes("environments")}
        if "ix_environments_promotion_lineage_id" in indexes:
            op.drop_index("ix_environments_promotion_lineage_id", table_name="environments")
        if "ix_environments_lifecycle_stage" in indexes:
            op.drop_index("ix_environments_lifecycle_stage", table_name="environments")
        env_cols = {c["name"] for c in inspector.get_columns("environments")}
        if "promoted_from_id" in env_cols:
            op.drop_column("environments", "promoted_from_id")
        if "promotion_lineage_id" in env_cols:
            op.drop_column("environments", "promotion_lineage_id")
        if "lifecycle_stage" in env_cols:
            op.drop_column("environments", "lifecycle_stage")
        op.alter_column(
            "environments",
            "ttl_expires_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )

    if "organizations" in tables:
        org_cols = {c["name"] for c in inspector.get_columns("organizations")}
        if "promotion_production_requires_approval" in org_cols:
            op.drop_column("organizations", "promotion_production_requires_approval")
        if "promotion_staging_requires_approval" in org_cols:
            op.drop_column("organizations", "promotion_staging_requires_approval")

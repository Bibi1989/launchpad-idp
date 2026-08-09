"""Projects, org plan/billing columns, workspace.project_id backfill.

Revision ID: 0024_projects_billing
Revises: 0023_gitlab_refresh_token
Create Date: 2026-08-09
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_projects_billing"
down_revision: str | None = "0023_gitlab_refresh_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("plan", sa.String(length=16), nullable=False, server_default="free"),
    )
    op.add_column(
        "organizations",
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_projects_org_id", "projects", ["org_id"])
    op.create_index("ix_projects_org_slug", "projects", ["org_id", "slug"], unique=True)

    op.create_table(
        "project_memberships",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Uuid(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_project_memberships_project_user",
        "project_memberships",
        ["project_id", "user_id"],
        unique=True,
    )
    op.create_index("ix_project_memberships_user_id", "project_memberships", ["user_id"])

    op.create_table(
        "project_invites",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Uuid(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_project_invites_project_id", "project_invites", ["project_id"])
    op.create_index("ix_project_invites_email", "project_invites", ["email"])
    op.create_index("ix_project_invites_token_hash", "project_invites", ["token_hash"], unique=True)

    op.add_column(
        "provisioning_workspaces",
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_provisioning_workspaces_project_id",
        "provisioning_workspaces",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_provisioning_workspaces_project_id",
        "provisioning_workspaces",
        ["project_id"],
    )

    conn = op.get_bind()
    orgs = conn.execute(sa.text("SELECT id FROM organizations")).fetchall()
    for (org_id,) in orgs:
        project_id = uuid.uuid4()
        owner_row = conn.execute(
            sa.text(
                "SELECT user_id FROM org_memberships "
                "WHERE org_id = :org_id AND role IN ('owner', 'admin') "
                "ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END "
                "LIMIT 1"
            ),
            {"org_id": org_id},
        ).fetchone()
        created_by = owner_row[0] if owner_row else None
        conn.execute(
            sa.text(
                "INSERT INTO projects (id, org_id, name, slug, created_by_user_id) "
                "VALUES (:id, :org_id, 'Default', 'default', :created_by)"
            ),
            {"id": project_id, "org_id": org_id, "created_by": created_by},
        )
        members = conn.execute(
            sa.text(
                "SELECT user_id, role FROM org_memberships "
                "WHERE org_id = :org_id AND role IN ('owner', 'admin', 'member', 'viewer')"
            ),
            {"org_id": org_id},
        ).fetchall()
        for user_id, role in members:
            conn.execute(
                sa.text(
                    "INSERT INTO project_memberships (id, project_id, user_id, role) "
                    "VALUES (:id, :project_id, :user_id, :role)"
                ),
                {
                    "id": uuid.uuid4(),
                    "project_id": project_id,
                    "user_id": user_id,
                    "role": role,
                },
            )
        conn.execute(
            sa.text(
                "UPDATE provisioning_workspaces SET project_id = :project_id "
                "WHERE org_id = :org_id AND project_id IS NULL"
            ),
            {"project_id": project_id, "org_id": org_id},
        )


def downgrade() -> None:
    op.drop_index("ix_provisioning_workspaces_project_id", table_name="provisioning_workspaces")
    op.drop_constraint(
        "fk_provisioning_workspaces_project_id",
        "provisioning_workspaces",
        type_="foreignkey",
    )
    op.drop_column("provisioning_workspaces", "project_id")
    op.drop_table("project_invites")
    op.drop_table("project_memberships")
    op.drop_table("projects")
    op.drop_column("organizations", "plan_updated_at")
    op.drop_column("organizations", "stripe_subscription_id")
    op.drop_column("organizations", "stripe_customer_id")
    op.drop_column("organizations", "plan")

"""Organizations, memberships, and org_id on environments/workspaces.

Revision ID: 0010_org_rbac
Revises: 0009_deploy_mode
Create Date: 2026-07-28
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_org_rbac"
down_revision: str | None = "0009_deploy_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return (cleaned or "org")[:48]


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)

    op.create_table(
        "org_memberships",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_org_memberships_org_user",
        "org_memberships",
        ["org_id", "user_id"],
        unique=True,
    )
    op.create_index("ix_org_memberships_user_id", "org_memberships", ["user_id"])

    op.add_column("users", sa.Column("oidc_issuer", sa.String(length=512), nullable=True))
    op.add_column("users", sa.Column("oidc_sub", sa.String(length=255), nullable=True))
    op.create_index(
        "ix_users_oidc_issuer_sub",
        "users",
        ["oidc_issuer", "oidc_sub"],
        unique=True,
    )
    op.alter_column("users", "password_hash", existing_type=sa.String(length=255), nullable=True)

    op.add_column("environments", sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_index("ix_environments_org_id", "environments", ["org_id"])
    op.create_foreign_key(
        "fk_environments_org_id",
        "environments",
        "organizations",
        ["org_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "provisioning_workspaces",
        sa.Column("org_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_provisioning_workspaces_org_id", "provisioning_workspaces", ["org_id"])
    op.create_foreign_key(
        "fk_provisioning_workspaces_org_id",
        "provisioning_workspaces",
        "organizations",
        ["org_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    conn = op.get_bind()
    users = conn.execute(sa.text("SELECT id, email, display_name FROM users")).mappings().all()
    used_slugs: set[str] = set()
    for user in users:
        local = str(user["email"]).split("@", 1)[0]
        base = _slugify(local)
        slug = base
        suffix = 1
        while slug in used_slugs:
            slug = f"{base}-{suffix}"[:64]
            suffix += 1
        used_slugs.add(slug)
        org_id = str(uuid.uuid4())
        membership_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, :name)"
            ),
            {
                "id": org_id,
                "slug": slug,
                "name": f"{user['display_name']}'s org",
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO org_memberships (id, org_id, user_id, role) "
                "VALUES (:id, :org_id, :user_id, :role)"
            ),
            {
                "id": membership_id,
                "org_id": org_id,
                "user_id": str(user["id"]),
                "role": "owner",
            },
        )
        conn.execute(
            sa.text("UPDATE environments SET org_id = :org_id WHERE owner_id = :user_id"),
            {"org_id": org_id, "user_id": str(user["id"])},
        )
        conn.execute(
            sa.text(
                "UPDATE provisioning_workspaces SET org_id = :org_id WHERE owner_id = :user_id"
            ),
            {"org_id": org_id, "user_id": str(user["id"])},
        )


def downgrade() -> None:
    op.drop_constraint("fk_provisioning_workspaces_org_id", "provisioning_workspaces", type_="foreignkey")
    op.drop_index("ix_provisioning_workspaces_org_id", table_name="provisioning_workspaces")
    op.drop_column("provisioning_workspaces", "org_id")

    op.drop_constraint("fk_environments_org_id", "environments", type_="foreignkey")
    op.drop_index("ix_environments_org_id", table_name="environments")
    op.drop_column("environments", "org_id")

    op.drop_index("ix_users_oidc_issuer_sub", table_name="users")
    op.drop_column("users", "oidc_sub")
    op.drop_column("users", "oidc_issuer")
    op.alter_column("users", "password_hash", existing_type=sa.String(length=255), nullable=False)

    op.drop_index("ix_org_memberships_user_id", table_name="org_memberships")
    op.drop_index("ix_org_memberships_org_user", table_name="org_memberships")
    op.drop_table("org_memberships")
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_table("organizations")

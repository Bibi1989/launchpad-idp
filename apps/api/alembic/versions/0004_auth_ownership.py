"""Add users, ownership, workspace link, and git_repo_url.

Revision ID: 0004_auth_ownership
Revises: 0003_provisioning
Create Date: 2026-07-27
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_auth_ownership"
down_revision: str | None = "0003_provisioning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYSTEM_USER_ID = "00000000-0000-4000-8000-000000000001"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # Placeholder hash - system user is not used for login; real users register/login.
    op.execute(
        sa.text(
            "INSERT INTO users (id, email, password_hash, display_name) "
            "VALUES (:id, :email, :password_hash, :display_name)"
        ).bindparams(
            id=uuid.UUID(SYSTEM_USER_ID),
            email="system@launchpad.local",
            password_hash="$2b$12$placeholder.system.user.not.for.loginxxxxx",
            display_name="System",
        )
    )

    op.add_column(
        "environments",
        sa.Column("owner_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "environments",
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "environments",
        sa.Column("git_repo_url", sa.String(length=512), nullable=True),
    )

    op.execute(
        sa.text(
            "UPDATE environments SET owner_id = :owner_id, "
            "git_repo_url = COALESCE(git_repo_url, 'https://github.com/example/app.git')"
        ).bindparams(owner_id=uuid.UUID(SYSTEM_USER_ID))
    )

    op.alter_column("environments", "owner_id", nullable=False)
    op.alter_column("environments", "git_repo_url", nullable=False)

    op.create_foreign_key(
        "fk_environments_owner_id_users",
        "environments",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_environments_workspace_id_workspaces",
        "environments",
        "provisioning_workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_environments_owner_id", "environments", ["owner_id"])

    op.add_column(
        "provisioning_workspaces",
        sa.Column("owner_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        sa.text("UPDATE provisioning_workspaces SET owner_id = :owner_id").bindparams(
            owner_id=uuid.UUID(SYSTEM_USER_ID)
        )
    )
    op.alter_column("provisioning_workspaces", "owner_id", nullable=False)
    op.create_foreign_key(
        "fk_provisioning_workspaces_owner_id_users",
        "provisioning_workspaces",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_provisioning_workspaces_owner_id",
        "provisioning_workspaces",
        ["owner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_provisioning_workspaces_owner_id", table_name="provisioning_workspaces")
    op.drop_constraint(
        "fk_provisioning_workspaces_owner_id_users",
        "provisioning_workspaces",
        type_="foreignkey",
    )
    op.drop_column("provisioning_workspaces", "owner_id")

    op.drop_index("ix_environments_owner_id", table_name="environments")
    op.drop_constraint(
        "fk_environments_workspace_id_workspaces",
        "environments",
        type_="foreignkey",
    )
    op.drop_constraint("fk_environments_owner_id_users", "environments", type_="foreignkey")
    op.drop_column("environments", "git_repo_url")
    op.drop_column("environments", "workspace_id")
    op.drop_column("environments", "owner_id")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

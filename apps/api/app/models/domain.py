from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class EnvironmentStatus(str, enum.Enum):
    PROVISIONING = "PROVISIONING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    EXPIRED = "EXPIRED"
    TEARDOWN_PENDING = "TEARDOWN_PENDING"
    DESTROYED = "DESTROYED"
    FAILED = "FAILED"


class LogLevel(str, enum.Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class ExecutionStage(str, enum.Enum):
    INIT = "INIT"
    VALIDATE = "VALIDATE"
    PLAN = "PLAN"
    BUILD = "BUILD"
    APPLY = "APPLY"


class AuditAction(str, enum.Enum):
    PROVISION_INITIATED = "PROVISION_INITIATED"
    PROVISION_VALIDATED = "PROVISION_VALIDATED"
    PROVISION_PLANNED = "PROVISION_PLANNED"
    PROVISION_SUCCEEDED = "PROVISION_SUCCEEDED"
    PROVISION_FAILED = "PROVISION_FAILED"
    REBUILD_INITIATED = "REBUILD_INITIATED"
    REBUILD_SUCCEEDED = "REBUILD_SUCCEEDED"
    REBUILD_FAILED = "REBUILD_FAILED"
    REBUILD_ROLLED_BACK = "REBUILD_ROLLED_BACK"
    PAUSE_SUCCEEDED = "PAUSE_SUCCEEDED"
    RESUME_SUCCEEDED = "RESUME_SUCCEEDED"
    TEARDOWN_INITIATED = "TEARDOWN_INITIATED"
    TEARDOWN_SUCCEEDED = "TEARDOWN_SUCCEEDED"
    TEARDOWN_FAILED = "TEARDOWN_FAILED"
    DRIFT_DETECTED = "DRIFT_DETECTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


class AuditStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REJECTED = "REJECTED"


class OrgRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email", "email", unique=True),
        Index("ix_users_oidc_issuer_sub", "oidc_issuer", "oidc_sub", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    oidc_issuer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    oidc_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    environments: Mapped[list[Environment]] = relationship(back_populates="owner")
    workspaces: Mapped[list[ProvisioningWorkspace]] = relationship(back_populates="owner")
    memberships: Mapped[list[OrgMembership]] = relationship(back_populates="user")


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (Index("ix_organizations_slug", "slug", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    memberships: Mapped[list[OrgMembership]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    invites: Mapped[list[OrgInvite]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    sso_mappings: Mapped[list[OrgSsoRoleMapping]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class OrgMembership(Base):
    __tablename__ = "org_memberships"
    __table_args__ = (
        Index("ix_org_memberships_org_user", "org_id", "user_id", unique=True),
        Index("ix_org_memberships_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[OrgRole] = mapped_column(
        # Persist enum *values* ("owner") - migration seed + API use lowercase;
        # SQLAlchemy defaults to member *names* ("OWNER") which breaks reads.
        Enum(
            OrgRole,
            name="org_role",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=OrgRole.MEMBER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class OrgInvite(Base):
    __tablename__ = "org_invites"
    __table_args__ = (
        Index("ix_org_invites_org_id", "org_id"),
        Index("ix_org_invites_email", "email"),
        Index("ix_org_invites_token_hash", "token_hash", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[OrgRole] = mapped_column(
        Enum(
            OrgRole,
            name="org_invite_role",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=OrgRole.MEMBER,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="invites")
    invited_by: Mapped[User] = relationship()


class OrgSsoRoleMapping(Base):
    __tablename__ = "org_sso_role_mappings"
    __table_args__ = (
        Index("ix_org_sso_role_mappings_org_group", "org_id", "group_name", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    group_name: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[OrgRole] = mapped_column(
        Enum(
            OrgRole,
            name="org_sso_role",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=OrgRole.MEMBER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="sso_mappings")


class Environment(Base):
    __tablename__ = "environments"
    __table_args__ = (
        Index("ix_environments_status", "status"),
        Index("ix_environments_ttl_expires_at", "ttl_expires_at"),
        Index("ix_environments_name", "name"),
        Index("ix_environments_owner_id", "owner_id"),
        Index("ix_environments_org_id", "org_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("provisioning_workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    git_branch: Mapped[str] = mapped_column(String(256), nullable=False)
    git_repo_url: Mapped[str] = mapped_column(String(512), nullable=False)
    latest_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[EnvironmentStatus] = mapped_column(
        Enum(EnvironmentStatus, name="environment_status", native_enum=False),
        nullable=False,
        default=EnvironmentStatus.PROVISIONING,
    )
    namespace_name: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)
    preview_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workload_image: Mapped[str | None] = mapped_column(String(256), nullable=True)
    node_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_pr_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    deploy_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="preview")
    manifest_packaging: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enable_postgres: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enable_redis: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ttl_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cost_estimate_hourly: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    cost_accrued: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False,
        default=Decimal("0.0000"),
        server_default="0",
    )
    cost_sampled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cost_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    owner: Mapped[User] = relationship(back_populates="environments")
    organization: Mapped[Organization | None] = relationship()
    workspace: Mapped[ProvisioningWorkspace | None] = relationship(
        back_populates="environments",
        foreign_keys=[workspace_id],
    )
    logs: Mapped[list[DeploymentLog]] = relationship(
        back_populates="environment",
        cascade="all, delete-orphan",
        order_by="DeploymentLog.timestamp.asc()",
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="environment",
        cascade="all, delete-orphan",
        order_by="AuditLog.timestamp.asc()",
        passive_deletes=True,
    )


class DeploymentLog(Base):
    __tablename__ = "deployment_logs"
    __table_args__ = (
        Index("ix_deployment_logs_environment_id", "environment_id"),
        Index("ix_deployment_logs_timestamp", "timestamp"),
        Index(
            "ix_deployment_logs_environment_timestamp",
            "environment_id",
            "timestamp",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("environments.id", ondelete="CASCADE"),
        nullable=False,
    )
    log_level: Mapped[LogLevel] = mapped_column(
        Enum(LogLevel, name="log_level", native_enum=False),
        nullable=False,
        default=LogLevel.INFO,
    )
    stage: Mapped[ExecutionStage | None] = mapped_column(
        Enum(ExecutionStage, name="execution_stage", native_enum=False),
        nullable=True,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    environment: Mapped[Environment] = relationship(back_populates="logs")


class AuditLog(Base):
    """Append-only control-plane audit trail. Rows must never be updated or deleted."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_workspace_id", "workspace_id"),
        Index("ix_audit_logs_environment_id", "environment_id"),
        Index("ix_audit_logs_actor_id", "actor_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index(
            "ix_audit_logs_environment_timestamp",
            "environment_id",
            "timestamp",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("provisioning_workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, name="audit_action", native_enum=False),
        nullable=False,
    )
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[AuditStatus] = mapped_column(
        Enum(AuditStatus, name="audit_status", native_enum=False),
        nullable=False,
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    environment: Mapped[Environment | None] = relationship(back_populates="audit_logs")
    workspace: Mapped[ProvisioningWorkspace | None] = relationship(
        back_populates="audit_logs",
    )


class ProvisioningWorkspace(Base):
    __tablename__ = "provisioning_workspaces"
    __table_args__ = (
        Index("ix_provisioning_workspaces_status", "status"),
        Index("ix_provisioning_workspaces_owner_id", "owner_id"),
        Index("ix_provisioning_workspaces_org_id", "org_id"),
        Index("ix_provisioning_workspaces_starred_at", "starred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    root_dir: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    encrypted_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Non-secret wizard snapshot so disk wipe can restore generated files.
    wizard_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When set, workspace appears under Catalog → Starred workspaces.
    starred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="workspaces")
    organization: Mapped[Organization | None] = relationship()
    environments: Mapped[list[Environment]] = relationship(
        back_populates="workspace",
        foreign_keys="Environment.workspace_id",
    )
    terminal_sessions: Mapped[list[TerminalSessionRecord]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="workspace",
        passive_deletes=True,
    )


class TerminalSessionRecord(Base):
    __tablename__ = "terminal_sessions"
    __table_args__ = (Index("ix_terminal_sessions_workspace_id", "workspace_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("provisioning_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    workspace: Mapped[ProvisioningWorkspace] = relationship(back_populates="terminal_sessions")


class CatalogService(Base):
    """Org-approved golden-path service catalog entry."""

    __tablename__ = "catalog_services"
    __table_args__ = (
        Index("ix_catalog_services_org_id", "org_id"),
        Index("ix_catalog_services_owner_id", "owner_id"),
        Index("ix_catalog_services_name", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("provisioning_workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    service_owner: Mapped[str] = mapped_column(String(128), nullable=False)
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="tier-2")
    slo_target: Mapped[str] = mapped_column(String(16), nullable=False, default="99.5")
    runbook_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    on_call: Mapped[str | None] = mapped_column(String(128), nullable=True)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(32), nullable=False)
    repository_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    compliance_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scorecard_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class GitlabConnection(Base):
    """Per-user GitLab OAuth or PAT connection for project create/push."""

    __tablename__ = "gitlab_connections"
    __table_args__ = (Index("ix_gitlab_connections_user_id", "user_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    base_url: Mapped[str] = mapped_column(String(256), nullable=False, default="https://gitlab.com")
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    token_type: Mapped[str] = mapped_column(String(32), nullable=False, default="pat")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserCloudCredentialStore(Base):
    """Per-user encrypted cloud credentials vault (GCP/AWS/Azure/Cloudflare)."""

    __tablename__ = "user_cloud_credentials"
    __table_args__ = (Index("ix_user_cloud_credentials_user_id", "user_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

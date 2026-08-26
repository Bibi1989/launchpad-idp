import {
  boolean,
  integer,
  numeric,
  pgTable,
  text,
  timestamp,
  uuid,
  varchar,
} from 'drizzle-orm/pg-core';

/**
 * Drizzle schema for tables this backend reads/writes. Mirrors SQLAlchemy models in
 * apps/api/app/models/domain.py and the same physical tables Alembic already created.
 * Drizzle does not own migrations here.
 */

const timestamptz = (name: string) => timestamp(name, { withTimezone: true });

export const users = pgTable('users', {
  id: uuid('id').primaryKey(),
  email: varchar('email', { length: 320 }).notNull(),
  passwordHash: varchar('password_hash', { length: 255 }),
  displayName: varchar('display_name', { length: 128 }).notNull(),
  oidcIssuer: varchar('oidc_issuer', { length: 512 }),
  oidcSub: varchar('oidc_sub', { length: 255 }),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
  updatedAt: timestamptz('updated_at').notNull().defaultNow(),
});
export type UserRow = typeof users.$inferSelect;

export const organizations = pgTable('organizations', {
  id: uuid('id').primaryKey(),
  slug: varchar('slug', { length: 64 }).notNull(),
  name: varchar('name', { length: 128 }).notNull(),
  plan: varchar('plan', { length: 16 }).notNull().default('free'),
  stripeCustomerId: varchar('stripe_customer_id', { length: 255 }),
  stripeSubscriptionId: varchar('stripe_subscription_id', { length: 255 }),
  planUpdatedAt: timestamptz('plan_updated_at'),
  promotionStagingRequiresApproval: boolean('promotion_staging_requires_approval')
    .notNull()
    .default(false),
  promotionProductionRequiresApproval: boolean('promotion_production_requires_approval')
    .notNull()
    .default(true),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
});
export type OrganizationRow = typeof organizations.$inferSelect;

export const orgMembers = pgTable('org_memberships', {
  id: uuid('id').primaryKey(),
  orgId: uuid('org_id').notNull(),
  userId: uuid('user_id').notNull(),
  role: varchar('role', { length: 32 }).notNull().default('member'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
});
export type OrgMemberRow = typeof orgMembers.$inferSelect;

export const orgInvites = pgTable('org_invites', {
  id: uuid('id').primaryKey(),
  orgId: uuid('org_id').notNull(),
  email: varchar('email', { length: 320 }).notNull(),
  role: varchar('role', { length: 32 }).notNull().default('member'),
  tokenHash: varchar('token_hash', { length: 64 }).notNull(),
  invitedByUserId: uuid('invited_by_user_id').notNull(),
  expiresAt: timestamptz('expires_at').notNull(),
  acceptedAt: timestamptz('accepted_at'),
  revokedAt: timestamptz('revoked_at'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
});
export type OrgInviteRow = typeof orgInvites.$inferSelect;

export const orgSsoRoleMappings = pgTable('org_sso_role_mappings', {
  id: uuid('id').primaryKey(),
  orgId: uuid('org_id').notNull(),
  groupName: varchar('group_name', { length: 256 }).notNull(),
  role: varchar('role', { length: 32 }).notNull().default('member'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
});
export type OrgSsoRoleMappingRow = typeof orgSsoRoleMappings.$inferSelect;

export const orgIntegrations = pgTable('org_integrations', {
  id: uuid('id').primaryKey(),
  orgId: uuid('org_id').notNull(),
  encryptedSlackWebhookUrl: text('encrypted_slack_webhook_url'),
  slackNotifyReady: boolean('slack_notify_ready').notNull().default(true),
  slackNotifyFailed: boolean('slack_notify_failed').notNull().default(true),
  slackNotifyTtlWarning: boolean('slack_notify_ttl_warning').notNull().default(true),
  slackNotifyCostCap: boolean('slack_notify_cost_cap').notNull().default(true),
  slackProjectIdsJson: text('slack_project_ids_json'),
  jiraSiteUrl: varchar('jira_site_url', { length: 512 }),
  jiraEmail: varchar('jira_email', { length: 256 }),
  encryptedJiraApiToken: text('encrypted_jira_api_token'),
  jiraProjectKey: varchar('jira_project_key', { length: 64 }),
  jiraIssueType: varchar('jira_issue_type', { length: 64 }).notNull().default('Bug'),
  jiraAutoCreateOnFailure: boolean('jira_auto_create_on_failure').notNull().default(false),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
  updatedAt: timestamptz('updated_at').notNull().defaultNow(),
});
export type OrgIntegrationRow = typeof orgIntegrations.$inferSelect;

export const projects = pgTable('projects', {
  id: uuid('id').primaryKey(),
  orgId: uuid('org_id').notNull(),
  name: varchar('name', { length: 128 }).notNull(),
  slug: varchar('slug', { length: 64 }).notNull(),
  createdByUserId: uuid('created_by_user_id'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
  updatedAt: timestamptz('updated_at').notNull().defaultNow(),
});
export type ProjectRow = typeof projects.$inferSelect;

export const projectMemberships = pgTable('project_memberships', {
  id: uuid('id').primaryKey(),
  projectId: uuid('project_id').notNull(),
  userId: uuid('user_id').notNull(),
  role: varchar('role', { length: 32 }).notNull().default('member'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
});
export type ProjectMembershipRow = typeof projectMemberships.$inferSelect;

export const projectInvites = pgTable('project_invites', {
  id: uuid('id').primaryKey(),
  projectId: uuid('project_id').notNull(),
  email: varchar('email', { length: 320 }).notNull(),
  role: varchar('role', { length: 32 }).notNull().default('member'),
  tokenHash: varchar('token_hash', { length: 64 }).notNull(),
  invitedByUserId: uuid('invited_by_user_id').notNull(),
  expiresAt: timestamptz('expires_at').notNull(),
  acceptedAt: timestamptz('accepted_at'),
  revokedAt: timestamptz('revoked_at'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
});
export type ProjectInviteRow = typeof projectInvites.$inferSelect;

export const provisioningWorkspaces = pgTable('provisioning_workspaces', {
  id: uuid('id').primaryKey(),
  ownerId: uuid('owner_id').notNull(),
  orgId: uuid('org_id'),
  projectId: uuid('project_id'),
  name: varchar('name', { length: 128 }).notNull(),
  engine: varchar('engine', { length: 32 }).notNull(),
  provider: varchar('provider', { length: 32 }).notNull(),
  rootDir: varchar('root_dir', { length: 512 }).notNull(),
  status: varchar('status', { length: 32 }).notNull().default('ready'),
  encryptedCredentials: text('encrypted_credentials'),
  wizardConfigJson: text('wizard_config_json'),
  /** Alembic 3e76b5c8ce7c - not yet mirrored on SQLAlchemy ProvisioningWorkspace. */
  infrastructureConfigJson: text('infrastructure_config_json'),
  starredAt: timestamptz('starred_at'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
});
export type ProvisioningWorkspaceRow = typeof provisioningWorkspaces.$inferSelect;

export const environments = pgTable('environments', {
  id: uuid('id').primaryKey(),
  ownerId: uuid('owner_id').notNull(),
  orgId: uuid('org_id'),
  projectId: uuid('project_id'),
  workspaceId: uuid('workspace_id'),
  name: varchar('name', { length: 128 }).notNull(),
  gitBranch: varchar('git_branch', { length: 256 }).notNull(),
  gitRepoUrl: varchar('git_repo_url', { length: 512 }).notNull(),
  latestCommitSha: varchar('latest_commit_sha', { length: 64 }),
  status: varchar('status', { length: 32 }).notNull().default('PROVISIONING'),
  namespaceName: varchar('namespace_name', { length: 253 }).notNull(),
  previewUrl: varchar('preview_url', { length: 512 }),
  previewEndpointsJson: text('preview_endpoints_json'),
  templateId: varchar('template_id', { length: 64 }),
  provider: varchar('provider', { length: 32 }),
  workloadImage: varchar('workload_image', { length: 256 }),
  nodePort: integer('node_port'),
  githubPrNumber: integer('github_pr_number'),
  githubPrUrl: varchar('github_pr_url', { length: 512 }),
  jiraIssueKey: varchar('jira_issue_key', { length: 64 }),
  jiraIssueUrl: varchar('jira_issue_url', { length: 512 }),
  notificationFlagsJson: text('notification_flags_json'),
  deployMode: varchar('deploy_mode', { length: 16 }).notNull().default('preview'),
  manifestPackaging: varchar('manifest_packaging', { length: 32 }),
  workloadImageSource: varchar('workload_image_source', { length: 32 }),
  workloadImageScanJson: text('workload_image_scan_json'),
  enablePostgres: boolean('enable_postgres').notNull().default(false),
  enableRedis: boolean('enable_redis').notNull().default(false),
  lifecycleStage: varchar('lifecycle_stage', { length: 32 }).notNull().default('preview'),
  promotionLineageId: uuid('promotion_lineage_id'),
  promotedFromId: uuid('promoted_from_id'),
  ttlExpiresAt: timestamptz('ttl_expires_at'),
  ttlDurationSeconds: integer('ttl_duration_seconds'),
  costEstimateHourly: numeric('cost_estimate_hourly', { precision: 12, scale: 4 }).notNull(),
  costAccrued: numeric('cost_accrued', { precision: 12, scale: 4 }).notNull().default('0'),
  costSampledAt: timestamptz('cost_sampled_at'),
  costSource: varchar('cost_source', { length: 32 }),
  errorMessage: text('error_message'),
  failureSummary: text('failure_summary'),
  seedStatus: varchar('seed_status', { length: 32 }),
  teardownContextJson: text('teardown_context_json'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
  updatedAt: timestamptz('updated_at').notNull().defaultNow(),
});
export type EnvironmentRow = typeof environments.$inferSelect;

export const promotionRequests = pgTable('promotion_requests', {
  id: uuid('id').primaryKey(),
  orgId: uuid('org_id').notNull(),
  sourceEnvironmentId: uuid('source_environment_id').notNull(),
  targetEnvironmentId: uuid('target_environment_id'),
  targetStage: varchar('target_stage', { length: 32 }).notNull(),
  status: varchar('status', { length: 32 }).notNull().default('pending'),
  requestedBy: uuid('requested_by').notNull(),
  reviewedBy: uuid('reviewed_by'),
  reviewNote: text('review_note'),
  payloadJson: text('payload_json'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
  reviewedAt: timestamptz('reviewed_at'),
  completedAt: timestamptz('completed_at'),
});
export type PromotionRequestRow = typeof promotionRequests.$inferSelect;

export const deploymentLogs = pgTable('deployment_logs', {
  id: uuid('id').primaryKey(),
  environmentId: uuid('environment_id').notNull(),
  logLevel: varchar('log_level', { length: 16 }).notNull().default('INFO'),
  stage: varchar('stage', { length: 32 }),
  message: text('message').notNull(),
  timestamp: timestamptz('timestamp').notNull().defaultNow(),
});
export type DeploymentLogRow = typeof deploymentLogs.$inferSelect;

/** Nest JS keys map onto SQLAlchemy AuditLog columns (detail / timestamp). */
export const auditLogs = pgTable('audit_logs', {
  id: uuid('id').primaryKey(),
  workspaceId: uuid('workspace_id'),
  environmentId: uuid('environment_id'),
  actorId: varchar('actor_id', { length: 128 }).notNull(),
  action: varchar('action', { length: 64 }).notNull(),
  commitSha: varchar('commit_sha', { length: 64 }),
  status: varchar('status', { length: 32 }).notNull(),
  detailsJson: text('detail'),
  createdAt: timestamptz('timestamp').notNull().defaultNow(),
});
export type AuditLogRow = typeof auditLogs.$inferSelect;

export const catalogServices = pgTable('catalog_services', {
  id: uuid('id').primaryKey(),
  ownerId: uuid('owner_id').notNull(),
  orgId: uuid('org_id'),
  workspaceId: uuid('workspace_id'),
  name: varchar('name', { length: 64 }).notNull(),
  description: varchar('description', { length: 512 }).notNull().default(''),
  serviceOwner: varchar('service_owner', { length: 128 }).notNull(),
  tier: varchar('tier', { length: 32 }).notNull().default('tier-2'),
  sloTarget: varchar('slo_target', { length: 16 }).notNull().default('99.5'),
  runbookUrl: varchar('runbook_url', { length: 512 }),
  onCall: varchar('on_call', { length: 128 }),
  templateId: varchar('template_id', { length: 64 }).notNull(),
  templateVersion: varchar('template_version', { length: 32 }).notNull(),
  repositoryUrl: varchar('repository_url', { length: 512 }),
  complianceScore: integer('compliance_score').notNull().default(0),
  scorecardJson: text('scorecard_json').notNull().default('{}'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
  updatedAt: timestamptz('updated_at').notNull().defaultNow(),
});
export type CatalogServiceRow = typeof catalogServices.$inferSelect;

export const gitlabConnections = pgTable('gitlab_connections', {
  id: uuid('id').primaryKey(),
  userId: uuid('user_id').notNull(),
  baseUrl: varchar('base_url', { length: 256 }).notNull().default('https://gitlab.com'),
  username: varchar('username', { length: 128 }).notNull(),
  encryptedToken: text('encrypted_token').notNull(),
  tokenType: varchar('token_type', { length: 32 }).notNull().default('pat'),
  encryptedRefreshToken: text('encrypted_refresh_token'),
  tokenExpiresAt: timestamptz('token_expires_at'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
  updatedAt: timestamptz('updated_at').notNull().defaultNow(),
});
export type GitlabConnectionRow = typeof gitlabConnections.$inferSelect;

export const userCloudCredentials = pgTable('user_cloud_credentials', {
  id: uuid('id').primaryKey(),
  userId: uuid('user_id').notNull(),
  encryptedPayload: text('encrypted_payload').notNull(),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
  updatedAt: timestamptz('updated_at').notNull().defaultNow(),
});
export type UserCloudCredentialsRow = typeof userCloudCredentials.$inferSelect;

export const providerCredentials = pgTable('provider_credentials', {
  id: uuid('id').primaryKey().defaultRandom(),
  userId: uuid('user_id').notNull(),
  encryptedPayload: text('encrypted_payload').notNull(),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
  updatedAt: timestamptz('updated_at').notNull().defaultNow(),
});
export type ProviderCredentialsRow = typeof providerCredentials.$inferSelect;

export const pluginManifests = pgTable('plugin_manifests', {
  id: uuid('id').primaryKey().defaultRandom(),
  orgId: uuid('org_id'),
  ownerUserId: uuid('owner_user_id'),
  pluginId: varchar('plugin_id', { length: 128 }).notNull(),
  manifestJson: text('manifest_json').notNull(),
  bundlePath: text('bundle_path'),
  visibility: varchar('visibility', { length: 16 }).notNull().default('private'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
  updatedAt: timestamptz('updated_at').notNull().defaultNow(),
});
export type PluginManifestRow = typeof pluginManifests.$inferSelect;

export const agentNodes = pgTable('agent_nodes', {
  id: uuid('id').primaryKey(),
  ownerId: uuid('owner_id').notNull(),
  orgId: uuid('org_id'),
  name: varchar('name', { length: 128 }).notNull(),
  status: varchar('status', { length: 32 }).notNull().default('PENDING'),
  enrollmentTokenHash: varchar('enrollment_token_hash', { length: 64 }),
  enrollmentExpiresAt: timestamptz('enrollment_expires_at'),
  encryptedAgentSecret: text('encrypted_agent_secret'),
  labelsJson: text('labels_json'),
  hostname: varchar('hostname', { length: 253 }),
  platform: varchar('platform', { length: 64 }),
  agentVersion: varchar('agent_version', { length: 32 }),
  cpuCores: integer('cpu_cores'),
  memTotalMb: integer('mem_total_mb'),
  lastHeartbeatAt: timestamptz('last_heartbeat_at'),
  cpuPercent: numeric('cpu_percent', { precision: 5, scale: 2 }),
  memPercent: numeric('mem_percent', { precision: 5, scale: 2 }),
  diskPercent: numeric('disk_percent', { precision: 5, scale: 2 }),
  dockerStatus: varchar('docker_status', { length: 32 }),
  containersJson: text('containers_json'),
  createdAt: timestamptz('created_at').notNull().defaultNow(),
  updatedAt: timestamptz('updated_at').notNull().defaultNow(),
});
export type AgentNodeRow = typeof agentNodes.$inferSelect;

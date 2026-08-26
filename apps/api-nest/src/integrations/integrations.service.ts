import {
  BadRequestException,
  ForbiddenException,
  Inject,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { and, eq } from 'drizzle-orm';
import { randomUUID } from 'crypto';

import { CurrentUser } from '../common/auth/current-user.interface';
import { SecretCipherService } from '../common/crypto/secret-cipher.service';
import { Database, DRIZZLE } from '../database/database.module';
import {
  gitlabConnections,
  orgIntegrations,
  OrgIntegrationRow,
  orgMembers,
} from '../database/schema';

/**
 * Response shapes and request bodies mirror the FastAPI schemas in
 * apps/api/app/schemas/integrations.py exactly (snake_case keys).
 */
export interface SlackIntegrationStatus {
  connected: boolean;
  notify_ready: boolean;
  notify_failed: boolean;
  notify_ttl_warning: boolean;
  notify_cost_cap: boolean;
  project_ids: string[];
  webhook_configured: boolean;
  updated_at: Date | null;
}

export interface SlackIntegrationUpdate {
  webhook_url?: string | null;
  notify_ready?: boolean | null;
  notify_failed?: boolean | null;
  notify_ttl_warning?: boolean | null;
  notify_cost_cap?: boolean | null;
  project_ids?: string[] | null;
  clear_webhook?: boolean;
}

export interface JiraIntegrationStatus {
  connected: boolean;
  site_url: string | null;
  email: string | null;
  project_key: string | null;
  issue_type: string;
  auto_create_on_failure: boolean;
  token_configured: boolean;
  updated_at: Date | null;
}

export interface JiraIntegrationUpdate {
  site_url?: string | null;
  email?: string | null;
  api_token?: string | null;
  project_key?: string | null;
  issue_type?: string | null;
  auto_create_on_failure?: boolean | null;
  clear?: boolean;
}

export interface IntegrationsSummary {
  github_app_installed: boolean;
  gitlab_oauth_connected: boolean;
  slack_configured: boolean;
  jira_configured: boolean;
  org_id: string;
}

// Mirrors app/services/orgs.py role ordering (viewer < member < admin < owner).
const ROLE_ORDER: Record<string, number> = {
  viewer: 0,
  member: 1,
  admin: 2,
  owner: 3,
};

function roleAtLeast(role: string, minimum: string): boolean {
  return (ROLE_ORDER[role] ?? -1) >= (ROLE_ORDER[minimum] ?? 0);
}

@Injectable()
export class IntegrationsService {
  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly cipher: SecretCipherService,
  ) {}

  /**
   * Non-breaking summary for the current user's org context. Keeps the original
   * keys (github_app_installed, gitlab_oauth_connected, org_id) and adds the
   * additive slack_configured / jira_configured booleans.
   */
  async getSummary(user: CurrentUser): Promise<IntegrationsSummary> {
    const orgId = await this.resolveOrgId(user);

    const [gitlabRow] = await this.db
      .select({ id: gitlabConnections.id })
      .from(gitlabConnections)
      .where(eq(gitlabConnections.userId, user.userId))
      .limit(1);

    const integrationRow = orgId ? await this.getRow(orgId) : null;

    return {
      github_app_installed: false,
      gitlab_oauth_connected: Boolean(gitlabRow),
      slack_configured: Boolean(integrationRow?.encryptedSlackWebhookUrl),
      jira_configured: this.jiraConnected(integrationRow),
      org_id: orgId ?? user.userId,
    };
  }

  async getSlack(user: CurrentUser, orgId: string): Promise<SlackIntegrationStatus> {
    await this.resolveContext(user, orgId);
    const row = await this.getRow(orgId);
    return this.slackStatus(row);
  }

  async upsertSlack(
    user: CurrentUser,
    orgId: string,
    payload: SlackIntegrationUpdate,
  ): Promise<SlackIntegrationStatus> {
    const ctx = await this.resolveContext(user, orgId);
    this.requireAdmin(ctx.role);

    const webhookUrl = this.validateWebhookUrl(payload.webhook_url);
    const row = await this.getOrCreate(orgId);

    const updates: Partial<OrgIntegrationRow> = {};
    if (payload.clear_webhook) {
      updates.encryptedSlackWebhookUrl = null;
    } else if (webhookUrl) {
      updates.encryptedSlackWebhookUrl = this.cipher.encrypt(webhookUrl);
    }
    if (payload.notify_ready != null) updates.slackNotifyReady = payload.notify_ready;
    if (payload.notify_failed != null) updates.slackNotifyFailed = payload.notify_failed;
    if (payload.notify_ttl_warning != null)
      updates.slackNotifyTtlWarning = payload.notify_ttl_warning;
    if (payload.notify_cost_cap != null) updates.slackNotifyCostCap = payload.notify_cost_cap;
    if (payload.project_ids != null)
      updates.slackProjectIdsJson = JSON.stringify(payload.project_ids.map((p) => String(p)));

    const saved = await this.applyUpdate(row, updates);
    return this.slackStatus(saved);
  }

  async disconnectSlack(user: CurrentUser, orgId: string): Promise<SlackIntegrationStatus> {
    const ctx = await this.resolveContext(user, orgId);
    this.requireAdmin(ctx.role);
    let row = await this.getRow(orgId);
    if (row) {
      row = await this.applyUpdate(row, { encryptedSlackWebhookUrl: null });
    }
    return this.slackStatus(row);
  }

  async getJira(user: CurrentUser, orgId: string): Promise<JiraIntegrationStatus> {
    await this.resolveContext(user, orgId);
    const row = await this.getRow(orgId);
    return this.jiraStatus(row);
  }

  async upsertJira(
    user: CurrentUser,
    orgId: string,
    payload: JiraIntegrationUpdate,
  ): Promise<JiraIntegrationStatus> {
    const ctx = await this.resolveContext(user, orgId);
    this.requireAdmin(ctx.role);
    const row = await this.getOrCreate(orgId);

    if (payload.clear) {
      const saved = await this.applyUpdate(row, {
        jiraSiteUrl: null,
        jiraEmail: null,
        encryptedJiraApiToken: null,
        jiraProjectKey: null,
        jiraIssueType: 'Bug',
        jiraAutoCreateOnFailure: false,
      });
      return this.jiraStatus(saved);
    }

    const siteUrl = this.validateSiteUrl(payload.site_url);
    const email = this.validateEmail(payload.email);
    const projectKey = this.validateProjectKey(payload.project_key);
    const apiToken = payload.api_token != null ? payload.api_token.trim() || null : null;

    const updates: Partial<OrgIntegrationRow> = {};
    if (siteUrl != null) updates.jiraSiteUrl = siteUrl;
    if (email != null) updates.jiraEmail = email;
    if (apiToken != null) updates.encryptedJiraApiToken = this.cipher.encrypt(apiToken);
    if (projectKey != null) updates.jiraProjectKey = projectKey;
    if (payload.issue_type != null && payload.issue_type.trim())
      updates.jiraIssueType = payload.issue_type.trim();
    if (payload.auto_create_on_failure != null)
      updates.jiraAutoCreateOnFailure = payload.auto_create_on_failure;

    // Effective row after the merge, used to enforce completeness.
    const merged: OrgIntegrationRow = { ...row, ...updates };
    if (
      !(
        merged.jiraSiteUrl &&
        merged.jiraEmail &&
        merged.encryptedJiraApiToken &&
        merged.jiraProjectKey
      )
    ) {
      throw new BadRequestException({
        code: 'jira_incomplete',
        message: 'Jira requires site URL, email, API token, and project key',
      });
    }

    const saved = await this.applyUpdate(row, updates);
    return this.jiraStatus(saved);
  }

  async disconnectJira(user: CurrentUser, orgId: string): Promise<JiraIntegrationStatus> {
    return this.upsertJira(user, orgId, { clear: true });
  }

  // ----- helpers -----

  private async resolveOrgId(user: CurrentUser): Promise<string | null> {
    if (user.orgId) return user.orgId;
    const [membership] = await this.db
      .select({ orgId: orgMembers.orgId })
      .from(orgMembers)
      .where(eq(orgMembers.userId, user.userId))
      .limit(1);
    return membership?.orgId ?? null;
  }

  private async resolveContext(
    user: CurrentUser,
    orgId: string,
  ): Promise<{ role: string }> {
    const [membership] = await this.db
      .select({ role: orgMembers.role })
      .from(orgMembers)
      .where(and(eq(orgMembers.orgId, orgId), eq(orgMembers.userId, user.userId)))
      .limit(1);
    if (!membership) {
      throw new NotFoundException({
        code: 'org_not_found',
        message: 'Organization not found',
      });
    }
    return { role: membership.role };
  }

  private requireAdmin(role: string): void {
    if (!roleAtLeast(role, 'admin')) {
      throw new ForbiddenException({
        code: 'forbidden',
        message: 'Org admin required to manage integrations',
      });
    }
  }

  private async getRow(orgId: string): Promise<OrgIntegrationRow | null> {
    const [row] = await this.db
      .select()
      .from(orgIntegrations)
      .where(eq(orgIntegrations.orgId, orgId))
      .limit(1);
    return row ?? null;
  }

  private async getOrCreate(orgId: string): Promise<OrgIntegrationRow> {
    const existing = await this.getRow(orgId);
    if (existing) return existing;
    // Explicit id: FastAPI-owned tables generate ids in Python, so the DB column has
    // no server-side default. Relying on Drizzle's `default` inserts NULL -> PK
    // violation -> 500. Always supply the id ourselves.
    const [created] = await this.db
      .insert(orgIntegrations)
      .values({ id: randomUUID(), orgId })
      .returning();
    return created;
  }

  private async applyUpdate(
    row: OrgIntegrationRow,
    updates: Partial<OrgIntegrationRow>,
  ): Promise<OrgIntegrationRow> {
    const [saved] = await this.db
      .update(orgIntegrations)
      .set({ ...updates, updatedAt: new Date() })
      .where(eq(orgIntegrations.id, row.id))
      .returning();
    return saved;
  }

  private jiraConnected(row: OrgIntegrationRow | null): boolean {
    return Boolean(
      row &&
        row.jiraSiteUrl &&
        row.jiraEmail &&
        row.encryptedJiraApiToken &&
        row.jiraProjectKey,
    );
  }

  private slackStatus(row: OrgIntegrationRow | null): SlackIntegrationStatus {
    if (!row) {
      return {
        connected: false,
        notify_ready: true,
        notify_failed: true,
        notify_ttl_warning: true,
        notify_cost_cap: true,
        project_ids: [],
        webhook_configured: false,
        updated_at: null,
      };
    }
    let projectIds: string[] = [];
    const raw = (row.slackProjectIdsJson ?? '').trim();
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) projectIds = parsed.map((item) => String(item));
      } catch {
        projectIds = [];
      }
    }
    const connected = Boolean(row.encryptedSlackWebhookUrl);
    return {
      connected,
      notify_ready: row.slackNotifyReady,
      notify_failed: row.slackNotifyFailed,
      notify_ttl_warning: row.slackNotifyTtlWarning,
      notify_cost_cap: row.slackNotifyCostCap,
      project_ids: projectIds,
      webhook_configured: connected,
      updated_at: row.updatedAt ?? null,
    };
  }

  private jiraStatus(row: OrgIntegrationRow | null): JiraIntegrationStatus {
    if (!row) {
      return {
        connected: false,
        site_url: null,
        email: null,
        project_key: null,
        issue_type: 'Bug',
        auto_create_on_failure: false,
        token_configured: false,
        updated_at: null,
      };
    }
    return {
      connected: this.jiraConnected(row),
      site_url: row.jiraSiteUrl ?? null,
      email: row.jiraEmail ?? null,
      project_key: row.jiraProjectKey ?? null,
      issue_type: row.jiraIssueType || 'Bug',
      auto_create_on_failure: row.jiraAutoCreateOnFailure,
      token_configured: Boolean(row.encryptedJiraApiToken),
      updated_at: row.updatedAt ?? null,
    };
  }

  // ----- validators (mirror the FastAPI Pydantic field validators) -----

  private validateWebhookUrl(value?: string | null): string | null {
    if (value == null) return null;
    const trimmed = value.trim();
    if (!trimmed) return null;
    if (!trimmed.startsWith('https://hooks.slack.com/')) {
      throw new BadRequestException({
        code: 'invalid_webhook_url',
        message: 'webhook_url must be a Slack Incoming Webhook URL',
      });
    }
    return trimmed;
  }

  private validateSiteUrl(value?: string | null): string | null {
    if (value == null) return null;
    const trimmed = value.trim().replace(/\/+$/, '');
    if (!trimmed) return null;
    if (!(trimmed.startsWith('https://') || trimmed.startsWith('http://'))) {
      throw new BadRequestException({
        code: 'invalid_site_url',
        message: 'site_url must start with https://',
      });
    }
    return trimmed;
  }

  private validateEmail(value?: string | null): string | null {
    if (value == null) return null;
    const trimmed = value.trim();
    if (!trimmed) return null;
    if (!trimmed.includes('@')) {
      throw new BadRequestException({
        code: 'invalid_email',
        message: 'email must be a valid Atlassian account email',
      });
    }
    return trimmed;
  }

  private validateProjectKey(value?: string | null): string | null {
    if (value == null) return null;
    const trimmed = value.trim().toUpperCase();
    return trimmed || null;
  }
}

import { Inject, Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { eq } from 'drizzle-orm';

import { SecretCipherService } from '../common/crypto/secret-cipher.service';
import { Database, DRIZZLE } from '../database/database.module';
import { environments, orgIntegrations, provisioningWorkspaces } from '../database/schema';

/**
 * Fire-and-forget Slack + Jira notifications for environment lifecycle events, mirroring
 * FastAPI's IntegrationNotifier (app/services/integrations/notifier.py). Makes REAL calls
 * to the Slack Incoming Webhook and Jira Cloud REST API using the org's stored (shared
 * Fernet-encrypted) credentials. Never throws - a failed notification must never break
 * provision/teardown. Per nest-worker-parity the env schema has no notification_flags /
 * jira_issue columns, so ttl/cost sends are deduped in-memory (best-effort per process).
 */

export type SlackEvent = 'ready' | 'failed' | 'ttl_warning' | 'ttl_expired' | 'cost_cap';

const SLACK_TIMEOUT_MS = 8000;
const JIRA_TIMEOUT_MS = 15000;

@Injectable()
export class IntegrationNotifierService {
  private readonly logger = new Logger(IntegrationNotifierService.name);
  // Best-effort de-dup for ttl/cost so a per-minute scheduler does not spam Slack.
  private readonly sentFlags = new Set<string>();

  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly config: ConfigService,
    private readonly cipher: SecretCipherService,
  ) {}

  /** Notify Slack (and auto-create Jira on failure). Never raises. */
  async notifyEnvironmentEvent(
    environmentId: string,
    opts: { event: SlackEvent; message?: string | null; correlationId?: string | null },
  ): Promise<void> {
    try {
      await this.notify(environmentId, opts);
    } catch (err) {
      this.logger.warn(
        `integration_notify_failed env=${environmentId} event=${opts.event} ${(err as Error).message}`,
      );
    }
  }

  private async notify(
    environmentId: string,
    opts: { event: SlackEvent; message?: string | null; correlationId?: string | null },
  ): Promise<void> {
    const [env] = await this.db.select().from(environments).where(eq(environments.id, environmentId));
    if (!env) return;
    if (!env.orgId) {
      this.logger.warn(`notify_skip env=${environmentId} event=${opts.event}: environment has no org_id`);
      return;
    }

    const [integration] = await this.db
      .select()
      .from(orgIntegrations)
      .where(eq(orgIntegrations.orgId, env.orgId));
    if (!integration || !integration.encryptedSlackWebhookUrl) {
      // Common cause of "no Slack messages": the webhook is configured for a DIFFERENT org
      // than the environment belongs to. Log it so it's diagnosable instead of silent.
      this.logger.warn(
        `notify_skip env=${environmentId} event=${opts.event}: no Slack webhook configured for ` +
          `org ${env.orgId} (configure Slack for THIS org on the Integrations page)`,
      );
      return;
    }

    let workspaceName: string | null = null;
    let projectId: string | null = null;
    if (env.workspaceId) {
      const [ws] = await this.db
        .select({ name: provisioningWorkspaces.name, projectId: provisioningWorkspaces.projectId })
        .from(provisioningWorkspaces)
        .where(eq(provisioningWorkspaces.id, env.workspaceId));
      workspaceName = ws?.name ?? null;
      projectId = ws?.projectId ?? null;
    }

    if (!this.projectAllowed(integration.slackProjectIdsJson, projectId)) return;

    await this.maybeSlack(integration, env, workspaceName, opts);
    if (opts.event === 'failed') {
      await this.maybeAutoJira(integration, env, opts.message ?? null);
    }
  }

  private projectAllowed(raw: string | null, projectId: string | null): boolean {
    const cleaned = (raw || '').trim();
    if (!cleaned) return true;
    try {
      const allowed = new Set((JSON.parse(cleaned) as unknown[]).map((x) => String(x)));
      if (allowed.size === 0) return true;
      if (!projectId) return true;
      return allowed.has(projectId);
    } catch {
      return true;
    }
  }

  private async maybeSlack(
    integration: any,
    env: any,
    workspaceName: string | null,
    opts: { event: SlackEvent; message?: string | null; correlationId?: string | null },
  ): Promise<void> {
    if (!integration.encryptedSlackWebhookUrl) return;
    const event = opts.event;
    if (event === 'ready' && !integration.slackNotifyReady) return;
    if (event === 'failed' && !integration.slackNotifyFailed) return;
    if ((event === 'ttl_warning' || event === 'ttl_expired') && !integration.slackNotifyTtlWarning) return;
    if (event === 'cost_cap' && !integration.slackNotifyCostCap) return;

    // De-dup one-shot events (ttl/cost) per environment.
    const dedupKey =
      event === 'ttl_warning' || event === 'ttl_expired' || event === 'cost_cap'
        ? `${env.id}:${event}`
        : null;
    if (dedupKey && this.sentFlags.has(dedupKey)) return;

    let webhook: string;
    try {
      webhook = this.cipher.decrypt(integration.encryptedSlackWebhookUrl);
    } catch {
      return;
    }

    const titles: Record<SlackEvent, string> = {
      ready: 'Preview ready',
      failed: 'Preview failed',
      ttl_warning: 'Preview TTL warning',
      ttl_expired: 'Preview TTL expired',
      cost_cap: 'Preview soft cost cap',
    };
    const title = titles[event];
    const status = String(env.status);
    const portalUrl = this.portalUrl(env.id);
    const blocks = this.buildBlocks({
      title,
      envName: env.name,
      status,
      portalUrl,
      previewUrl: env.previewUrl ?? null,
      workspaceLabel: workspaceName,
      correlationId: opts.correlationId ?? null,
      detail: opts.message ?? null,
    });
    const text = `${title}: ${env.name} (${status})`;
    const ok = await this.postSlack(webhook, text, blocks);
    if (ok && dedupKey) this.sentFlags.add(dedupKey);
    this.logger.log(`slack_notify env=${env.id} event=${event} ok=${ok}`);
  }

  private async maybeAutoJira(integration: any, env: any, message: string | null): Promise<void> {
    if (!integration.jiraAutoCreateOnFailure) return;
    if (!this.jiraConnected(integration)) return;

    let token: string;
    try {
      token = this.cipher.decrypt(integration.encryptedJiraApiToken);
    } catch {
      return;
    }
    const site = (integration.jiraSiteUrl || '').replace(/\/+$/, '');
    const portal = this.portalUrl(env.id);
    const detailParts = [
      `Environment: ${env.name}`,
      `Status: ${env.status}`,
      `Branch: ${env.gitBranch}`,
      `Repo: ${env.gitRepoUrl}`,
    ];
    if (env.latestCommitSha) detailParts.push(`Commit: ${env.latestCommitSha}`);
    if (portal) detailParts.push(`Portal: ${portal}`);
    if (message) detailParts.push(`Error: ${message}`);

    const result = await this.createJiraIssue({
      site,
      email: integration.jiraEmail || '',
      apiToken: token,
      projectKey: integration.jiraProjectKey || '',
      issueType: integration.jiraIssueType || 'Bug',
      summary: `[Launchpad] Preview failed: ${env.name}`,
      description: detailParts.join('\n'),
    });
    if (result) {
      this.logger.log(`jira_auto_created env=${env.id} issue_key=${result.key}`);
    }
  }

  private jiraConnected(i: any): boolean {
    return Boolean(i.jiraSiteUrl && i.jiraEmail && i.encryptedJiraApiToken && i.jiraProjectKey);
  }

  private portalUrl(envId: string): string {
    const base = (this.config.get<string>('PREVIEW_PUBLIC_BASE_URL') ?? 'http://localhost:3000').replace(
      /\/+$/,
      '',
    );
    return `${base}/p/${envId}`;
  }

  private buildBlocks(o: {
    title: string;
    envName: string;
    status: string;
    portalUrl: string | null;
    previewUrl: string | null;
    workspaceLabel: string | null;
    correlationId: string | null;
    detail: string | null;
  }): any[] {
    const fields: any[] = [
      { type: 'mrkdwn', text: `*Environment*\n\`${o.envName}\`` },
      { type: 'mrkdwn', text: `*Status*\n\`${o.status}\`` },
    ];
    if (o.workspaceLabel) fields.push({ type: 'mrkdwn', text: `*Workspace*\n${o.workspaceLabel}` });
    if (o.correlationId) fields.push({ type: 'mrkdwn', text: `*Correlation*\n\`${o.correlationId}\`` });
    const blocks: any[] = [
      { type: 'header', text: { type: 'plain_text', text: o.title.slice(0, 150), emoji: true } },
      { type: 'section', fields: fields.slice(0, 10) },
    ];
    if (o.detail) {
      blocks.push({
        type: 'section',
        text: { type: 'mrkdwn', text: `*Detail*\n\`\`\`${o.detail.slice(0, 1800)}\`\`\`` },
      });
    }
    const links: string[] = [];
    if (o.portalUrl) links.push(`<${o.portalUrl}|Open portal>`);
    if (o.previewUrl) links.push(`<${o.previewUrl}|Open app>`);
    if (links.length) {
      blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: links.join(' · ') }] });
    }
    return blocks;
  }

  private async postSlack(webhookUrl: string, text: string, blocks: any[]): Promise<boolean> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), SLACK_TIMEOUT_MS);
    try {
      const res = await fetch(webhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, blocks }),
        signal: controller.signal,
      });
      if (!res.ok) {
        this.logger.warn(`slack_webhook_failed status=${res.status}`);
        return false;
      }
      return true;
    } catch (err) {
      this.logger.warn(`slack_webhook_error ${(err as Error).message}`);
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  private async createJiraIssue(o: {
    site: string;
    email: string;
    apiToken: string;
    projectKey: string;
    issueType: string;
    summary: string;
    description: string;
  }): Promise<{ key: string; url: string } | null> {
    const url = `${o.site}/rest/api/3/issue`;
    const auth = Buffer.from(`${o.email}:${o.apiToken}`).toString('base64');
    const body = {
      fields: {
        project: { key: o.projectKey },
        summary: o.summary.slice(0, 255),
        issuetype: { name: o.issueType },
        description: {
          type: 'doc',
          version: 1,
          content: [
            { type: 'paragraph', content: [{ type: 'text', text: o.description.slice(0, 4000) }] },
          ],
        },
      },
    };
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), JIRA_TIMEOUT_MS);
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: {
          Authorization: `Basic ${auth}`,
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!res.ok) {
        this.logger.warn(`jira_create_issue_failed status=${res.status}`);
        return null;
      }
      const payload = await res.json();
      const key = String(payload?.key || '').trim();
      if (!key) return null;
      return { key, url: `${o.site}/browse/${key}` };
    } catch (err) {
      this.logger.warn(`jira_create_issue_error ${(err as Error).message}`);
      return null;
    } finally {
      clearTimeout(timer);
    }
  }
}

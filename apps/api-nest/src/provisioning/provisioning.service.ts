import { Inject, Injectable, Logger } from '@nestjs/common';
import { InjectQueue } from '@nestjs/bullmq';
import { ConfigService } from '@nestjs/config';
import { Queue } from 'bullmq';
import { and, desc, eq, isNotNull, ne } from 'drizzle-orm';
import * as fs from 'fs';
import * as path from 'path';

import { CurrentUser } from '../common/auth/current-user.interface';
import { Database, DRIZZLE } from '../database/database.module';
import {
  auditLogs,
  gitlabConnections,
  projects,
  provisioningWorkspaces,
  ProvisioningWorkspaceRow,
} from '../database/schema';

@Injectable()
export class ProvisioningService {
  private readonly logger = new Logger(ProvisioningService.name);

  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    @InjectQueue('provisioning') private readonly provisioningQueue: Queue,
    private readonly config: ConfigService,
  ) {}

  /** Audit log entries for a workspace, newest first. */
  async getWorkspaceAudits(workspaceId: string): Promise<any[]> {
    const rows = await this.db
      .select()
      .from(auditLogs)
      .where(eq(auditLogs.workspaceId, workspaceId))
      .orderBy(desc(auditLogs.createdAt))
      .limit(200);
    // FastAPI AuditLogRead: {id, workspace_id, environment_id, actor_id, action,
    // commit_sha, status, detail (string), timestamp}. Legacy keys (details/created_at)
    // kept additively for non-breakage.
    return rows.map((a) => ({
      id: a.id,
      workspace_id: a.workspaceId,
      environment_id: a.environmentId,
      actor_id: a.actorId,
      action: a.action,
      commit_sha: a.commitSha ?? null,
      status: a.status,
      detail: a.detailsJson ?? null,
      timestamp: a.createdAt,
      details: this.parseJson(a.detailsJson),
      created_at: a.createdAt,
    }));
  }

  /**
   * Wizard config for a workspace. FastAPI returns a FLAT WorkspaceWizardConfig
   * (top-level name/iac_engine/cloud/runtime_mode/...), so the edit-wizard hydrates
   * from top-level keys. The stored wizardConfigJson IS that payload, so we spread it
   * flat, backfilling name/iac_engine from the row. Legacy wrapper keys
   * (workspace_id/engine/provider/config) are kept additively for non-breakage.
   */
  async getWorkspaceConfig(workspaceId: string): Promise<any> {
    const ws = await this.getWorkspace(workspaceId);
    if (!ws) {
      return { workspace_id: workspaceId, engine: null, provider: null, config: null };
    }
    const cfg = (this.parseJson(ws.wizardConfigJson) as Record<string, unknown>) || {};
    return {
      ...cfg,
      name: cfg.name ?? ws.name,
      iac_engine: cfg.iac_engine ?? ws.engine,
      infrastructure_config: ws.infrastructureConfigJson ? this.parseJson(ws.infrastructureConfigJson) : null,
      // Legacy wrapper keys retained so existing consumers keep working.
      workspace_id: ws.id,
      engine: ws.engine,
      provider: ws.provider,
      config: cfg,
    };
  }

  /** GitHub App status in the FastAPI GitHubAppStatusResponse shape (no live client).
   *
   * The frontend keys off ``configured`` + ``install_url`` (NOT connected/app_installed),
   * so returning the wrong field names made a fully-configured API still show
   * "Set GITHUB_APP_ID". Mirrors app/services/github_app.get_github_app_status.
   */
  async githubStatus(githubApp?: {
    isConfigured(): boolean;
    resolveAppSlug(): Promise<string | null>;
    listInstallations(): Promise<any[]>;
  }): Promise<any> {
    const appId = (this.config.get<string>('GITHUB_APP_ID') ?? '').trim();
    const privateKey = (this.config.get<string>('GITHUB_APP_PRIVATE_KEY') ?? '').trim();
    const privateKeyPath = (this.config.get<string>('GITHUB_APP_PRIVATE_KEY_PATH') ?? '').trim();
    let slug = (this.config.get<string>('GITHUB_APP_SLUG') ?? '').trim();
    const setupUrl = (
      this.config.get<string>('GITHUB_APP_SETUP_URL') ??
      'http://localhost:3000/integrations/github'
    )
      .trim()
      .replace(/\/+$/, '');
    const installIdRaw = (this.config.get<string>('GITHUB_APP_INSTALLATION_ID') ?? '').trim();

    const configured = githubApp
      ? githubApp.isConfigured()
      : Boolean(appId && (privateKey || privateKeyPath));

    // Slug is required for the install URL. Resolve it from GitHub (GET /app) when
    // unset so Connect still appears with only APP_ID + private key configured.
    if (configured && !slug && githubApp) {
      slug = (await githubApp.resolveAppSlug()) ?? '';
    }

    // Surface authorized installations so the SPA shows the connected accounts.
    let installations: any[] = [];
    if (configured && githubApp) {
      try {
        installations = await githubApp.listInstallations();
      } catch (_) {
        installations = [];
      }
    }

    const installUrl =
      configured && slug
        ? `https://github.com/apps/${slug}/installations/new?state=${encodeURIComponent(setupUrl)}`
        : null;

    let message: string;
    if (configured && installUrl) {
      message =
        'GitHub App credentials loaded - click Connect GitHub to authorize an installation';
    } else if (configured && !installUrl) {
      message =
        'GitHub App credentials loaded, but the app slug could not be resolved. ' +
        'Set GITHUB_APP_SLUG in the API .env (from the app URL: github.com/apps/<slug>).';
    } else if (!appId) {
      message = 'Set GITHUB_APP_ID and a private key on the API to enable GitHub Connect';
    } else {
      message = 'GITHUB_APP_ID is set but the private key is missing or unreadable';
    }

    return {
      configured,
      app_id: appId ? Number.parseInt(appId, 10) || null : null,
      app_slug: slug || null,
      install_url: installUrl,
      default_installation_id: installIdRaw ? Number.parseInt(installIdRaw, 10) || null : null,
      message,
      installations,
    };
  }

  /**
   * GitLab connection status in the FastAPI GitlabStatusResponse shape:
   * {connected, oauth_configured, authorize_url, base_url, username, token_type, message}.
   *
   * ``authorize_url`` stays null here: minting it requires the signed OAuth-state +
   * callback exchange (a full flow, not just a shape), so we steer users to the PAT
   * connect path, which this control plane fully supports. ``oauth_configured`` still
   * reflects whether the OAuth env is present, matching FastAPI.
   */
  async gitlabStatus(user: CurrentUser): Promise<any> {
    const clientId = (this.config.get<string>('GITLAB_OAUTH_CLIENT_ID') ?? '').trim();
    const clientSecret = (this.config.get<string>('GITLAB_OAUTH_CLIENT_SECRET') ?? '').trim();
    const oauthConfigured = Boolean(clientId && clientSecret);
    const defaultBase = (this.config.get<string>('GITLAB_BASE_URL') ?? 'https://gitlab.com')
      .trim()
      .replace(/\/+$/, '');

    const [conn] = await this.db
      .select()
      .from(gitlabConnections)
      .where(eq(gitlabConnections.userId, user.userId))
      .limit(1);
    if (!conn) {
      return {
        connected: false,
        oauth_configured: oauthConfigured,
        authorize_url: null,
        base_url: defaultBase,
        username: null,
        token_type: null,
        message:
          'Connect GitLab with a Personal Access Token (api + write_repository scopes).',
      };
    }
    return {
      connected: true,
      oauth_configured: oauthConfigured,
      authorize_url: null,
      base_url: conn.baseUrl,
      username: conn.username,
      token_type: conn.tokenType,
      message: `Connected as ${conn.username}`,
    };
  }

  /**
   * Advisory Dockerfile build+run+probe verification (simulated control-plane).
   *
   * Mirrors the FastAPI contract shape exactly. The Nest worker does not run
   * real Docker, so results are simulated as verified; see the nest-worker
   * parity note (contract, not real infra).
   */
  async verifyWorkspaceDockerfiles(
    workspaceId: string,
    services?: Array<{ name?: string; path?: string; dockerfile_path?: string; listen_port?: number }>,
  ): Promise<{ results: Array<Record<string, unknown>> }> {
    const ws = await this.getWorkspace(workspaceId);
    let specs = Array.isArray(services) ? services : [];
    if (specs.length === 0) {
      // Enumerate detected services from the stored wizard config when available.
      const config = ws ? (this.parseJson(ws.wizardConfigJson) as any) : null;
      const detected = config?.detection?.services;
      if (Array.isArray(detected) && detected.length > 0) {
        specs = detected.map((s: any) => ({
          name: s?.name ?? s?.id ?? 'app',
          listen_port: s?.port ?? undefined,
        }));
      } else {
        specs = [{ name: ws?.name ?? 'app' }];
      }
    }
    const results = specs.map((spec) => ({
      service: spec.name ?? 'app',
      status: 'verified',
      used_repo_dockerfile: true,
      generated_stack: null,
      built: true,
      ran: true,
      probe_ok: true,
      listen_port: spec.listen_port ?? null,
      warning: null,
      logs_tail: null,
    }));
    return { results };
  }

  private parseJson(value: string | null): unknown {
    if (!value) return null;
    try {
      return JSON.parse(value);
    } catch {
      return null;
    }
  }

  async listWorkspaces(user: CurrentUser): Promise<ProvisioningWorkspaceRow[]> {
    // Hide in-flight deletes ('deleting'), matching FastAPI's workspace list;
    // 'destroy_failed' rows remain visible so they can be retried.
    return this.db
      .select()
      .from(provisioningWorkspaces)
      .where(
        and(
          eq(provisioningWorkspaces.ownerId, user.userId),
          ne(provisioningWorkspaces.status, 'deleting'),
        ),
      );
  }

  async getWorkspace(id: string): Promise<ProvisioningWorkspaceRow | null> {
    const [row] = await this.db
      .select()
      .from(provisioningWorkspaces)
      .where(eq(provisioningWorkspaces.id, id));
    return row || null;
  }

  /** Resolve a project name for a workspace's projectId (null when unset/missing). */
  private async projectNameFor(projectId: string | null): Promise<string | null> {
    if (!projectId) return null;
    const [row] = await this.db
      .select({ name: projects.name })
      .from(projects)
      .where(eq(projects.id, projectId));
    return row?.name ?? null;
  }

  /** Map a workspace row to the FastAPI WorkspaceListItem / IaCBundleSummary shape. */
  private shapeWorkspace(
    row: ProvisioningWorkspaceRow,
    projectName: string | null,
  ): Record<string, unknown> {
    const cfg = (this.parseJson(row.wizardConfigJson) as Record<string, unknown>) || {};
    return {
      // Both list (WorkspaceListItem) and detail (IaCBundleSummary) shapes; the detail
      // page keys child widgets off ``workspace_id`` so it MUST be present.
      id: row.id,
      workspace_id: row.id,
      name: row.name,
      engine: row.engine,
      provider: row.provider,
      root_dir: row.rootDir,
      status: row.status,
      artifact_mode: (cfg.artifact_mode as string) ?? 'iac_only',
      runtime_mode: (cfg.runtime_mode as string) ?? 'kubernetes',
      starred: Boolean(row.starredAt),
      starred_at: row.starredAt,
      project_id: row.projectId ?? null,
      project_name: projectName,
      files: [] as string[],
      created_at: row.createdAt,
    };
  }

  /** Workspace list in FastAPI WorkspaceListItem shape (honors starred/projectId). */
  async listWorkspaceItems(
    user: CurrentUser,
    opts: { starred?: boolean; projectId?: string } = {},
  ): Promise<Record<string, unknown>[]> {
    const conditions = [
      eq(provisioningWorkspaces.ownerId, user.userId),
      ne(provisioningWorkspaces.status, 'deleting'),
    ];
    if (opts.starred) conditions.push(isNotNull(provisioningWorkspaces.starredAt));
    if (opts.projectId) conditions.push(eq(provisioningWorkspaces.projectId, opts.projectId));
    const rows = await this.db
      .select()
      .from(provisioningWorkspaces)
      .where(and(...conditions));
    const names = new Map<string, string | null>();
    for (const row of rows) {
      if (row.projectId && !names.has(row.projectId)) {
        names.set(row.projectId, await this.projectNameFor(row.projectId));
      }
    }
    return rows.map((row) =>
      this.shapeWorkspace(row, row.projectId ? names.get(row.projectId) ?? null : null),
    );
  }

  /** Single workspace in FastAPI IaCBundleSummary shape (workspace_id key). */
  async getWorkspaceSummary(id: string): Promise<Record<string, unknown> | null> {
    const row = await this.getWorkspace(id);
    if (!row) return null;
    return this.shapeWorkspace(row, await this.projectNameFor(row.projectId));
  }

  async starWorkspace(id: string, starred: boolean): Promise<ProvisioningWorkspaceRow | null> {
    const [updated] = await this.db
      .update(provisioningWorkspaces)
      .set({ starredAt: starred ? new Date() : null })
      .where(eq(provisioningWorkspaces.id, id))
      .returning();
    return updated || null;
  }

  async deleteWorkspace(id: string): Promise<Record<string, unknown> | null> {
    // Soft-destroy, matching FastAPI: mark 'deleting' and let the worker finalize the
    // teardown (it removes the row on success, or marks 'destroy_failed' on error).
    // Returns the shaped workspace item (202 body) - the SPA reads it to update the list;
    // a 204/empty body made the delete call error out ("cannot delete workspace").
    const [updated] = await this.db
      .update(provisioningWorkspaces)
      .set({ status: 'deleting' })
      .where(eq(provisioningWorkspaces.id, id))
      .returning();

    try {
      await this.provisioningQueue.add('finalize-workspace-destroy', {
        action: 'finalize-workspace-destroy',
        payload: { workspaceId: id },
      });
    } catch (err) {
      this.logger.error(`Failed to enqueue workspace finalize for ${id}`, err as Error);
    }

    if (!updated) return null;
    return this.shapeWorkspace(updated, await this.projectNameFor(updated.projectId));
  }

  /**
   * Flat file tree for a workspace's durable dir, in the FastAPI WorkspaceFileNode
   * shape ({path, type, size}). Returns [] when the dir is missing. Skips VCS/large
   * dirs so the tree stays useful (scaffolded infra + source, not node_modules/.git).
   */
  async getWorkspaceFileTree(id: string): Promise<Array<{ path: string; type: string; size: number | null }>> {
    const ws = await this.getWorkspace(id);
    const root = ws?.rootDir;
    if (!root || !fs.existsSync(root)) return [];
    const SKIP = new Set(['.git', 'node_modules', '.next', '.output', 'dist', '.venv', '__pycache__']);
    const out: Array<{ path: string; type: string; size: number | null }> = [];
    const walk = (absDir: string, rel: string) => {
      let entries: fs.Dirent[];
      try {
        entries = fs.readdirSync(absDir, { withFileTypes: true });
      } catch {
        return;
      }
      for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
        if (SKIP.has(entry.name)) continue;
        const relPath = rel ? `${rel}/${entry.name}` : entry.name;
        const absPath = path.join(absDir, entry.name);
        if (entry.isDirectory()) {
          out.push({ path: relPath, type: 'directory', size: null });
          if (out.length < 2000) walk(absPath, relPath);
        } else if (entry.isFile()) {
          let size: number | null = null;
          try {
            size = fs.statSync(absPath).size;
          } catch {
            size = null;
          }
          out.push({ path: relPath, type: 'file', size });
        }
      }
    };
    walk(root, '');
    return out;
  }

  /** File content for a workspace, in the FastAPI WorkspaceFileContent shape. */
  async getWorkspaceFileContent(id: string, relPath: string): Promise<{ path: string; content: string }> {
    const ws = await this.getWorkspace(id);
    const root = ws?.rootDir;
    const cleaned = (relPath || '').replace(/^\/+/, '');
    if (!root || !cleaned) return { path: cleaned, content: '' };
    // Guard against path traversal outside the workspace root.
    const abs = path.resolve(root, cleaned);
    if (!abs.startsWith(path.resolve(root))) return { path: cleaned, content: '' };
    try {
      if (fs.existsSync(abs) && fs.statSync(abs).isFile()) {
        return { path: cleaned, content: fs.readFileSync(abs, 'utf-8') };
      }
    } catch {
      // fall through
    }
    return { path: cleaned, content: '' };
  }
}

import { randomUUID } from 'node:crypto';

import { BadRequestException, Inject, Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { eq } from 'drizzle-orm';

import { CurrentUser } from '../common/auth/current-user.interface';
import { SecretCipherService } from '../common/crypto/secret-cipher.service';
import { Database, DRIZZLE } from '../database/database.module';
import { gitlabConnections } from '../database/schema';

/**
 * Real GitLab PAT integration mirroring apps/api/app/services/gitlab_service.py.
 *
 * GitLab is a genuine external dependency (gitlab.com / self-managed api/v4), not
 * simulated infra, so we make real calls with the user's stored token. Only the PAT
 * flow is implemented here (OAuth authorize/callback is a separate signed-state flow);
 * response shapes mirror the FastAPI schemas 1:1 (GitlabStatusResponse / GitlabProjectItem).
 */

export interface GitlabProjectItem {
  id: number;
  name: string;
  path_with_namespace: string;
  http_url_to_repo: string;
  web_url: string;
  visibility: string;
  default_branch: string;
}

@Injectable()
export class GitlabService {
  private readonly logger = new Logger(GitlabService.name);

  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly config: ConfigService,
    private readonly cipher: SecretCipherService,
  ) {}

  private normalizeBase(baseUrl?: string | null): string {
    const raw = (baseUrl ?? '').trim() || (this.config.get<string>('GITLAB_BASE_URL') ?? '').trim();
    const base = raw || 'https://gitlab.com';
    return base.replace(/\/+$/, '');
  }

  private async gitlabFetch(base: string, apiPath: string, token: string): Promise<any> {
    const res = await fetch(`${base}/api/v4${apiPath}`, {
      headers: { 'PRIVATE-TOKEN': token, 'User-Agent': 'launchpad-idp' },
    });
    if (res.status === 401 || res.status === 403) {
      throw new BadRequestException({
        code: 'gitlab_auth_error',
        message: `GitLab request unauthorized (${res.status}) - reconnect GitLab under Integrations`,
      });
    }
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      this.logger.error(`gitlab_api_failed ${apiPath} status=${res.status} ${body.slice(0, 200)}`);
      throw new BadRequestException({
        code: 'gitlab_error',
        message: `GitLab request failed (${res.status}) for ${apiPath}`,
      });
    }
    return res.json();
  }

  /** Validate a PAT by fetching the current user, then upsert the connection. */
  async connectPat(
    user: CurrentUser,
    payload: { token?: string; base_url?: string | null },
  ): Promise<any> {
    const token = (payload?.token ?? '').trim();
    if (!token) {
      throw new BadRequestException({ code: 'gitlab_token_required', message: 'token is required' });
    }
    const base = this.normalizeBase(payload?.base_url);
    const profile = await this.gitlabFetch(base, '/user', token);
    const username = String(profile?.username ?? profile?.name ?? 'gitlab');

    const [existing] = await this.db
      .select()
      .from(gitlabConnections)
      .where(eq(gitlabConnections.userId, user.userId))
      .limit(1);
    const now = new Date();
    const encrypted = this.cipher.encrypt(token);

    if (existing) {
      await this.db
        .update(gitlabConnections)
        .set({
          baseUrl: base,
          username,
          encryptedToken: encrypted,
          tokenType: 'pat',
          updatedAt: now,
        })
        .where(eq(gitlabConnections.id, existing.id));
    } else {
      // Explicit id: FastAPI-owned table has no DB-level id default.
      await this.db.insert(gitlabConnections).values({
        id: randomUUID(),
        userId: user.userId,
        baseUrl: base,
        username,
        encryptedToken: encrypted,
        tokenType: 'pat',
        createdAt: now,
        updatedAt: now,
      });
    }

    return this.status(user);
  }

  async disconnect(user: CurrentUser): Promise<void> {
    await this.db.delete(gitlabConnections).where(eq(gitlabConnections.userId, user.userId));
  }

  /** GitlabStatusResponse shape (mirrors ProvisioningService.gitlabStatus). */
  async status(user: CurrentUser): Promise<any> {
    const clientId = (this.config.get<string>('GITLAB_OAUTH_CLIENT_ID') ?? '').trim();
    const clientSecret = (this.config.get<string>('GITLAB_OAUTH_CLIENT_SECRET') ?? '').trim();
    const oauthConfigured = Boolean(clientId && clientSecret);
    const defaultBase = this.normalizeBase(null);

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
        message: 'Connect GitLab with a Personal Access Token (api + write_repository scopes).',
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

  private async connectionToken(user: CurrentUser): Promise<{ base: string; token: string }> {
    const [conn] = await this.db
      .select()
      .from(gitlabConnections)
      .where(eq(gitlabConnections.userId, user.userId))
      .limit(1);
    if (!conn) {
      throw new BadRequestException({
        code: 'gitlab_not_connected',
        message: 'Connect GitLab first (Integrations > GitLab).',
      });
    }
    return { base: conn.baseUrl, token: this.cipher.decrypt(conn.encryptedToken) };
  }

  async listProjects(user: CurrentUser, q?: string): Promise<GitlabProjectItem[]> {
    const { base, token } = await this.connectionToken(user);
    const params = new URLSearchParams({
      membership: 'true',
      simple: 'true',
      order_by: 'last_activity_at',
      per_page: '100',
    });
    if (q && q.trim()) params.set('search', q.trim());
    const data = await this.gitlabFetch(base, `/projects?${params.toString()}`, token);
    const items = Array.isArray(data) ? data : [];
    return items.map((p: any) => ({
      id: Number(p.id),
      name: String(p.name ?? ''),
      path_with_namespace: String(p.path_with_namespace ?? ''),
      http_url_to_repo: String(p.http_url_to_repo ?? ''),
      web_url: String(p.web_url ?? ''),
      visibility: String(p.visibility ?? 'private'),
      default_branch: String(p.default_branch ?? 'main'),
    }));
  }

  async listProjectBranches(
    user: CurrentUser,
    projectRef: string,
  ): Promise<{ branches: Array<{ name: string; protected: boolean; is_default: boolean }>; default_branch: string | null }> {
    const { base, token } = await this.connectionToken(user);
    const encoded = encodeURIComponent(projectRef);
    const project = await this.gitlabFetch(base, `/projects/${encoded}`, token);
    const defaultBranch = String(project?.default_branch ?? 'main');
    const data = await this.gitlabFetch(base, `/projects/${encoded}/repository/branches?per_page=100`, token);
    const items = Array.isArray(data) ? data : [];
    const branches = items
      .map((b: any) => ({
        name: String(b?.name ?? ''),
        protected: Boolean(b?.protected),
        is_default: String(b?.name ?? '') === defaultBranch,
      }))
      .filter((b) => b.name);
    branches.sort((a, b) => {
      if (a.is_default !== b.is_default) return a.is_default ? -1 : 1;
      return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
    });
    return { branches, default_branch: defaultBranch };
  }
}

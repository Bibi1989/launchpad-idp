import { createSign } from 'node:crypto';
import { readFileSync } from 'node:fs';

import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

/**
 * Real GitHub App integration mirroring apps/api/app/services/github_app.py.
 *
 * The GitHub App is a genuine external dependency (api.github.com), not simulated
 * infra, so when GITHUB_APP_ID + a private key are configured we actually call
 * GitHub - exactly like FastAPI - instead of returning empty stubs. This keeps
 * GitHub Connect working identically in NestJS API mode. Response field names are
 * mirrored 1:1 from the FastAPI schemas (installations/repos snake_case; the
 * repository SEARCH item is camelCase - see apps/web GitHubRepositorySearchItem).
 */

const GITHUB_API = 'https://api.github.com';

export interface GitHubInstallationSummary {
  id: number;
  account_login: string;
  account_type: string;
  target_type: string | null;
  repository_selection: string | null;
}

export interface GitHubRepositorySummary {
  id: number;
  name: string;
  full_name: string;
  private: boolean;
  html_url: string;
  default_branch: string;
  owner_login: string;
}

export interface GitHubRepositorySearchItem {
  id: number;
  name: string;
  fullName: string;
  isPrivate: boolean;
  owner: string;
  defaultBranch: string;
  htmlUrl: string;
}

export interface GitHubBranchSummary {
  name: string;
  protected: boolean;
  is_default: boolean;
}

/** Raised when GitHub App credentials are missing or invalid (maps to HTTP 400). */
export class GitHubAppAuthError extends Error {}

@Injectable()
export class GithubAppService {
  private readonly logger = new Logger(GithubAppService.name);

  constructor(private readonly config: ConfigService) {}

  private cfg(key: string): string {
    return (this.config.get<string>(key) ?? '').trim();
  }

  get appId(): string {
    return this.cfg('GITHUB_APP_ID');
  }

  /**
   * Load and normalize the App private key: accepts raw PEM, escaped-\n PEM,
   * base64-encoded PEM, or a filesystem path (via GITHUB_APP_PRIVATE_KEY_PATH or a
   * path value in GITHUB_APP_PRIVATE_KEY). Mirrors _coerce_private_key_pem.
   */
  loadPrivateKey(): string {
    const raw = this.cfg('GITHUB_APP_PRIVATE_KEY');
    const pathValue = this.cfg('GITHUB_APP_PRIVATE_KEY_PATH');

    if (raw) {
      const pem = this.coercePem(raw);
      if (pem) return pem;
      // Treat non-PEM raw values as a path fallback.
      try {
        const fromFile = this.coercePem(readFileSync(raw.replace(/\\n/g, '\n'), 'utf8'));
        if (fromFile) return fromFile;
      } catch (_) {
        // fall through to the error below
      }
      throw new GitHubAppAuthError(
        'GITHUB_APP_PRIVATE_KEY must be a PEM string, base64-encoded PEM, or an existing file path',
      );
    }

    if (pathValue) {
      let contents: string;
      try {
        contents = readFileSync(pathValue, 'utf8');
      } catch (_) {
        throw new GitHubAppAuthError(`GITHUB_APP_PRIVATE_KEY_PATH does not exist: ${pathValue}`);
      }
      const pem = this.coercePem(contents);
      if (!pem) {
        throw new GitHubAppAuthError(
          `GITHUB_APP_PRIVATE_KEY_PATH does not contain a readable PEM: ${pathValue}`,
        );
      }
      return pem;
    }

    throw new GitHubAppAuthError(
      'GitHub App private key missing - set GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_PATH',
    );
  }

  private coercePem(value: string): string | null {
    const text = value.trim().replace(/^["']|["']$/g, '');
    if (!text) return null;
    const normalized = text.replace(/\\n/g, '\n');
    if (normalized.includes('BEGIN') && normalized.includes('PRIVATE KEY')) {
      return normalized.endsWith('\n') ? normalized : normalized + '\n';
    }
    const compact = text.replace(/\s+/g, '');
    if (compact.length < 32) return null;
    try {
      const decoded = Buffer.from(compact, 'base64').toString('utf8');
      if (decoded.includes('BEGIN') && decoded.includes('PRIVATE KEY')) {
        return decoded.endsWith('\n') ? decoded : decoded + '\n';
      }
    } catch (_) {
      return null;
    }
    return null;
  }

  isConfigured(): boolean {
    if (!this.appId) return false;
    try {
      this.loadPrivateKey();
      return true;
    } catch (_) {
      return false;
    }
  }

  /** Mint a short-lived (10 min) App JWT signed RS256 with the private key. */
  private appJwt(): string {
    const privateKey = this.loadPrivateKey();
    const now = Math.floor(Date.now() / 1000);
    const header = { alg: 'RS256', typ: 'JWT' };
    // iat backdated 60s to tolerate clock drift; exp capped to GitHub's 10 min max.
    const payload = { iat: now - 60, exp: now + 9 * 60, iss: this.appId };
    const b64 = (obj: unknown) =>
      Buffer.from(JSON.stringify(obj)).toString('base64url');
    const signingInput = `${b64(header)}.${b64(payload)}`;
    const signer = createSign('RSA-SHA256');
    signer.update(signingInput);
    signer.end();
    const signature = signer.sign(privateKey).toString('base64url');
    return `${signingInput}.${signature}`;
  }

  private async ghFetch(
    path: string,
    token: string,
    tokenType: 'Bearer' | 'token' = 'Bearer',
  ): Promise<any> {
    const res = await fetch(`${GITHUB_API}${path}`, {
      headers: {
        Authorization: `${tokenType} ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'launchpad-idp',
      },
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      this.logger.error(`github_api_failed ${path} status=${res.status} ${body.slice(0, 200)}`);
      throw new GitHubAppAuthError(`GitHub API request failed (${res.status}) for ${path}`);
    }
    return res.json();
  }

  /**
   * Resolve which installation id to use: explicit arg, else GITHUB_APP_INSTALLATION_ID,
   * else the sole installation. Mirrors github_app.resolve_installation_id.
   */
  async resolveInstallationId(installationId?: number | null): Promise<number> {
    if (installationId != null) return installationId;
    const envId = this.cfg('GITHUB_APP_INSTALLATION_ID');
    if (envId) {
      const n = Number.parseInt(envId, 10);
      if (n) return n;
    }
    const list = await this.listInstallations();
    if (list.length === 1) return list[0].id;
    if (list.length === 0) {
      throw new GitHubAppAuthError(
        'GitHub App has no installations - install it on an organization first',
      );
    }
    throw new GitHubAppAuthError(
      'Multiple GitHub App installations found - pass installation_id or organization',
    );
  }

  /** Public wrapper: mint an installation token, resolving the id when omitted. */
  async getInstallationToken(installationId?: number | null): Promise<string> {
    const id = await this.resolveInstallationId(installationId);
    return this.installationToken(id);
  }

  /**
   * Resolve a git clone token: prefer GITHUB_PAT (local/dev), else a GitHub App
   * installation token when the App is configured and the repo is on github.com.
   * Returns undefined when neither is available (public-repo / no-auth clone).
   * Mirrors github_app.resolve_git_clone_token.
   */
  async resolveCloneToken(opts: {
    repoUrl?: string;
    installationId?: number | null;
    useGithubApp?: boolean;
    preferPat?: boolean;
  }): Promise<string | undefined> {
    const preferPat = opts.preferPat !== false;
    const pat = this.cfg('GITHUB_PAT');
    if (preferPat && pat) return pat;
    const isGithub = /(^|\/\/|@)github\.com[:/]/i.test(opts.repoUrl ?? '');
    if ((opts.useGithubApp ?? true) && isGithub && this.isConfigured()) {
      try {
        return await this.getInstallationToken(opts.installationId);
      } catch (err) {
        this.logger.warn(`git_clone_token_unavailable ${(err as Error).message}`);
        return pat || undefined;
      }
    }
    return pat || undefined;
  }

  private async installationToken(installationId: number): Promise<string> {
    const res = await fetch(`${GITHUB_API}/app/installations/${installationId}/access_tokens`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${this.appJwt()}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'launchpad-idp',
      },
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      this.logger.error(
        `github_installation_token_failed id=${installationId} status=${res.status} ${body.slice(0, 200)}`,
      );
      throw new GitHubAppAuthError(
        `Failed to mint installation access token for installation ${installationId}`,
      );
    }
    const data = await res.json();
    const token = data?.token;
    if (!token) throw new GitHubAppAuthError('GitHub App returned an empty installation token');
    return String(token);
  }

  /** Resolve the app slug: prefer GITHUB_APP_SLUG, else GET /app via JWT. */
  async resolveAppSlug(): Promise<string | null> {
    const configured = this.cfg('GITHUB_APP_SLUG');
    if (configured) return configured;
    try {
      const app = await this.ghFetch('/app', this.appJwt());
      const slug = app?.slug;
      if (typeof slug === 'string' && slug.trim()) return slug.trim();
      const htmlUrl = app?.html_url;
      if (typeof htmlUrl === 'string' && htmlUrl.includes('/apps/')) {
        return htmlUrl.replace(/\/+$/, '').split('/apps/').pop()?.trim() || null;
      }
    } catch (err) {
      this.logger.warn(`github_app_slug_resolve_failed ${(err as Error).message}`);
    }
    return null;
  }

  async listInstallations(): Promise<GitHubInstallationSummary[]> {
    const data = await this.ghFetch('/app/installations?per_page=100', this.appJwt());
    const items = Array.isArray(data) ? data : [];
    return items.map((inst: any) => {
      const account = inst?.account ?? {};
      return {
        id: Number(inst.id),
        account_login: String(account.login ?? inst.id),
        account_type: String(account.type ?? 'Unknown'),
        target_type: inst.target_type ?? null,
        repository_selection: inst.repository_selection ?? null,
      };
    });
  }

  async listInstallationRepositories(
    installationId: number,
    limit = 100,
  ): Promise<GitHubRepositorySummary[]> {
    const capped = Math.max(1, Math.min(limit, 200));
    const token = await this.installationToken(installationId);
    const repos: GitHubRepositorySummary[] = [];
    let page = 1;
    while (repos.length < capped) {
      const data = await this.ghFetch(
        `/installation/repositories?per_page=100&page=${page}`,
        token,
        'token',
      );
      const items: any[] = Array.isArray(data?.repositories) ? data.repositories : [];
      if (items.length === 0) break;
      for (const item of items) {
        const fullName = String(item.full_name ?? '');
        const owner = String(item?.owner?.login ?? fullName.split('/')[0] ?? '');
        repos.push({
          id: Number(item.id),
          name: String(item.name ?? fullName.split('/').pop() ?? ''),
          full_name: fullName,
          private: Boolean(item.private),
          html_url: String(item.html_url ?? ''),
          default_branch: String(item.default_branch ?? 'main'),
          owner_login: owner,
        });
        if (repos.length >= capped) break;
      }
      if (items.length < 100) break;
      page += 1;
    }
    repos.sort((a, b) => a.full_name.toLowerCase().localeCompare(b.full_name.toLowerCase()));
    return repos;
  }

  async searchRepositories(opts: {
    q?: string;
    page?: number;
    perPage?: number;
    installationId?: number;
  }): Promise<GitHubRepositorySearchItem[]> {
    const perPage = opts.perPage ?? 100;
    const page = opts.page ?? 1;
    let all: GitHubRepositorySummary[] = [];

    if (this.isConfigured()) {
      let installIds: number[] = [];
      if (opts.installationId != null) {
        installIds = [opts.installationId];
      } else {
        try {
          installIds = (await this.listInstallations()).map((s) => s.id);
        } catch (_) {
          installIds = [];
        }
      }
      const seen = new Set<number>();
      for (const id of installIds) {
        try {
          const repos = await this.listInstallationRepositories(id, 200);
          for (const repo of repos) {
            if (!seen.has(repo.id)) {
              seen.add(repo.id);
              all.push(repo);
            }
          }
        } catch (err) {
          this.logger.warn(
            `github_search_inst_repo_failed id=${id} ${(err as Error).message}`,
          );
        }
      }
    }

    const query = (opts.q ?? '').trim().toLowerCase();
    if (query) {
      all = all.filter(
        (r) =>
          r.name.toLowerCase().includes(query) ||
          r.full_name.toLowerCase().includes(query) ||
          r.owner_login.toLowerCase().includes(query),
      );
    }
    const offset = Math.max(0, (page - 1) * perPage);
    return all.slice(offset, offset + perPage).map((r) => ({
      id: r.id,
      name: r.name,
      fullName: r.full_name,
      isPrivate: r.private,
      owner: r.owner_login,
      defaultBranch: r.default_branch,
      htmlUrl: r.html_url,
    }));
  }

  async listRepositoryBranches(opts: {
    installationId: number;
    fullName: string;
    limit?: number;
  }): Promise<GitHubBranchSummary[]> {
    const repoName = opts.fullName.trim();
    if (!repoName.includes('/')) {
      throw new GitHubAppAuthError('full_name must be owner/repo');
    }
    const capped = Math.max(1, Math.min(opts.limit ?? 100, 200));
    const token = await this.installationToken(opts.installationId);
    const repo = await this.ghFetch(`/repos/${repoName}`, token, 'token');
    const defaultBranch = String(repo?.default_branch ?? 'main');
    const branches: GitHubBranchSummary[] = [];
    let page = 1;
    while (branches.length < capped) {
      const data = await this.ghFetch(
        `/repos/${repoName}/branches?per_page=100&page=${page}`,
        token,
        'token',
      );
      const items: any[] = Array.isArray(data) ? data : [];
      if (items.length === 0) break;
      for (const branch of items) {
        const name = String(branch?.name ?? '');
        if (!name) continue;
        branches.push({
          name,
          protected: Boolean(branch?.protected),
          is_default: name === defaultBranch,
        });
        if (branches.length >= capped) break;
      }
      if (items.length < 100) break;
      page += 1;
    }
    branches.sort((a, b) => {
      if (a.is_default !== b.is_default) return a.is_default ? -1 : 1;
      return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
    });
    return branches;
  }
}

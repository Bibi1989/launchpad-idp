import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { execFile } from 'child_process';
import { randomUUID } from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { promisify } from 'util';

const execFileAsync = promisify(execFile);

export interface ImportCloneResult {
  importId: string;
  rootDir: string;
  commitSha: string;
  repoUrl: string;
  branch: string;
}

export interface ImportMeta {
  import_id: string;
  repo_url: string;
  branch: string;
  commit_sha: string;
  created_at: string;
  root_dir: string;
}

const IMPORT_META_FILE = '.launchpad-import.json';

@Injectable()
export class GitImporterService {
  private readonly logger = new Logger(GitImporterService.name);

  constructor(private readonly config: ConfigService) {}

  /** Name of the per-import metadata file written at a clone root. */
  get metaFileName(): string {
    return IMPORT_META_FILE;
  }

  get importsRoot(): string {
    const configured = (this.config.get<string>('repo_import_root') || '').trim();
    if (configured) {
      return path.resolve(configured);
    }
    return '/tmp/launchpad/imports';
  }

  get durableWorkspacesRoot(): string {
    const configured = (this.config.get<string>('workspaces_root') || '').trim();
    if (configured) {
      return path.resolve(configured);
    }
    return '/tmp/launchpad/workspaces';
  }

  async clone(params: {
    repoUrl: string;
    branch?: string;
    token?: string;
    importId?: string;
  }): Promise<ImportCloneResult> {
    const { repoUrl, token, importId } = params;
    const branch = (params.branch || 'main').trim() || 'main';
    const cleanedUrl = this.validateRepoUrl(repoUrl);
    const iid = importId || randomUUID();
    const dest = path.join(this.importsRoot, iid);

    if (fs.existsSync(dest)) {
      fs.rmSync(dest, { recursive: true, force: true });
    }
    fs.mkdirSync(path.dirname(dest), { recursive: true });

    let authUrl = cleanedUrl;
    if (token && (cleanedUrl.startsWith('https://') || cleanedUrl.startsWith('http://'))) {
      const urlObj = new URL(cleanedUrl);
      urlObj.username = 'x-access-token';
      urlObj.password = token;
      authUrl = urlObj.toString();
    }

    try {
      await execFileAsync('git', ['clone', '--depth', '1', '--branch', branch, authUrl, dest], {
        timeout: 120000,
      });

      const { stdout } = await execFileAsync('git', ['rev-parse', 'HEAD'], { cwd: dest });
      const commitSha = stdout.trim();

      const meta: ImportMeta = {
        import_id: iid,
        repo_url: cleanedUrl,
        branch,
        commit_sha: commitSha,
        created_at: new Date().toISOString(),
        root_dir: dest,
      };

      fs.writeFileSync(path.join(dest, IMPORT_META_FILE), JSON.stringify(meta, null, 2), 'utf-8');

      this.logger.log(`Cloned repo import_id=${iid} branch=${branch} sha=${commitSha.substring(0, 12)}`);

      return {
        importId: iid,
        rootDir: dest,
        commitSha,
        repoUrl: cleanedUrl,
        branch,
      };
    } catch (err: any) {
      if (fs.existsSync(dest)) {
        fs.rmSync(dest, { recursive: true, force: true });
      }
      const message = err?.stderr || err?.message || String(err);
      throw new Error(`Git clone failed: ${message.substring(0, 500)}`);
    }
  }

  getRoot(importId: string): string {
    if (!importId || importId.includes('..') || importId.includes('/') || importId.includes('\\')) {
      throw new Error(`Invalid import_id format`);
    }
    const dest = path.join(this.importsRoot, importId);
    if (!fs.existsSync(dest)) {
      throw new Error(`Import workspace ${importId} not found`);
    }
    return dest;
  }

  readMeta(importId: string): ImportMeta {
    const root = this.getRoot(importId);
    const metaPath = path.join(root, IMPORT_META_FILE);
    if (!fs.existsSync(metaPath)) {
      return {
        import_id: importId,
        repo_url: '',
        branch: 'main',
        commit_sha: '',
        created_at: new Date().toISOString(),
        root_dir: root,
      };
    }
    const content = fs.readFileSync(metaPath, 'utf-8');
    return JSON.parse(content);
  }

  cleanup(importId: string): void {
    try {
      const root = path.join(this.importsRoot, importId);
      if (fs.existsSync(root)) {
        fs.rmSync(root, { recursive: true, force: true });
      }
    } catch (err) {
      this.logger.warn(`Failed to cleanup import ${importId}: ${err}`);
    }
  }

  allocateDurableDir(name: string): string {
    const slug = name.toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/^-+|-+$/g, '');
    const dirName = `${slug || 'workspace'}-${randomUUID().substring(0, 8)}`;
    const durablePath = path.join(this.durableWorkspacesRoot, dirName);
    fs.mkdirSync(this.durableWorkspacesRoot, { recursive: true });
    return durablePath;
  }

  private validateRepoUrl(url: string): string {
    const cleaned = url.trim();
    if (!cleaned.startsWith('https://') && !cleaned.startsWith('http://') && !cleaned.startsWith('git@') && !cleaned.startsWith('ssh://')) {
      throw new Error('git_repo_url must be an http(s), git@, or ssh URL');
    }
    return cleaned;
  }
}

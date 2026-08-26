import {
  BadRequestException,
  ConflictException,
  Inject,
  Injectable,
  Logger,
  NotFoundException,
  UnprocessableEntityException,
} from '@nestjs/common';
import { randomUUID } from 'crypto';
import { and, eq } from 'drizzle-orm';
import * as fs from 'fs';
import * as path from 'path';

import { CurrentUser } from '../common/auth/current-user.interface';
import { Database, DRIZZLE } from '../database/database.module';
import { provisioningWorkspaces } from '../database/schema';
import { GithubAppService } from '../provisioning/github-app.service';
import {
  RepoImportCreateRequestDto,
  RepoImportSaveRequestDto,
  RepoImportSaveResultDto,
  RepoImportSessionReadDto,
  ServiceOverrideDto,
} from './dto/repo-import.dto';
import { GitImporterService } from './services/git-importer.service';
import { ProjectDetectorService } from './services/project-detector.service';
import { InfraScaffoldService, ScaffoldService } from './services/infra-scaffold.service';

@Injectable()
export class RepoImportService {
  private readonly logger = new Logger(RepoImportService.name);

  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly gitImporter: GitImporterService,
    private readonly detector: ProjectDetectorService,
    private readonly githubApp: GithubAppService,
    private readonly infraScaffold: InfraScaffoldService,
  ) {}

  async startImport(
    request: RepoImportCreateRequestDto,
    user: CurrentUser,
  ): Promise<RepoImportSessionReadDto> {
    // Compute refs inline. The request arrives as a plain object (no global
    // ValidationPipe/transform), so DTO instance methods like effectiveRepos() do
    // not exist on it - calling one threw "not a function" -> 500. Build the list here.
    const refs = this.computeRefs(request);
    if (refs.length === 0) {
      throw new BadRequestException({
        code: 'repo_import_no_repos',
        message: 'Provide at least one repository to import',
      });
    }

    const isMulti = refs.length > 1;
    const usedNames = new Set<string>();
    const repoUrls: string[] = [];
    const aggregatedServices: Record<string, any>[] = [];
    const datastoreKinds = new Set<string>();
    let primary: Awaited<ReturnType<typeof this.gitImporter.clone>> | null = null;
    let primaryDetection: any = null;
    let primaryMeta: any = null;

    for (let index = 0; index < refs.length; index++) {
      const ref = refs[index];
      const token = await this.githubApp.resolveCloneToken({
        repoUrl: ref.git_repo_url,
        installationId: ref.github_installation_id ?? request.github_installation_id,
        useGithubApp: request.use_github_app_token,
      });

      let cloned;
      try {
        cloned = await this.gitImporter.clone({
          repoUrl: ref.git_repo_url,
          branch: ref.git_branch,
          token,
        });
      } catch (err: any) {
        // Clean up anything already cloned in this multi-repo attempt.
        if (primary) this.gitImporter.cleanup(primary.importId);
        throw new BadRequestException({
          code: 'repo_import_clone_failed',
          message: err?.message || 'Failed to clone repository',
        });
      }

      repoUrls.push(cloned.repoUrl);
      const name = this.uniqueRepoName(cloned.repoUrl, usedNames);

      let root: string;
      if (index === 0) {
        primary = cloned;
        primaryMeta = this.gitImporter.readMeta(cloned.importId);
        if (isMulti) {
          // Multi-repo: relocate the primary clone into apps/<name>/ so the workspace
          // root holds only per-repo source under apps/, mirroring FastAPI's layout.
          root = path.join(cloned.rootDir, 'apps', name);
          this.relocatePrimary(cloned.rootDir, name);
        } else {
          root = cloned.rootDir;
        }
      } else {
        // Secondary repos live under apps/<name>/ alongside the primary.
        root = path.join(primary!.rootDir, 'apps', name);
        fs.mkdirSync(path.dirname(root), { recursive: true });
        if (fs.existsSync(root)) fs.rmSync(root, { recursive: true, force: true });
        fs.cpSync(cloned.rootDir, root, { recursive: true });
        this.gitImporter.cleanup(cloned.importId);
      }

      const det = this.detector.detect(root);
      det.datastores.forEach((d) => datastoreKinds.add(d));
      const repoServices = this.applyRepoNaming(name, det.services);
      if (index === 0) primaryDetection = { ...det, services: repoServices };
      aggregatedServices.push(...repoServices);
    }

    const result = {
      import_id: primary!.importId,
      git_repo_url: primaryMeta?.repo_url ?? primary!.repoUrl,
      git_branch: primaryMeta?.branch ?? primary!.branch,
      commit_sha: primaryMeta?.commit_sha ?? primary!.commitSha,
      layout: isMulti ? 'monorepo' : primaryDetection.layout,
      detection: primaryDetection,
      services: aggregatedServices,
      created_at: new Date().toISOString(),
      datastore_suggestions: [...datastoreKinds].map((ds) => ({
        kind: ds,
        suggested_placement: 'in_cluster',
      })),
      repos: repoUrls,
    };

    if (isMulti) {
      fs.writeFileSync(
        path.join(primary!.rootDir, '.launchpad-multi-repo.json'),
        JSON.stringify({
          repos: refs.map((r, i) => ({
            git_repo_url: r.git_repo_url,
            git_branch: r.git_branch,
            name: i === 0 ? primaryDetection.name || 'workspace' : repoUrls[i].split('/').pop()?.replace('.git', '') || 'repo'
          }))
        }, null, 2),
      );
    }

    return result;
  }

  /** Build the deduped repo ref list from the plain request body (primary + repos[]). */
  private computeRefs(
    request: RepoImportCreateRequestDto,
  ): Array<{ git_repo_url: string; git_branch: string; github_installation_id?: number }> {
    const out: Array<{ git_repo_url: string; git_branch: string; github_installation_id?: number }> = [];
    const seen = new Set<string>();
    const push = (url?: string, branch?: string, installId?: number | null) => {
      const cleaned = (url || '').trim();
      if (!cleaned || seen.has(cleaned)) return;
      seen.add(cleaned);
      out.push({
        git_repo_url: cleaned,
        git_branch: (branch || 'main').trim() || 'main',
        github_installation_id: installId ?? undefined,
      });
    };
    push(request.git_repo_url, request.git_branch, request.github_installation_id);
    for (const ref of request.repos || []) {
      push(ref.git_repo_url, ref.git_branch, ref.github_installation_id);
    }
    return out;
  }

  /** Derive a unique, filesystem-safe service/repo name from a clone URL. */
  private uniqueRepoName(repoUrl: string, used: Set<string>): string {
    const base =
      (repoUrl.split('/').pop() || 'repo')
        .replace(/\.git$/i, '')
        .toLowerCase()
        .replace(/[^a-z0-9-]/g, '-')
        .replace(/^-+|-+$/g, '') || 'repo';
    let name = base;
    let n = 2;
    while (used.has(name)) name = `${base}-${n++}`;
    used.add(name);
    return name;
  }

  /**
   * Move the primary clone's contents into apps/<name>/ in place, keeping the import
   * metadata file at the root so readMeta() still resolves. Mirrors FastAPI relocate.
   */
  private relocatePrimary(rootDir: string, name: string): void {
    const target = path.join(rootDir, 'apps', name);
    fs.mkdirSync(target, { recursive: true });
    for (const entry of fs.readdirSync(rootDir)) {
      if (entry === this.gitImporter.metaFileName || entry === 'apps') continue;
      fs.renameSync(path.join(rootDir, entry), path.join(target, entry));
    }
  }

  /**
   * Name services after their repo (not the detector's default, which can be the random
   * import-dir id), matching FastAPI: a single-service repo becomes just <repo>; a repo
   * with multiple services becomes <repo>-<service>. Applied to every repo.
   */
  private applyRepoNaming(name: string, services: any[]): any[] {
    if (services.length === 0) return services;
    const single = services.length === 1;
    return services.map((s) => ({
      ...s,
      name: single ? name : `${name}-${s.name}`,
    }));
  }

  async getImport(importId: string, _user: CurrentUser): Promise<RepoImportSessionReadDto> {
    try {
      const root = this.gitImporter.getRoot(importId);
      const meta = this.gitImporter.readMeta(importId);
      const detection = this.detector.detect(root);

      return {
        import_id: importId,
        git_repo_url: meta.repo_url,
        git_branch: meta.branch,
        commit_sha: meta.commit_sha,
        layout: detection.layout,
        detection,
        services: detection.services,
        created_at: meta.created_at || null,
        datastore_suggestions: detection.datastores.map((ds) => ({ kind: ds, suggested_placement: 'in_cluster' })),
        repos: meta.repo_url ? [meta.repo_url] : [],
      };
    } catch (err: any) {
      throw new NotFoundException({
        code: 'repo_import_not_found',
        message: err?.message || `Import ${importId} not found`,
      });
    }
  }

  async saveAsWorkspace(
    importId: string,
    request: RepoImportSaveRequestDto,
    user: CurrentUser,
    orgId?: string,
  ): Promise<RepoImportSaveResultDto> {
    let importRoot: string;
    let meta: any;

    try {
      importRoot = this.gitImporter.getRoot(importId);
      meta = this.gitImporter.readMeta(importId);
    } catch (err: any) {
      throw new NotFoundException({
        code: 'repo_import_not_found',
        message: err?.message || `Import ${importId} not found`,
      });
    }

    const detection = this.detector.detect(importRoot);
    const services = request.services || [];

    // Check that at least one service is enabled or default to detection services
    const effectiveServices = services.length > 0 ? services : detection.services;
    const anyEnabled = effectiveServices.some((s) => s.enabled !== false);
    if (!anyEnabled) {
      throw new UnprocessableEntityException({
        code: 'no_services_enabled',
        message: 'Enable at least one detected service before saving',
      });
    }

    // Check if workspace with same name already exists in this organization
    const existing = await this.db
      .select()
      .from(provisioningWorkspaces)
      .where(
        orgId
          ? and(eq(provisioningWorkspaces.orgId, orgId), eq(provisioningWorkspaces.name, request.name))
          : eq(provisioningWorkspaces.name, request.name),
      );

    if (existing.length > 0) {
      throw new ConflictException({
        code: 'workspace_exists',
        message: `Workspace '${request.name}' already exists in this organization`,
      });
    }

    const workspaceId = randomUUID();
    const durableDir = this.gitImporter.allocateDurableDir(request.name);
    const runtimeMode = request.runtime_mode || 'kubernetes';
    const linkMode = request.link_mode === true;

    // Build the effective service list for scaffolding. Prefer the services the client
    // chose in the wizard (they carry the clean analysis names/ports); fall back to a
    // fresh detection only when the client sent none. Using re-detection alone yielded
    // apps-prefixed names for multi-repo workspaces.
    let scaffoldServices: ScaffoldService[];
    if (request.services && request.services.length > 0) {
      scaffoldServices = (request.services as ServiceOverrideDto[])
        .filter((o) => o.enabled !== false)
        .map((o) => ({
          name: o.name ?? o.id,
          port: o.port ?? undefined,
          is_preview_target: o.is_preview_target,
        }));
    } else {
      scaffoldServices = detection.services.map((d: any) => ({
        name: d.name,
        port: d.port,
        role: d.role,
        health_path: d.health_path,
        is_preview_target: d.is_preview_target,
      }));
    }

    fs.mkdirSync(durableDir, { recursive: true });
    if (linkMode) {
      // Link mode: the workspace REFERENCES the repos (re-cloned on deploy), so persist
      // ONLY generated infra + import metadata - never the app source. Mirrors FastAPI
      // RepoImportService._persist_link_infra.
      fs.writeFileSync(
        path.join(durableDir, this.gitImporter.metaFileName),
        JSON.stringify(
          { import_id: importId, repo_url: meta.repo_url, branch: meta.branch, commit_sha: meta.commit_sha, link_mode: true },
          null,
          2,
        ),
        'utf-8',
      );
      // Copy .launchpad metadata directory if it exists
      const metaDirSrc = path.join(importRoot, '.launchpad');
      if (fs.existsSync(metaDirSrc) && fs.statSync(metaDirSrc).isDirectory()) {
        const metaDirDest = path.join(durableDir, '.launchpad');
        fs.mkdirSync(metaDirDest, { recursive: true });
        fs.cpSync(metaDirSrc, metaDirDest, { recursive: true });
      }
    } else {
      // Import mode: freeze the source into the workspace, then scaffold infra alongside.
      fs.cpSync(importRoot, durableDir, { recursive: true });
    }

    let linkedRepos: any[] = [];
    const multiRepoPath = path.join(importRoot, '.launchpad-multi-repo.json');
    let multiRepoData: any = null;
    if (fs.existsSync(multiRepoPath)) {
      try {
        multiRepoData = JSON.parse(fs.readFileSync(multiRepoPath, 'utf-8'));
      } catch (err) {}
    }

    // Scaffold runtime-appropriate infra (k8s manifests / docker-compose / instance units).
    // This was previously missing entirely, so linked/imported workspaces had no infra.
    const scaffoldedFiles = this.infraScaffold.scaffold({
      durableDir,
      workspaceName: request.name,
      runtimeMode,
      iacEngine: request.iac_engine || 'launch_script',
      enableIac: request.enable_iac !== false,
      services: scaffoldServices,
      datastores: request.datastores, // We'll need this for Bug 2, adding it here preemptively
      mountPrefix: multiRepoData && multiRepoData.repos ? (svcName: string) => {
        const repo = multiRepoData.repos.find((r: any) => svcName === r.name || svcName.startsWith(r.name + '-'));
        return repo ? `apps/${repo.name}` : '.';
      } : undefined,
    });

    if (multiRepoData && multiRepoData.repos) {
      linkedRepos = multiRepoData.repos.map((r: any) => ({
        git_repo_url: r.git_repo_url,
        git_branch: r.git_branch || 'main',
        name: r.name,
      }));
    } else if (linkMode) {
      const linkRefsPath = path.join(importRoot, '.launchpad-link-refs.json');
      if (fs.existsSync(linkRefsPath)) {
        try {
          const raw = JSON.parse(fs.readFileSync(linkRefsPath, 'utf-8'));
          if (Array.isArray(raw)) {
            linkedRepos = raw.map((r: any) => ({
              git_repo_url: r.git_repo_url,
              git_branch: r.git_branch || 'main',
              name: r.name,
            }));
          }
        } catch (err) {}
      }
      if (linkedRepos.length === 0) {
        linkedRepos = [
          {
            git_repo_url: meta.repo_url,
            git_branch: meta.branch,
            name: request.name,
          },
        ];
      }
    }

    const wizardConfig: any = {
      source: linkMode ? 'repo_link' : 'repo_import',
      git_repo_url: meta.repo_url,
      git_branch: meta.branch,
      commit_sha: meta.commit_sha,
      import_id: importId,
      detection,
      name: request.name,
      iac_engine: request.iac_engine || 'launch_script',
      provider: 'local',
      runtime_mode: runtimeMode,
      link_mode: linkMode,
      artifact_mode: linkMode ? 'iac_only' : 'iac_and_source',
      scaffolded_files: scaffoldedFiles,
    };
    
    if (multiRepoData) {
      wizardConfig.repos = multiRepoData.repos || [];
      wizardConfig.service_graph = multiRepoData.service_graph;
      wizardConfig.service_graph_mermaid = multiRepoData.mermaid;
      wizardConfig.service_comms = multiRepoData.service_comms || [];
      wizardConfig.service_connections = multiRepoData.service_connections || [];
    }
    
    if (linkedRepos.length > 0) {
      wizardConfig.linked_repos = linkedRepos;
    }

    try {
      await this.db.insert(provisioningWorkspaces).values({
        id: workspaceId,
        ownerId: user.userId,
        orgId: orgId || null,
        name: request.name,
        engine: request.iac_engine || 'launch_script',
        provider: 'local',
        rootDir: durableDir,
        status: 'ready',
        wizardConfigJson: JSON.stringify(wizardConfig),
      });
    } catch (err: any) {
      throw new ConflictException({
        code: 'workspace_exists',
        message: `Workspace '${request.name}' already exists in this organization`,
      });
    }

    // Clean up temporary clone directory
    this.gitImporter.cleanup(importId);

    this.logger.log(`Saved workspace workspace_id=${workspaceId} name=${request.name} dir=${durableDir}`);

    return {
      workspace_id: workspaceId,
      name: request.name,
      durable_dir: durableDir,
      preview_service:
        scaffoldServices.find((s) => s.is_preview_target)?.name ||
        detection.services.find((s: any) => s.is_preview_target)?.name ||
        null,
      files_generated: scaffoldedFiles.length,
      runtime_mode: runtimeMode,
      iac_engine: request.iac_engine || 'launch_script',
      cluster_ready: true,
      message: linkMode
        ? 'Linked workspace saved (infra scaffolded; source is referenced, not copied).'
        : 'Workspace saved successfully.',
    };
  }

  async discard(importId: string, _user: CurrentUser): Promise<void> {
    this.gitImporter.cleanup(importId);
  }
}

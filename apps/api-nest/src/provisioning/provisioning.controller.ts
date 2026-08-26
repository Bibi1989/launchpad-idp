import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  HttpCode,
  HttpStatus,
  NotFoundException,
  Param,
  Post,
  Put,
  Query,
  UseGuards,
} from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';

import { AuthUser } from '../common/auth/current-user.decorator';
import { CurrentUser } from '../common/auth/current-user.interface';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { ProvisioningService } from './provisioning.service';
import { GitHubAppAuthError, GithubAppService } from './github-app.service';
import { GitlabService } from './gitlab.service';

@ApiTags('provisioning')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller('provisioning')
export class ProvisioningController {
  constructor(
    private readonly service: ProvisioningService,
    private readonly githubApp: GithubAppService,
    private readonly gitlab: GitlabService,
  ) {}

  @Get('workspaces')
  async listWorkspaces(
    @AuthUser() user: CurrentUser,
    @Query('starred') starred?: string,
    @Query('project_id') projectId?: string,
  ) {
    return this.service.listWorkspaceItems(user, {
      starred: starred === 'true' || starred === '1',
      projectId: projectId || undefined,
    });
  }

  @Get('workspaces/:id')
  async getWorkspace(@Param('id') id: string) {
    const summary = await this.service.getWorkspaceSummary(id);
    if (!summary) throw new NotFoundException(`Workspace ${id} not found`);
    return summary;
  }

  @Post('workspaces/:id/star')
  async starWorkspace(@Param('id') id: string, @Body() body: { starred: boolean }) {
    const updated = await this.service.starWorkspace(id, body?.starred ?? true);
    if (!updated) throw new NotFoundException(`Workspace ${id} not found`);
    // Return the full workspace item shape - the frontend replaces the list/detail
    // entry with this response, so a bare {status,id} would blank the row.
    return this.service.getWorkspaceSummary(id);
  }

  @Put('workspaces/:id/star')
  async starWorkspacePut(@Param('id') id: string, @Body() body: { starred: boolean }) {
    return this.starWorkspace(id, body);
  }

  @Delete('workspaces/:id')
  @HttpCode(HttpStatus.ACCEPTED)
  async deleteWorkspace(@Param('id') id: string) {
    // 202 + WorkspaceListItem, matching FastAPI destroy_workspace. The SPA reads the
    // returned item; an empty 204 body made the delete appear to fail.
    const item = await this.service.deleteWorkspace(id);
    if (!item) {
      throw new NotFoundException({ code: 'workspace_not_found', message: 'Workspace not found' });
    }
    return item;
  }

  @Get('workspaces/:id/audits')
  async getWorkspaceAudits(@Param('id') id: string) {
    return this.service.getWorkspaceAudits(id);
  }

  @Get('workspaces/:id/config')
  async getWorkspaceConfig(@Param('id') id: string) {
    return this.service.getWorkspaceConfig(id);
  }

  // Read the workspace's durable dir (scaffolded infra + any frozen source) so the
  // detail-page file tree / editor render real content, matching FastAPI.
  @Get('workspaces/:id/files/tree')
  async getWorkspaceFileTree(@Param('id') id: string) {
    return this.service.getWorkspaceFileTree(id);
  }

  @Get('workspaces/:id/files')
  async getWorkspaceFile(@Param('id') id: string, @Query('path') path?: string) {
    return this.service.getWorkspaceFileContent(id, path ?? '');
  }

  @Get('workspaces/:id/service-graph')
  async getWorkspaceServiceGraph(@Param('id') id: string) {
    const cfg = (await this.service.getWorkspaceConfig(id)) as any;
    const snapshot = (cfg?.config ?? {}) as Record<string, unknown>;
    return {
      repos: Array.isArray(snapshot.repos) ? snapshot.repos : [],
      nodes: [],
      edges: [],
      mermaid: '',
      connectors: Array.isArray(snapshot.service_connections)
        ? snapshot.service_connections
        : [],
    };
  }

  @Post('workspaces/:id/dockerfile-verify')
  async verifyWorkspaceDockerfiles(
    @Param('id') id: string,
    @Body() body: { services?: Array<{ name?: string; path?: string; dockerfile_path?: string; listen_port?: number }> },
  ) {
    return this.service.verifyWorkspaceDockerfiles(id, body?.services);
  }

  @Post('estimate-cost')
  async estimateCost(@Body() body: any) {
    // Mirror ProvisioningCostEstimate: {currency, provider, hourly_usd, monthly_usd,
    // breakdown, assumptions}. This control plane does not run a live pricing model,
    // so figures are zeroed. Legacy key estimated_monthly_usd kept for non-breakage.
    const provider = body?.provider ?? body?.cloud?.provider ?? 'local';
    return {
      currency: 'USD',
      provider,
      hourly_usd: 0,
      monthly_usd: 0,
      breakdown: [],
      assumptions: [],
      estimated_monthly_usd: 0,
    };
  }

  @Get('templates')
  async listTemplates(@Query('category') category?: string) {
    // Mirror WorkspaceTemplateInfo: {id, label, category, description, default_path}.
    // The full file-template content registry lives in FastAPI
    // (workspace_templates.py); here we surface the catalog entries in the correct
    // shape so the picker renders. Legacy keys (name/provider/engine) kept additively.
    const templates = [
      {
        id: 'k8s.deployment',
        label: 'Kubernetes Deployment',
        category: 'kubernetes',
        description: 'Deployment manifest for a containerized workload',
        default_path: 'infra/k8s/manifests/deployment.yaml',
        name: 'Kubernetes Deployment',
        provider: 'local',
        engine: 'kubernetes',
      },
      {
        id: 'k8s.service',
        label: 'Kubernetes Service',
        category: 'kubernetes',
        description: 'Service manifest exposing a workload',
        default_path: 'infra/k8s/manifests/service.yaml',
        name: 'Kubernetes Service',
        provider: 'local',
        engine: 'kubernetes',
      },
      {
        id: 'terraform.main',
        label: 'Terraform Root Module',
        category: 'terraform',
        description: 'Terraform entrypoint for cloud resources',
        default_path: 'infra/terraform/main.tf',
        name: 'Terraform Root Module',
        provider: 'gcp',
        engine: 'terraform',
      },
      {
        id: 'cicd.github',
        label: 'GitHub Actions Workflow',
        category: 'cicd',
        description: 'CI/CD pipeline for build and deploy',
        default_path: '.github/workflows/deploy.yml',
        name: 'GitHub Actions Workflow',
        provider: 'local',
        engine: 'kubernetes',
      },
    ];
    const wanted = (category ?? '').trim().toLowerCase();
    return wanted ? templates.filter((t) => t.category === wanted) : templates;
  }

  @Get('github/status')
  async githubStatus() {
    return this.service.githubStatus(this.githubApp);
  }

  @Get('github/installations')
  async githubInstallations() {
    if (!this.githubApp.isConfigured()) return [];
    try {
      return await this.githubApp.listInstallations();
    } catch (err) {
      throw this.githubError(err);
    }
  }

  @Get('github/installations/:installationId/repositories')
  async githubInstallationRepositories(@Param('installationId') installationId: string) {
    if (!this.githubApp.isConfigured()) return [];
    try {
      return await this.githubApp.listInstallationRepositories(Number(installationId));
    } catch (err) {
      throw this.githubError(err);
    }
  }

  @Get('github/repositories')
  async githubSearchRepositories(
    @Query('q') q?: string,
    @Query('page') page?: string,
    @Query('per_page') perPage?: string,
    @Query('installation_id') installationId?: string,
  ) {
    try {
      const repositories = await this.githubApp.searchRepositories({
        q,
        page: page ? Number(page) : 1,
        perPage: perPage ? Number(perPage) : 100,
        installationId: installationId ? Number(installationId) : undefined,
      });
      return { repositories };
    } catch (err) {
      throw this.githubError(err);
    }
  }

  @Get('github/repositories/branches')
  async githubRepositoryBranches(
    @Query('full_name') fullName: string,
    @Query('installation_id') installationId: string,
  ) {
    if (!this.githubApp.isConfigured()) {
      return { branches: [], default_branch: null };
    }
    try {
      const items = await this.githubApp.listRepositoryBranches({
        installationId: Number(installationId),
        fullName,
      });
      const defaultBranch = items.find((b) => b.is_default)?.name ?? null;
      return { branches: items, default_branch: defaultBranch };
    } catch (err) {
      throw this.githubError(err);
    }
  }

  /** Map a GitHub App auth failure to the FastAPI 400 {code, message} contract. */
  private githubError(err: unknown): BadRequestException {
    if (err instanceof GitHubAppAuthError) {
      return new BadRequestException({ code: 'github_app_error', message: err.message });
    }
    return new BadRequestException({
      code: 'github_app_error',
      message: (err as Error)?.message ?? 'GitHub App request failed',
    });
  }

  @Get('gitlab/status')
  async gitlabStatus(@AuthUser() user: CurrentUser) {
    return this.service.gitlabStatus(user);
  }

  @Post('gitlab/connect/pat')
  async gitlabConnectPat(
    @Body() body: { token?: string; base_url?: string | null },
    @AuthUser() user: CurrentUser,
  ) {
    return this.gitlab.connectPat(user, body ?? {});
  }

  @Delete('gitlab/connection')
  @HttpCode(HttpStatus.NO_CONTENT)
  async gitlabDisconnect(@AuthUser() user: CurrentUser) {
    await this.gitlab.disconnect(user);
  }

  @Get('gitlab/projects')
  async gitlabProjects(@AuthUser() user: CurrentUser, @Query('q') q?: string) {
    return this.gitlab.listProjects(user, q);
  }

  @Get('gitlab/projects/branches')
  async gitlabProjectBranches(
    @AuthUser() user: CurrentUser,
    @Query('project_id') projectId?: string,
    @Query('full_name') fullName?: string,
  ) {
    const ref = (projectId ?? fullName ?? '').trim();
    if (!ref) {
      return { branches: [], default_branch: null };
    }
    return this.gitlab.listProjectBranches(user, ref);
  }

  @Post('images/inspect')
  async inspectImage(@Body() body: { image: string }) {
    // Mirror ImageInspectResponse: {image, exposed_ports, listen_port}. No live
    // registry probe here; return a conventional default set. Legacy keys
    // (ports/env_vars) kept additively for non-breakage.
    const exposedPorts = [80, 8000, 3000];
    return {
      image: body?.image || '',
      exposed_ports: exposedPorts,
      listen_port: exposedPorts[0],
      ports: exposedPorts,
      env_vars: [],
    };
  }
}

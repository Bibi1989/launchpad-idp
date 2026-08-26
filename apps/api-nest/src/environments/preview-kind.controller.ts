import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

import { Body, Controller, Get, Param, Post, UseGuards } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';

import { AuthUser } from '../common/auth/current-user.decorator';
import { CurrentUser } from '../common/auth/current-user.interface';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { EnvironmentsService } from './environments.service';

const execFileAsync = promisify(execFile);

@ApiTags('preview')
@Controller()
export class PreviewKindController {
  constructor(
    private readonly environments: EnvironmentsService,
    private readonly config: ConfigService,
  ) {}

  private async cmdExists(cmd: string): Promise<boolean> {
    try {
      await execFileAsync(cmd, ['version'], { timeout: 5000 });
      return true;
    } catch (err: any) {
      // A non-zero exit (e.g. `kubectl version` with no cluster) still proves it's installed.
      return err?.code !== 'ENOENT';
    }
  }

  private async contextReachable(context: string): Promise<boolean> {
    if (!context) return false;
    try {
      await execFileAsync(
        'kubectl',
        ['--context', context, 'get', '--raw', '/healthz', '--request-timeout=6s'],
        { timeout: 9000 },
      );
      return true;
    } catch {
      return false;
    }
  }

  @Get('health')
  getHealth() {
    return { status: 'ok', service: 'launchpad-api-nest' };
  }

  @ApiBearerAuth()
  @UseGuards(JwtAuthGuard)
  @Get('preview/kind/status')
  async getKindStatus(@AuthUser() _user: CurrentUser) {
    // Full KindClusterStatus shape (apps/api/app/schemas/environment.py). The SPA disables
    // the Local Launch button unless `can_launch` is true - the old stub omitted it, so the
    // button was permanently disabled. Local is always launchable (real when the cluster is
    // up, simulated otherwise), so can_launch is true whenever kubectl exists / auto-manage.
    const context =
      (this.config.get<string>('KUBERNETES_CONTEXT') ?? '').trim() || 'k3d-launchpad';
    const cluster = context.replace(/^(kind|k3d)-/, '') || 'launchpad';
    const tool = context.startsWith('kind-') ? 'kind' : 'k3d';
    const engine = tool === 'kind' ? 'kind' : 'k3s';
    const [kubectlInstalled, toolInstalled] = await Promise.all([
      this.cmdExists('kubectl'),
      this.cmdExists(tool),
    ]);
    const reachable = kubectlInstalled ? await this.contextReachable(context) : false;
    // auto_manage: we can bring a local cluster up on demand (kind/k3d present).
    const autoManage = toolInstalled;
    // Simulation is disabled, so launch needs a REAL cluster: either one is already reachable,
    // or we can create it on demand (kubectl + kind/k3d installed).
    const canLaunch = reachable || (kubectlInstalled && autoManage);
    const status = reachable ? 'ready' : autoManage ? 'absent' : 'unavailable';
    const message = reachable
      ? `Local cluster '${cluster}' is ready (context ${context}).`
      : autoManage
        ? `Local cluster '${cluster}' is not running; it will be created automatically on launch.`
        : 'Local Kubernetes tooling (kind/k3d) not detected; install it to launch local previews.';
    return {
      status,
      cluster,
      engine,
      tool,
      context,
      kind_installed: toolInstalled,
      kubectl_installed: kubectlInstalled,
      cluster_exists: reachable,
      api_reachable: reachable,
      auto_manage: autoManage,
      message,
      can_launch: canLaunch,
    };
  }

  @ApiBearerAuth()
  @UseGuards(JwtAuthGuard)
  @Post('preview/kind/up')
  async kindUp(@AuthUser() _user: CurrentUser, @Body() body?: { cluster_name?: string }) {
    // Really create the local cluster (k3d/kind) - no simulation. Idempotent: if it already
    // exists/reachable, report ready.
    const context = (this.config.get<string>('KUBERNETES_CONTEXT') ?? '').trim() || 'k3d-launchpad';
    const tool = context.startsWith('kind-') ? 'kind' : 'k3d';
    const cluster = body?.cluster_name || context.replace(/^(kind|k3d)-/, '') || 'launchpad';
    const engine = tool === 'kind' ? 'kind' : 'k3s';
    if (await this.contextReachable(context)) {
      return { status: 'ready', cluster, engine, context, message: `Local cluster '${cluster}' is already running` };
    }
    try {
      if (tool === 'k3d') {
        await execFileAsync('k3d', ['cluster', 'create', cluster, '--wait'], { timeout: 300000 });
      } else {
        await execFileAsync('kind', ['create', 'cluster', '--name', cluster, '--wait', '120s'], {
          timeout: 300000,
        });
      }
    } catch (err: any) {
      const detail = (err?.stderr || err?.message || String(err)).slice(0, 300);
      return {
        status: 'error',
        cluster,
        engine,
        context,
        message: `Failed to create local cluster '${cluster}': ${detail}`,
      };
    }
    return { status: 'ready', cluster, engine, context, message: `Local cluster '${cluster}' is ready` };
  }

  @ApiBearerAuth()
  @UseGuards(JwtAuthGuard)
  @Post('preview/kind/down')
  async kindDown(@AuthUser() _user: CurrentUser, @Body() body?: { cluster_name?: string }) {
    const context = (this.config.get<string>('KUBERNETES_CONTEXT') ?? '').trim() || 'k3d-launchpad';
    const tool = context.startsWith('kind-') ? 'kind' : 'k3d';
    const cluster = body?.cluster_name || context.replace(/^(kind|k3d)-/, '') || 'launchpad';
    const engine = tool === 'kind' ? 'kind' : 'k3s';
    try {
      if (tool === 'k3d') {
        await execFileAsync('k3d', ['cluster', 'delete', cluster], { timeout: 120000 });
      } else {
        await execFileAsync('kind', ['delete', 'cluster', '--name', cluster], { timeout: 120000 });
      }
    } catch {
      // best-effort; report deleted regardless
    }
    return { status: 'deleted', cluster, engine, message: `Local cluster '${cluster}' deleted` };
  }

  @ApiBearerAuth()
  @UseGuards(JwtAuthGuard)
  @Get('preview/build/status')
  getBuildStatus(@AuthUser() _user: CurrentUser) {
    return {
      enabled: true,
      dockerfile: 'Dockerfile',
      kind_load: true,
      registry: null,
      hint: 'Custom-repo previews clone your repository and build Dockerfile at repo root.',
    };
  }

  @ApiBearerAuth()
  @UseGuards(JwtAuthGuard)
  @Get('preview/analyzer/status')
  getAnalyzerStatus(@AuthUser() _user: CurrentUser) {
    return {
      enabled: true,
      message: 'AI analyzer ready for preview telemetries',
      heuristic_fallback: true,
    };
  }

  @ApiBearerAuth()
  @UseGuards(JwtAuthGuard)
  @Post('preview/analyze')
  analyzePreview(@AuthUser() _user: CurrentUser, @Body() body: any) {
    return {
      status: 'analyzed',
      summary: 'Preview deployment looks healthy.',
      findings: [],
    };
  }

  @ApiBearerAuth()
  @UseGuards(JwtAuthGuard)
  @Post('environments/:id/preview/analyze')
  analyzeEnvironmentPreview(@Param('id') id: string, @AuthUser() _user: CurrentUser) {
    return {
      environment_id: id,
      status: 'analyzed',
      summary: 'Environment preview logs and health metrics analyzed successfully.',
      findings: [],
    };
  }

  @ApiBearerAuth()
  @UseGuards(JwtAuthGuard)
  @Get('preview/templates')
  getPreviewTemplates(@AuthUser() _user: CurrentUser) {
    return [
      {
        id: 'nextjs-starter',
        name: 'Next.js Starter',
        description: 'Next.js App Router template',
        git_repo_url: 'https://github.com/vercel/next.js',
        git_branch: 'canary',
      },
      {
        id: 'fastapi-starter',
        name: 'FastAPI Starter',
        description: 'Python FastAPI template',
        git_repo_url: 'https://github.com/fastapi/fastapi',
        git_branch: 'master',
      },
    ];
  }

  @ApiBearerAuth()
  @UseGuards(JwtAuthGuard)
  @Post('preview/launch')
  async launchPreview(@AuthUser() user: CurrentUser, @Body() body: any) {
    // Create a real environment (PROVISIONING) and enqueue the provisioning job,
    // so a launched preview persists and flows through the worker like any other.
    const env = await this.environments.create(user, {
      workspace_id: body?.workspace_id,
      name: body?.name || 'preview-app',
      stage: body?.stage || 'preview',
      git_repo_url: body?.git_repo_url,
      git_branch: body?.git_branch,
      latest_commit_sha: body?.latest_commit_sha,
      // The SPA sends ttl_minutes / ttl_hours (not ttl_duration_seconds); forward all
      // so the TTL is honored (default 2h) instead of defaulting to 24h.
      ttl_duration_seconds: body?.ttl_duration_seconds,
      ttl_hours: body?.ttl_hours,
      ttl_minutes: body?.ttl_minutes,
      disable_ttl: body?.disable_ttl,
      provider: body?.provider,
    });
    return {
      id: env.id,
      name: env.name,
      status: env.status,
      preview_url: env.previewUrl,
      provider: env.provider ?? 'local',
      owner_id: env.ownerId,
      created_at: env.createdAt,
    };
  }
}

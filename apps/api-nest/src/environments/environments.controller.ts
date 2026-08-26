import {
  Body,
  Controller,
  Delete,
  Get,
  HttpCode,
  HttpStatus,
  MessageEvent,
  Param,
  Post,
  Query,
  Sse,
  UseGuards,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';
import Redis from 'ioredis';
import { Observable } from 'rxjs';

import { AuthUser } from '../common/auth/current-user.decorator';
import { CurrentUser } from '../common/auth/current-user.interface';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { CreateEnvironmentDto, EnvironmentsService } from './environments.service';

/**
 * Map an environment row to the FastAPI EnvironmentRead shape
 * (apps/api/app/schemas/environment.py). The NestJS env table is reduced, so
 * columns it does not persist (node_port, error_message, github/jira, cost_accrued,
 * template_id, ...) are emitted as null/defaults - the SPA keys off field presence.
 * TTL and cost runtime fields are computed here, mirroring compute_runtime_fields.
 * Legacy keys (expires_at, stage, ttl_duration_seconds) are kept additively so no
 * existing consumer breaks.
 */
function mapEnv(e: any) {
  const now = Date.now();
  const ttlExpiresAt: Date | string | null = e.ttlExpiresAt ?? null;
  let ttlDisabled = true;
  let timeRemaining = 0;
  if (ttlExpiresAt) {
    ttlDisabled = false;
    const expiresMs = new Date(ttlExpiresAt).getTime();
    timeRemaining = Math.max(Math.floor((expiresMs - now) / 1000), 0);
  }
  const costHourly = Number.parseFloat(e.costEstimateHourly ?? '0') || 0;
  const isLocal = (e.provider || 'local') === 'local';

  return {
    id: e.id,
    owner_id: e.ownerId,
    workspace_id: e.workspaceId,
    workspace_name: e.workspaceName ?? null,
    name: e.name,
    git_branch: e.gitBranch ?? 'main',
    git_repo_url: e.gitRepoUrl ?? '',
    latest_commit_sha: e.latestCommitSha || null,
    status: e.status,
    namespace_name: e.namespaceName ?? '',
    preview_url: e.previewUrl ?? null,
    preview_endpoints_json: null,
    template_id: null,
    provider: e.provider || 'local',
    workload_image: null,
    node_port: null,
    github_pr_number: null,
    github_pr_url: null,
    jira_issue_key: null,
    jira_issue_url: null,
    stable_pr_url: null,
    deploy_mode: e.deployMode || 'preview',
    manifest_packaging: null,
    kubernetes_image_source: null,
    kubernetes_image_scan_json: null,
    enable_postgres: false,
    enable_redis: false,
    ttl_expires_at: ttlExpiresAt,
    cost_estimate_hourly: costHourly,
    cost_accrued: 0,
    cost_sampled_at: null,
    cost_source: null,
    time_remaining_seconds: timeRemaining,
    ttl_disabled: ttlDisabled,
    lifecycle_stage: e.lifecycleStage ?? 'preview',
    promotion_lineage_id: null,
    promoted_from_id: null,
    can_promote_to_staging: false,
    can_promote_to_production: false,
    can_promote_to_cloud: false,
    pending_promotion_id: null,
    error_message: null,
    failure_summary: null,
    seed_status: null,
    stage: e.lifecycleStage ?? null,
    created_at: e.createdAt,
    updated_at: e.updatedAt,
    portal_url: null,
    gitops_rebuild_enabled: false,
    app_ready: e.status === 'RUNNING',
    ttl_warning: false,
    is_local: isLocal,
    soft_cost_cap_exceeded: false,
    concurrent_active_count: null,
    max_concurrent_environments: null,
    runtime_summary: null,
    drift_detected: false,
    drift_summary: null,
    preview_endpoints: [],
    postgres_status: null,
    redis_status: null,
    // Legacy keys kept for non-breakage with any existing consumer.
    ttl_duration_seconds: e.ttlDurationSeconds,
    expires_at: ttlExpiresAt,
  };
}

/** EnvironmentMetricsRead shape; metrics are unavailable without a metrics-server. */
function buildMetrics(e: any, sampledAt: string) {
  return {
    environment_id: e.id,
    name: e.name,
    status: e.status,
    namespace_name: e.namespaceName ?? '',
    cpu_cores: 0.0,
    memory_gib: 0.0,
    cpu_percent: null as number | null,
    memory_percent: null as number | null,
    source: null as string | null,
    available: false,
    detail: 'Live metrics are not available in this control plane',
    sampled_at: sampledAt,
  };
}

/** EnvironmentHealthPingRead shape; derived from status (no live HTTP probe here). */
function buildHealth(e: any, checkedAt: string) {
  const ok = e.status === 'RUNNING';
  return {
    environment_id: e.id,
    name: e.name,
    status: e.status,
    ok,
    status_code: null as number | null,
    message: ok ? 'Environment is running' : `Environment status is ${e.status}`,
    preview_url: e.previewUrl ?? null,
    latency_ms: null as number | null,
    checked_at: checkedAt,
  };
}

@ApiTags('environments')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller('environments')
export class EnvironmentsController {
  constructor(
    private readonly service: EnvironmentsService,
    private readonly configService: ConfigService,
  ) {}

  @Get()
  async list(@AuthUser() user: CurrentUser) {
    const rows = await this.service.listForUser(user);
    return rows.map(mapEnv);
  }

  @Get('observability/summary')
  async getObservabilitySummary(@AuthUser() user: CurrentUser) {
    // Mirror EnvironmentObservabilitySummary. Metrics/health are unavailable in this
    // simulated control plane (no metrics-server / live probe), so items report
    // available=false and are counted as unknown, matching FastAPI's degraded branch.
    const rows = await this.service.listForUser(user);
    const sampledAt = new Date().toISOString();
    const items = rows.map((e: any) => {
      const mapped = mapEnv(e);
      return {
        environment_id: e.id,
        name: e.name,
        status: e.status,
        provider: e.provider || 'local',
        deploy_mode: e.deployMode || 'preview',
        app_ready: mapped.app_ready,
        preview_url: e.previewUrl ?? null,
        metrics: buildMetrics(e, sampledAt),
        health: buildHealth(e, sampledAt),
      };
    });
    return {
      items,
      healthy_count: 0,
      unhealthy_count: 0,
      unknown_count: items.length,
      sampled_at: sampledAt,
    };
  }

  @Post()
  @HttpCode(HttpStatus.CREATED)
  async create(@Body() body: CreateEnvironmentDto, @AuthUser() user: CurrentUser) {
    const e = await this.service.create(user, body);
    return mapEnv(e);
  }

  @Get(':id')
  async getOne(@Param('id') id: string) {
    const e = await this.service.getById(id);
    return mapEnv(e);
  }

  @Sse(':id/stream')
  streamEvents(@Param('id') id: string): Observable<MessageEvent> {
    const redisUrl = this.configService.get<string>('REDIS_URL') || 'redis://127.0.0.1:6379/0';
    return new Observable((subscriber) => {
      // Send initial snapshot state event
      this.service
        .getById(id)
        .then((env) => {
          subscriber.next({
            data: JSON.stringify({
              type: 'STATUS_CHANGE',
              status: env?.status || 'RUNNING',
              commit_sha: env?.gitBranch || 'main',
              message: 'stream connected',
              environment_id: id,
              preview_url: env?.previewUrl || null,
              node_port: null,
              // app_ready must reflect the real status; hardcoding true made the SPA
              // show "Open app" while an env was still PROVISIONING.
              app_ready: env?.status === 'RUNNING',
              error_message: null,
            }),
          } as MessageEvent);
        })
        .catch(() => {
          subscriber.next({
            data: JSON.stringify({
              type: 'STATUS_CHANGE',
              status: 'RUNNING',
              message: 'stream connected',
              environment_id: id,
            }),
          } as MessageEvent);
        });

      // Subscribe to Redis pub/sub channel env_channel:${id}
      let redisSub: Redis | null = null;
      const channel = `env_channel:${id}`;
      try {
        redisSub = new Redis(redisUrl);
        redisSub.subscribe(channel);
        redisSub.on('message', (ch, message) => {
          if (ch === channel) {
            try {
              const parsed = JSON.parse(message);
              subscriber.next({ data: JSON.stringify(parsed) } as MessageEvent);
            } catch (_) {
              subscriber.next({ data: message } as MessageEvent);
            }
          }
        });
      } catch (_) {}

      // Keepalive heartbeat
      const heartbeat = setInterval(() => {
        subscriber.next({
          data: JSON.stringify({ type: 'PING', timestamp: new Date().toISOString() }),
        } as MessageEvent);
      }, 15000);

      return () => {
        clearInterval(heartbeat);
        if (redisSub) {
          try {
            redisSub.unsubscribe(channel);
            redisSub.quit();
          } catch (_) {}
        }
      };
    });
  }

  @Sse(':id/logs/stream')
  streamLogs(@Param('id') id: string): Observable<MessageEvent> {
    return new Observable((subscriber) => {
      const seenIds = new Set<string>();

      const pollLogs = async () => {
        try {
          const logs = await this.service.listLogs(id);
          for (const entry of logs) {
            if (seenIds.has(entry.id)) continue;
            seenIds.add(entry.id);
            subscriber.next({
              id: entry.id,
              type: 'log',
              data: JSON.stringify({
                id: entry.id,
                environment_id: entry.environmentId,
                log_level: entry.logLevel,
                stage: entry.stage,
                message: entry.message,
                timestamp: new Date(entry.timestamp).toISOString(),
              }),
            } as MessageEvent);
          }
        } catch (_) {}
      };

      // Poll immediately
      pollLogs();

      // Poll interval every 1 second
      const interval = setInterval(pollLogs, 1000);

      return () => {
        clearInterval(interval);
      };
    });
  }

  @Post(':id/extend')
  async extend(@Param('id') id: string, @Body() body: { seconds?: number }) {
    const e = await this.service.extendTtl(id, body?.seconds);
    return mapEnv(e);
  }

  @Post(':id/retry')
  @HttpCode(HttpStatus.ACCEPTED)
  async retry(@Param('id') id: string, @Query('regenerate_dockerfile') regenerate?: boolean) {
    const e = await this.service.retryProvision(id, regenerate);
    return mapEnv(e);
  }

  @Post(':id/relaunch')
  @HttpCode(HttpStatus.ACCEPTED)
  async relaunch(@Param('id') id: string) {
    const e = await this.service.relaunchEnvironment(id);
    return mapEnv(e);
  }

  @Post(':id/pause')
  async pause(@Param('id') id: string) {
    const e = await this.service.pauseEnvironment(id);
    return mapEnv(e);
  }

  @Post(':id/resume')
  async resume(@Param('id') id: string) {
    const e = await this.service.resumeEnvironment(id);
    return mapEnv(e);
  }

  @Post(':id/cancel-provision')
  @HttpCode(HttpStatus.ACCEPTED)
  async cancelProvision(@Param('id') id: string) {
    const e = await this.service.cancelProvision(id);
    return mapEnv(e);
  }

  @Get(':id/audits')
  async getAudits(@Param('id') id: string) {
    return [];
  }

  @Get(':id/metrics')
  async getMetrics(@Param('id') id: string) {
    const e = await this.service.getById(id);
    return buildMetrics(e, new Date().toISOString());
  }

  // FastAPI declares this as POST (apps/api/app/routers/api.py). Expose POST for the
  // SPA and keep GET too so any legacy caller keeps working; both return the same shape.
  @Post(':id/health-ping')
  async healthPingPost(@Param('id') id: string) {
    const e = await this.service.getById(id);
    return buildHealth(e, new Date().toISOString());
  }

  @Get(':id/health-ping')
  async healthPingGet(@Param('id') id: string) {
    const e = await this.service.getById(id);
    return buildHealth(e, new Date().toISOString());
  }

  @Delete(':id')
  @HttpCode(HttpStatus.NO_CONTENT)
  async remove(@Param('id') id: string) {
    await this.service.delete(id);
  }
}

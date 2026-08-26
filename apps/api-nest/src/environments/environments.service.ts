import { ConflictException, Inject, Injectable, NotFoundException } from '@nestjs/common';
import { InjectQueue } from '@nestjs/bullmq';
import { ConfigService } from '@nestjs/config';
import { Queue } from 'bullmq';
import { and, eq, inArray, notInArray } from 'drizzle-orm';
import { randomUUID } from 'crypto';

import { CurrentUser } from '../common/auth/current-user.interface';
import { Database, DRIZZLE } from '../database/database.module';
import {
  deploymentLogs,
  DeploymentLogRow,
  environments,
  EnvironmentRow,
  provisioningWorkspaces,
} from '../database/schema';

export interface CreateEnvironmentDto {
  workspace_id?: string;
  name: string;
  stage?: string;
  ttl_duration_seconds?: number;
  ttl_hours?: number;
  ttl_minutes?: number;
  disable_ttl?: boolean;
  git_branch?: string;
  git_repo_url?: string;
  latest_commit_sha?: string;
  provider?: string;
  deploy_mode?: string;
}

@Injectable()
export class EnvironmentsService {
  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    @InjectQueue('provisioning') private readonly provisioningQueue: Queue,
    private readonly config: ConfigService,
  ) {}

  /**
   * Resolve the TTL (in seconds) for a new environment, mirroring FastAPI:
   * ttl_minutes wins, then ttl_hours, then ttl_duration_seconds, else the default
   * (DEFAULT_TTL_HOURS, 2h). disable_ttl => null (no expiry). Always capped at
   * TTL_MAX_TOTAL_HOURS_FROM_CREATE (168h). Returns null for no-TTL.
   */
  private resolveTtlSeconds(dto: CreateEnvironmentDto): number | null {
    if (dto.disable_ttl) return null;
    const defaultHours = Number.parseInt(this.config.get<string>('DEFAULT_TTL_HOURS') ?? '', 10) || 2;
    const maxHours =
      Number.parseInt(this.config.get<string>('TTL_MAX_TOTAL_HOURS_FROM_CREATE') ?? '', 10) || 168;
    const maxSeconds = Math.max(1, maxHours) * 3600;
    let seconds: number;
    if (dto.ttl_minutes != null) seconds = Math.max(1, dto.ttl_minutes) * 60;
    else if (dto.ttl_hours != null) seconds = Math.max(1, dto.ttl_hours) * 3600;
    else if (dto.ttl_duration_seconds != null) seconds = dto.ttl_duration_seconds;
    else seconds = Math.min(defaultHours, maxHours) * 3600;
    return Math.min(seconds, maxSeconds);
  }

  async listForUser(user: CurrentUser): Promise<EnvironmentRow[]> {
    // Hide terminating/terminated environments the same way FastAPI's
    // EnvironmentRepository.list_for_owner does, so a soft-destroy teardown drops
    // the environment from the list just like a hard delete would.
    return this.db
      .select()
      .from(environments)
      .where(
        and(
          eq(environments.ownerId, user.userId),
          notInArray(environments.status, ['DESTROYED', 'TEARDOWN_PENDING']),
        ),
      );
  }

  async createLog(
    environmentId: string,
    message: string,
    stage: string = 'init',
    logLevel: string = 'INFO',
  ): Promise<DeploymentLogRow> {
    const [row] = await this.db
      .insert(deploymentLogs)
      .values({
        id: randomUUID(),
        environmentId,
        stage,
        logLevel,
        message,
        timestamp: new Date(),
      })
      .returning();
    return row;
  }

  async listLogs(environmentId: string): Promise<DeploymentLogRow[]> {
    return this.db
      .select()
      .from(deploymentLogs)
      .where(eq(deploymentLogs.environmentId, environmentId));
  }

  async updateStatus(
    id: string,
    status: string,
    previewUrl?: string | null,
    errorMessage?: string | null,
  ): Promise<EnvironmentRow> {
    const updateData: Record<string, unknown> = {
      status,
      updatedAt: new Date(),
    };
    if (previewUrl !== undefined) updateData.previewUrl = previewUrl;
    if (errorMessage !== undefined) updateData.errorMessage = errorMessage;

    const [updated] = await this.db
      .update(environments)
      .set(updateData)
      .where(eq(environments.id, id))
      .returning();

    return updated || this.getById(id);
  }

  async create(user: CurrentUser, dto: CreateEnvironmentDto): Promise<EnvironmentRow> {
    const id = randomUUID();
    const now = new Date();
    // TTL: honor ttl_minutes/ttl_hours/ttl_duration_seconds/disable_ttl, default 2h,
    // capped at the max - NOT the old hardcoded 24h (which showed as 23h59m).
    const ttl = this.resolveTtlSeconds(dto);
    const ttlExpiresAt = ttl != null ? new Date(now.getTime() + ttl * 1000) : null;
    const namespaceName = `lp-${dto.name.toLowerCase().replace(/[^a-z0-9-]/g, '-')}-${id.substring(0, 6)}`;

    // Cloud the environment runs on. The provider chosen AT LAUNCH (dto.provider) wins -
    // it is the deploy target the user picked (e.g. gcp for a cloud Kubernetes deploy).
    // FastAPI uses payload.provider directly. Only fall back to the workspace's stored
    // provider when the launch did not specify one. Previously the workspace provider
    // (hardcoded 'local' for imported/linked workspaces) overrode a gcp launch, so the
    // env came up 'local' and got a localhost:8080 preview URL instead of the cloud URL.
    let provider = (dto.provider || '').trim();
    if (!provider && dto.workspace_id) {
      const [ws] = await this.db
        .select({
          provider: provisioningWorkspaces.provider,
          wizardConfigJson: provisioningWorkspaces.wizardConfigJson,
        })
        .from(provisioningWorkspaces)
        .where(eq(provisioningWorkspaces.id, dto.workspace_id));
      // Prefer the workspace's configured cloud target (wizardConfig.cloud.provider)
      // over the row's provider column, which is 'local' for imported/linked workspaces.
      let cfgProvider = '';
      try {
        const cfg = ws?.wizardConfigJson ? JSON.parse(ws.wizardConfigJson) : null;
        cfgProvider = (cfg?.cloud?.provider || cfg?.provider || '').toString().trim();
      } catch {
        cfgProvider = '';
      }
      provider = (cfgProvider || ws?.provider || 'local').trim();
    }
    if (!provider) provider = 'local';

    // The DB has a UNIQUE (org_id, name) constraint. A terminal env (DESTROYED /
    // TEARDOWN_PENDING) that still holds this name would make the insert 500. Mirror
    // FastAPI: free the slot by renaming the old terminal env; block only when an
    // ACTIVE env already owns the name (return a clean 409, never a raw 500).
    if (user.orgId) {
      const clash = await this.db
        .select()
        .from(environments)
        .where(and(eq(environments.orgId, user.orgId), eq(environments.name, dto.name)));
      for (const existing of clash) {
        if (['DESTROYED', 'TEARDOWN_PENDING', 'EXPIRED'].includes(existing.status)) {
          await this.releaseUniqueIdentity(existing);
        } else {
          throw new ConflictException({
            code: 'environment_name_taken',
            message: `An environment named '${dto.name}' already exists in this organization`,
          });
        }
      }
    }

    const [row] = await this.db
      .insert(environments)
      .values({
        id,
        workspaceId: dto.workspace_id,
        ownerId: user.userId,
        orgId: user.orgId || null,
        name: dto.name,
        gitBranch: dto.git_branch || 'main',
        gitRepoUrl: dto.git_repo_url || '',
        latestCommitSha: dto.latest_commit_sha || '',
        // Created in PROVISIONING; the worker transitions it to RUNNING on success
        // (matches FastAPI EnvironmentService.create -> Celery provision task).
        status: 'PROVISIONING',
        provider,
        lifecycleStage: dto.stage || 'preview',
        namespaceName,
        ttlDurationSeconds: ttl,
        ttlExpiresAt,
        costEstimateHourly: '0.00',
        deployMode: dto.deploy_mode || 'preview',
        createdAt: now,
        updatedAt: now,
      })
      .returning();

    try {
      await this.provisioningQueue.add('provision', {
        action: 'provision',
        environmentId: row.id,
      });
    } catch (_) {}

    return row;
  }

  async getById(id: string): Promise<EnvironmentRow> {
    const [row] = await this.db
      .select()
      .from(environments)
      .where(eq(environments.id, id));

    if (!row) {
      throw new NotFoundException(`Environment ${id} not found`);
    }
    return row;
  }

  async extendTtl(id: string, additionalSeconds: number = 86400): Promise<EnvironmentRow> {
    const current = await this.getById(id);
    const baseTime = current.ttlExpiresAt ? new Date(current.ttlExpiresAt).getTime() : Date.now();
    const newExpires = new Date(baseTime + additionalSeconds * 1000);

    const [updated] = await this.db
      .update(environments)
      .set({
        ttlExpiresAt: newExpires,
        updatedAt: new Date(),
      })
      .where(eq(environments.id, id))
      .returning();

    return updated;
  }

  async retryProvision(id: string, regenerateDockerfile: boolean = false): Promise<EnvironmentRow> {
    await this.getById(id);
    const now = new Date();

    // Retry re-provisions the SAME environment with its existing config - it must NOT
    // touch the TTL. Only an explicit Reset/Extend (extendTtl) or a fresh Relaunch may
    // change ttl_expires_at, matching FastAPI retry_provision (which leaves TTL alone).
    const [updated] = await this.db
      .update(environments)
      .set({
        // Re-enters PROVISIONING; the worker drives it back to RUNNING.
        status: 'PROVISIONING',
        updatedAt: now,
      })
      .where(eq(environments.id, id))
      .returning();

    try {
      await this.provisioningQueue.add('rebuild', {
        action: 'rebuild',
        environmentId: id,
        payload: { regenerateDockerfile },
      });
    } catch (_) {}

    return updated;
  }

  async relaunchEnvironment(id: string): Promise<EnvironmentRow> {
    const current = await this.getById(id);
    const now = new Date();
    const ttl = current.ttlDurationSeconds || 86400;
    const newExpires = new Date(now.getTime() + ttl * 1000);

    const [updated] = await this.db
      .update(environments)
      .set({
        // Re-enters PROVISIONING; the worker drives it back to RUNNING.
        status: 'PROVISIONING',
        ttlExpiresAt: newExpires,
        updatedAt: now,
      })
      .where(eq(environments.id, id))
      .returning();

    try {
      await this.provisioningQueue.add('provision', {
        action: 'provision',
        environmentId: id,
      });
    } catch (_) {}

    return updated;
  }

  async pauseEnvironment(id: string): Promise<EnvironmentRow> {
    const [updated] = await this.db
      .update(environments)
      .set({
        status: 'PAUSED',
        updatedAt: new Date(),
      })
      .where(eq(environments.id, id))
      .returning();

    return updated || this.getById(id);
  }

  async resumeEnvironment(id: string): Promise<EnvironmentRow> {
    const [updated] = await this.db
      .update(environments)
      .set({
        status: 'RUNNING',
        updatedAt: new Date(),
      })
      .where(eq(environments.id, id))
      .returning();

    return updated || this.getById(id);
  }

  async cancelProvision(id: string): Promise<EnvironmentRow> {
    // Stopping an in-flight provision is terminal: flip to FAILED (matches
    // FastAPI's stop-provision path). The worker's cancellation checkpoint sees the
    // status is no longer PROVISIONING and aborts before marking RUNNING.
    const [updated] = await this.db
      .update(environments)
      .set({
        status: 'FAILED',
        updatedAt: new Date(),
      })
      .where(eq(environments.id, id))
      .returning();

    return updated || this.getById(id);
  }

  async delete(id: string): Promise<void> {
    // Soft-destroy, matching FastAPI: mark TEARDOWN_PENDING and let the worker run
    // the teardown pipeline through to DESTROYED (it keeps the row for history).
    // Hard-deleting here would leave the teardown job with no environment to act on.
    await this.db
      .update(environments)
      .set({ status: 'TEARDOWN_PENDING', updatedAt: new Date() })
      .where(eq(environments.id, id));

    // Free the unique (org_id,name) + namespace slots now, so the same name can be
    // relaunched immediately (FastAPI releases on TEARDOWN_PENDING/DESTROYED).
    const [env] = await this.db.select().from(environments).where(eq(environments.id, id));
    if (env) await this.releaseUniqueIdentity(env);

    try {
      await this.provisioningQueue.add('teardown', {
        action: 'teardown',
        environmentId: id,
      });
    } catch (_) {}
  }

  /**
   * Free a terminal environment's unique NAME so the same name can be relaunched
   * (mirrors FastAPI _release_unique_identity's name rename). We deliberately DO NOT
   * rename namespace_name: it already carries the env-id suffix (so it never collides
   * with a fresh env), and the async teardown job reads it to delete the REAL cluster
   * namespace - renaming it here would orphan the deployed cloud resources.
   */
  async releaseUniqueIdentity(env: EnvironmentRow): Promise<void> {
    if (env.name.includes('--destroyed-')) return;
    const suffix = env.id.replace(/-/g, '').slice(0, 12);
    const base = env.name.slice(0, Math.max(1, 128 - suffix.length - 12));
    await this.db
      .update(environments)
      .set({ name: `${base}--destroyed-${suffix}`, updatedAt: new Date() })
      .where(eq(environments.id, env.id));
  }
}

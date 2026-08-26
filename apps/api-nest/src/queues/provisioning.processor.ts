import { Processor, WorkerHost } from '@nestjs/bullmq';
import { Inject, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Job } from 'bullmq';
import { eq } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import { environments, provisioningWorkspaces } from '../database/schema';
import { EnvEventsService, ExecutionStage } from './env-events.service';
import { DockerfileBuildStore } from './dockerfile-build.store';
import { RealK8sProvisionerService } from './real-k8s-provisioner.service';
import { IntegrationNotifierService } from '../integrations/notifier.service';

export interface ProvisioningJobData {
  action:
    | 'provision'
    | 'teardown'
    | 'rebuild'
    | 'drift-scan'
    | 'build-dockerfile'
    | 'finalize-workspace-destroy';
  // Environment lifecycle jobs carry environmentId; build/finalize jobs carry
  // their own target id in `payload` (jobId / workspaceId).
  environmentId?: string;
  payload?: Record<string, unknown>;
}

interface StageStep {
  stage: ExecutionStage;
  message: string;
  delayMs: number;
}

/**
 * Ordered provisioning stages, matching the FastAPI Celery pipeline
 * (INIT -> VALIDATE -> PLAN -> BUILD -> APPLY). The NestJS control plane has no
 * real Kubernetes/Docker layer (k8s.service is a mock), so the APPLY step is
 * simulated - but the stage names, ordering, statuses and emitted events mirror
 * `app/workers/tasks.py::_run_provision` so SSE consumers behave identically.
 */
const PROVISION_STAGES: StageStep[] = [
  { stage: 'INIT', message: 'INIT - Preparing environment context & git repository', delayMs: 400 },
  { stage: 'VALIDATE', message: 'VALIDATE - Checking cluster reachability & deployment spec', delayMs: 400 },
  { stage: 'PLAN', message: 'PLAN - Resolving container image & resource manifests', delayMs: 400 },
  { stage: 'BUILD', message: 'BUILD - Building container image from application source', delayMs: 600 },
  { stage: 'APPLY', message: 'APPLY - Applying manifests & waiting for pods ready', delayMs: 400 },
];

@Processor('provisioning')
export class ProvisioningProcessor extends WorkerHost {
  private readonly logger = new Logger(ProvisioningProcessor.name);

  constructor(
    private readonly configService: ConfigService,
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly events: EnvEventsService,
    private readonly buildStore: DockerfileBuildStore,
    private readonly notifier: IntegrationNotifierService,
    private readonly realK8s: RealK8sProvisionerService,
  ) {
    super();
  }

  async process(job: Job<ProvisioningJobData, void, string>): Promise<void> {
    const { action, environmentId, payload } = job.data;
    this.logger.log(`Processing job ${job.id} of type ${job.name} (action=${action})`);

    // Jobs that operate on non-environment targets (dockerfile build jobs, workspace
    // finalize) carry their id in the payload and manage their own state/errors.
    if (action === 'build-dockerfile') {
      await this.runDockerfileBuild(payload);
      return;
    }
    if (action === 'finalize-workspace-destroy') {
      await this.runFinalizeWorkspaceDestroy(payload);
      return;
    }

    if (!environmentId) return;

    try {
      if (action === 'provision' || action === 'rebuild') {
        await this.runProvision(environmentId, action);
      } else if (action === 'teardown') {
        await this.runTeardown(environmentId);
      } else {
        this.logger.warn(`Unhandled provisioning action '${action}' for env ${environmentId}`);
      }
    } catch (err) {
      // Guarantee the environment never gets stuck: any failure flips it to FAILED
      // and emits an EXECUTION_FAILED event, mirroring `_fail_execution`.
      await this.failExecution(environmentId, action, err);
    }
  }

  private async runProvision(
    environmentId: string,
    action: 'provision' | 'rebuild',
  ): Promise<void> {
    const [env] = await this.db
      .select()
      .from(environments)
      .where(eq(environments.id, environmentId));
    if (!env) {
      this.logger.warn(`provision_missing_environment ${environmentId}`);
      return;
    }
    const commitSha = env.latestCommitSha || null;
    const verb = action === 'rebuild' ? 'Rebuild' : 'Provision';

    for (const step of PROVISION_STAGES) {
      await this.events.emitLog(environmentId, step.message, {
        stage: step.stage,
        status: 'PROVISIONING',
        commitSha,
      });
      await this.sleep(step.delayMs);
    }

    // Cooperative cancellation checkpoint: a stop-provision (FAILED) or force-delete
    // (TEARDOWN_PENDING) flips the row away from PROVISIONING while we run. Abort
    // before marking RUNNING, matching FastAPI's `_provision_cancelled` guard.
    const [latest] = await this.db
      .select()
      .from(environments)
      .where(eq(environments.id, environmentId));
    if (!latest || latest.status !== 'PROVISIONING') {
      this.logger.log(
        `provision_cancelled env=${environmentId} status=${latest?.status ?? 'missing'}`,
      );
      return;
    }

    // Real deploy is REQUIRED - NO SIMULATION in NestJS mode. The env reaches RUNNING only
    // via an actual cluster deploy; realK8s.provision throws on any failure (-> FAILED).
    let workspaceRootDir: string | null = null;
    let runtimeMode: string | null = 'kubernetes';
    let cloudConfig: Record<string, any> | null = null;
    if (env.workspaceId) {
      const [ws] = await this.db
        .select()
        .from(provisioningWorkspaces)
        .where(eq(provisioningWorkspaces.id, env.workspaceId));
      workspaceRootDir = ws?.rootDir ?? null;
      try {
        const cfg = ws?.wizardConfigJson ? JSON.parse(ws.wizardConfigJson) : null;
        runtimeMode = (cfg?.runtime_mode || 'kubernetes').toString();
        cloudConfig = cfg ?? null;
      } catch {
        runtimeMode = 'kubernetes';
      }
    }
    const real = await this.realK8s.provision({
      id: environmentId,
      name: env.name,
      provider: env.provider,
      namespace: env.namespaceName,
      ownerId: env.ownerId,
      cloud: cloudConfig,
      workspaceRootDir,
      runtimeMode,
    });
    if (!real?.applied) {
      // Should not happen (provision throws on failure), but never fake a RUNNING env.
      throw new Error('Provisioning did not produce a real cluster deploy (simulation is disabled).');
    }
    const previewUrl = real.previewUrl ?? '';
    await this.events.emitLog(environmentId, `APPLY - real k8s: ${real.detail}`, {
      stage: 'APPLY',
      status: 'PROVISIONING',
      commitSha,
    });
    await this.db
      .update(environments)
      .set({ status: 'RUNNING', previewUrl, updatedAt: new Date() })
      .where(eq(environments.id, environmentId));

    await this.events.emitLog(
      environmentId,
      `APPLY - ${verb.toLowerCase()} completed, RUNNING. Open app: ${previewUrl}`,
      { stage: 'APPLY', status: 'RUNNING', commitSha },
    );
    await this.events.emitStatus(environmentId, 'RUNNING', previewUrl, {
      stage: 'APPLY',
      commitSha,
      previewUrl,
      appReady: true,
    });

    // Preview ready -> Slack (notify_ready). Fire-and-forget; never blocks the worker.
    await this.notifier.notifyEnvironmentEvent(environmentId, {
      event: 'ready',
      message: `Open app: ${previewUrl}`,
    });
  }

  private async runTeardown(environmentId: string): Promise<void> {
    const [env] = await this.db
      .select()
      .from(environments)
      .where(eq(environments.id, environmentId));
    if (!env) {
      this.logger.warn(`teardown_missing_environment ${environmentId}`);
      return;
    }
    const commitSha = env.latestCommitSha || null;
    const namespace = env.namespaceName || environmentId.substring(0, 8);

    // Mark TEARDOWN_PENDING first (unless already), matching `_run_teardown`.
    if (env.status !== 'TEARDOWN_PENDING') {
      await this.db
        .update(environments)
        .set({ status: 'TEARDOWN_PENDING', updatedAt: new Date() })
        .where(eq(environments.id, environmentId));
      await this.events.emitStatus(environmentId, 'TEARDOWN_PENDING', 'Teardown started', {
        stage: 'INIT',
        commitSha,
      });
    }

    await this.events.emitLog(environmentId, `INIT - tearing down namespace ${namespace}`, {
      stage: 'INIT',
      status: 'TEARDOWN_PENDING',
      commitSha,
    });

    // Real Kubernetes teardown: delete the namespace from the cluster (best-effort). For a
    // cloud env this re-acquires cluster access from the owner's stored creds.
    try {
      let cloudConfig: Record<string, any> | null = null;
      if (env.workspaceId) {
        const [ws] = await this.db
          .select()
          .from(provisioningWorkspaces)
          .where(eq(provisioningWorkspaces.id, env.workspaceId));
        try {
          cloudConfig = ws?.wizardConfigJson ? JSON.parse(ws.wizardConfigJson) : null;
        } catch {
          cloudConfig = null;
        }
      }
      await this.realK8s.teardown({
        id: environmentId,
        name: env.name,
        provider: env.provider,
        namespace: env.namespaceName,
        ownerId: env.ownerId,
        cloud: cloudConfig,
      });
    } catch (err) {
      this.logger.warn(`real_k8s_teardown_failed ${environmentId}: ${(err as Error).message}`);
    }
    await this.events.emitLog(environmentId, 'APPLY - deleting namespace and resources', {
      stage: 'APPLY',
      status: 'TEARDOWN_PENDING',
      commitSha,
    });
    await this.sleep(400);

    // Soft-destroy: keep the row (FastAPI keeps the row and marks DESTROYED) so the
    // history and terminal status remain queryable.
    await this.db
      .update(environments)
      .set({ status: 'DESTROYED', previewUrl: null, updatedAt: new Date() })
      .where(eq(environments.id, environmentId));

    await this.events.emitLog(environmentId, 'APPLY - teardown completed, DESTROYED', {
      stage: 'APPLY',
      status: 'DESTROYED',
      commitSha,
    });
    await this.events.emitStatus(environmentId, 'DESTROYED', 'Teardown completed', {
      stage: 'APPLY',
      commitSha,
    });
  }

  /**
   * Simulated Dockerfile image build. Mirrors the FastAPI `build_dockerfile_image`
   * task lifecycle (queued -> running -> succeeded/failed), advancing the shared
   * build-job store the dockerfiles API polls. The NestJS control plane has no real
   * registry, so the build is simulated rather than cloned/pushed.
   */
  private async runDockerfileBuild(payload?: Record<string, unknown>): Promise<void> {
    const jobId = typeof payload?.jobId === 'string' ? payload.jobId : undefined;
    if (!jobId) {
      this.logger.warn('build-dockerfile job missing jobId in payload');
      return;
    }
    const fullName = typeof payload?.fullName === 'string' ? payload.fullName : 'app';
    const branch = typeof payload?.branch === 'string' ? payload.branch : 'main';
    const tags = Array.isArray(payload?.tags) ? (payload!.tags as string[]) : ['latest'];
    const registry = typeof payload?.registry === 'string' ? payload.registry : 'localhost:5000';

    try {
      this.buildStore.markRunning(jobId, [`Cloning ${fullName}@${branch}`]);
      await this.sleep(500);
      this.buildStore.appendLogs(jobId, ['Building image from Dockerfile']);
      await this.sleep(700);
      const imageRefs = tags.map((tag) => `${registry}/${fullName}:${tag}`);
      this.buildStore.markSucceeded(jobId, imageRefs, [`Pushed ${imageRefs.join(', ')}`]);
      this.logger.log(`dockerfile_build_succeeded job=${jobId}`);
    } catch (err) {
      const errorText = err instanceof Error ? err.message : String(err);
      this.buildStore.markFailed(jobId, errorText);
      this.logger.error(`dockerfile_build_failed job=${jobId}: ${errorText}`);
    }
  }

  /**
   * Simulated workspace destroy finalization. Mirrors FastAPI's
   * `finalize_workspace_destroy`: a 'deleting' workspace is torn down and its row
   * removed on success, or flipped to 'destroy_failed' so it can be retried.
   */
  private async runFinalizeWorkspaceDestroy(payload?: Record<string, unknown>): Promise<void> {
    const workspaceId = typeof payload?.workspaceId === 'string' ? payload.workspaceId : undefined;
    if (!workspaceId) {
      this.logger.warn('finalize-workspace-destroy job missing workspaceId in payload');
      return;
    }
    try {
      const [ws] = await this.db
        .select()
        .from(provisioningWorkspaces)
        .where(eq(provisioningWorkspaces.id, workspaceId));
      if (!ws) {
        this.logger.warn(`finalize-workspace-destroy: workspace ${workspaceId} not found`);
        return;
      }
      if (ws.status !== 'deleting' && ws.status !== 'destroy_failed') {
        this.logger.warn(
          `finalize-workspace-destroy: workspace ${workspaceId} not in a deleting state (${ws.status})`,
        );
        return;
      }
      await this.sleep(500);
      // Success: remove the workspace row (teardown complete).
      await this.db.delete(provisioningWorkspaces).where(eq(provisioningWorkspaces.id, workspaceId));
      this.logger.log(`workspace_destroy_finalized workspace=${workspaceId}`);
    } catch (err) {
      const errorText = err instanceof Error ? err.message : String(err);
      this.logger.error(`workspace_finalize_failed workspace=${workspaceId}: ${errorText}`);
      // Never leave the row stuck on 'deleting' after a failure.
      try {
        await this.db
          .update(provisioningWorkspaces)
          .set({ status: 'destroy_failed' })
          .where(eq(provisioningWorkspaces.id, workspaceId));
      } catch (markErr) {
        this.logger.error(
          `Failed to mark workspace ${workspaceId} destroy_failed: ${(markErr as Error)?.message || markErr}`,
        );
      }
    }
  }

  /** Flip the environment to FAILED and emit an EXECUTION_FAILED event. */
  private async failExecution(
    environmentId: string,
    action: string,
    err: unknown,
  ): Promise<void> {
    const errorText = err instanceof Error ? err.message : String(err);
    const label = action === 'teardown' ? 'Teardown' : action === 'rebuild' ? 'Rebuild' : 'Provision';
    const message = `${label} failed: ${errorText}`;
    this.logger.error(`${message} (env=${environmentId})`, err instanceof Error ? err.stack : undefined);

    let commitSha: string | null = null;
    try {
      const [env] = await this.db
        .select()
        .from(environments)
        .where(eq(environments.id, environmentId));
      commitSha = env?.latestCommitSha || null;
      await this.db
        .update(environments)
        .set({ status: 'FAILED', updatedAt: new Date() })
        .where(eq(environments.id, environmentId));
    } catch (dbErr) {
      this.logger.error(`Failed to persist FAILED status for env ${environmentId}`, dbErr as Error);
    }

    await this.events.emitLog(environmentId, message, {
      logLevel: 'ERROR',
      stage: 'APPLY',
      status: 'FAILED',
      commitSha,
      eventType: 'EXECUTION_FAILED',
      errorMessage: message,
    });
    await this.events.emitStatus(environmentId, 'FAILED', message, {
      stage: 'APPLY',
      commitSha,
      appReady: false,
      errorMessage: message,
    });

    // Preview failed -> Slack (notify_failed) + auto-create a Jira Bug when enabled.
    // Only for env lifecycle actions; workspace-finalize/dockerfile jobs route elsewhere.
    if (action === 'provision' || action === 'rebuild') {
      await this.notifier.notifyEnvironmentEvent(environmentId, {
        event: 'failed',
        message,
      });
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

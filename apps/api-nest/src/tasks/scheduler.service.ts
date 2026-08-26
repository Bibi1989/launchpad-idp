import { Inject, Injectable, Logger } from '@nestjs/common';
import { InjectQueue } from '@nestjs/bullmq';
import { ConfigService } from '@nestjs/config';
import { Cron, CronExpression } from '@nestjs/schedule';
import { Queue } from 'bullmq';
import { and, eq, gt, lt, lte, ne } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import { environments } from '../database/schema';
import { EnvEventsService } from '../queues/env-events.service';
import { K8sService } from '../k8s/k8s.service';
import { IntegrationNotifierService } from '../integrations/notifier.service';

// Watchdog thresholds, matching FastAPI's app/workers/tasks.py.
const STALE_PROVISIONING_SECONDS = 3000;
const STALE_TEARDOWN_SECONDS = 180;
// Warn on Slack when a RUNNING env is within this window of its TTL expiry.
const TTL_WARNING_WINDOW_SECONDS = 30 * 60;

/**
 * Scheduled ("beat") tasks, mirroring the FastAPI Celery beat schedule
 * (app/workers/celery_app.py):
 *   - reap_expired_environments  (TTL reaper + stale watchdog)
 *   - scan_preview_drift         (drift scan)
 *   - sample_environment_costs   (cost metering)
 *
 * The NestJS control plane has no real Kubernetes layer, so drift-scan and
 * cost-metering are gated behind feature flags (default off) exactly the way the
 * FastAPI tasks gate on `drift_scan_enabled` / `cost_metering_enabled`; they are
 * wired here so the two workers have the same scheduled surface and a ready seam.
 */
@Injectable()
export class SchedulerService {
  private readonly logger = new Logger(SchedulerService.name);

  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly events: EnvEventsService,
    private readonly configService: ConfigService,
    private readonly k8s: K8sService,
    @InjectQueue('provisioning') private readonly provisioningQueue: Queue,
    private readonly notifier: IntegrationNotifierService,
  ) {}

  /**
   * TTL reaper + stale watchdog. Runs every minute:
   *   1. RUNNING (or FAILED) environments past their TTL -> EXPIRED (terminal),
   *      unless they are production lifecycle. Emits a WARN log + EXPIRED event.
   *   2. Environments stuck in PROVISIONING with no active worker -> FAILED.
   *   3. TEARDOWN_PENDING rows that lost their worker mid-flight -> re-enqueued.
   */
  @Cron(CronExpression.EVERY_MINUTE)
  async handleTtlReaper(): Promise<void> {
    const now = new Date();
    let reaped = 0;

    // 1. TTL-expired environments.
    try {
      const expired = await this.db
        .select()
        .from(environments)
        .where(and(eq(environments.status, 'RUNNING'), lte(environments.ttlExpiresAt, now)));

      for (const env of expired) {
        // TTL expiry is terminal and cannot be resumed; production is never reaped.
        if (env.lifecycleStage === 'production') continue;

        await this.db
          .update(environments)
          .set({ status: 'EXPIRED', updatedAt: now })
          .where(eq(environments.id, env.id));
        reaped += 1;

        const commitSha = env.latestCommitSha || null;
        await this.events.emitLog(
          env.id,
          `TTL expired - environment marked expired (expires_at=${env.ttlExpiresAt?.toISOString() || 'none'})`,
          { logLevel: 'WARN', stage: 'APPLY', status: 'EXPIRED', commitSha },
        );
        await this.events.emitStatus(env.id, 'EXPIRED', 'TTL expired', {
          stage: 'APPLY',
          commitSha,
        });
        await this.notifier.notifyEnvironmentEvent(env.id, {
          event: 'ttl_expired',
          message: 'Environment TTL expired and it was destroyed.',
        });
        this.logger.log(`TTL Reaper: marked environment ${env.id} (${env.name}) as EXPIRED`);
      }
    } catch (err) {
      this.logger.error(`TTL Reaper (expiry) failed: ${(err as Error)?.message || err}`);
    }

    // TTL warning: RUNNING envs approaching (but not past) their TTL. The notifier
    // dedups so each env warns at most once. Mirrors FastAPI's ttl_warning event.
    try {
      const warnBefore = new Date(now.getTime() + TTL_WARNING_WINDOW_SECONDS * 1000);
      const nearing = await this.db
        .select()
        .from(environments)
        .where(
          and(
            eq(environments.status, 'RUNNING'),
            lte(environments.ttlExpiresAt, warnBefore),
            gt(environments.ttlExpiresAt, now),
          ),
        );
      for (const env of nearing) {
        if (env.lifecycleStage === 'production') continue;
        await this.notifier.notifyEnvironmentEvent(env.id, {
          event: 'ttl_warning',
          message: `Environment expires at ${env.ttlExpiresAt?.toISOString() || 'soon'}.`,
        });
      }
    } catch (err) {
      this.logger.error(`TTL Reaper (warning) failed: ${(err as Error)?.message || err}`);
    }

    // 2. Stale PROVISIONING watchdog: no active worker after the cutoff -> FAILED.
    try {
      const staleCutoff = new Date(now.getTime() - STALE_PROVISIONING_SECONDS * 1000);
      const stuck = await this.db
        .select()
        .from(environments)
        .where(and(eq(environments.status, 'PROVISIONING'), lt(environments.createdAt, staleCutoff)));

      for (const env of stuck) {
        const message =
          `Provisioning timed out with no active worker (stuck > ${STALE_PROVISIONING_SECONDS}s). ` +
          'The provisioning worker likely crashed. Delete and relaunch the preview.';
        await this.db
          .update(environments)
          .set({ status: 'FAILED', updatedAt: now })
          .where(eq(environments.id, env.id));
        reaped += 1;

        const commitSha = env.latestCommitSha || null;
        await this.events.emitLog(env.id, message, {
          logLevel: 'ERROR',
          stage: 'APPLY',
          status: 'FAILED',
          commitSha,
          eventType: 'EXECUTION_FAILED',
          errorMessage: message,
        });
        await this.events.emitStatus(env.id, 'FAILED', message, {
          stage: 'APPLY',
          commitSha,
          appReady: false,
          errorMessage: message,
        });
        this.logger.warn(`TTL Reaper: failed stale PROVISIONING environment ${env.id}`);
      }
    } catch (err) {
      this.logger.error(`TTL Reaper (stale provisioning) failed: ${(err as Error)?.message || err}`);
    }

    // 3. Re-enqueue TEARDOWN_PENDING rows that lost their worker mid-flight.
    try {
      const teardownCutoff = new Date(now.getTime() - STALE_TEARDOWN_SECONDS * 1000);
      const stalledTeardowns = await this.db
        .select()
        .from(environments)
        .where(
          and(eq(environments.status, 'TEARDOWN_PENDING'), lt(environments.updatedAt, teardownCutoff)),
        );

      for (const env of stalledTeardowns) {
        try {
          await this.provisioningQueue.add('teardown', {
            action: 'teardown',
            environmentId: env.id,
          });
          reaped += 1;
          this.logger.log(`TTL Reaper: re-enqueued stale teardown for environment ${env.id}`);
        } catch (enqueueErr) {
          this.logger.error(
            `Failed to re-enqueue teardown for ${env.id}: ${(enqueueErr as Error)?.message || enqueueErr}`,
          );
        }
      }
    } catch (err) {
      this.logger.error(`TTL Reaper (stale teardowns) failed: ${(err as Error)?.message || err}`);
    }

    if (reaped > 0) {
      this.logger.log(`TTL Reaper complete: ${reaped} environment(s) actioned`);
    }
  }

  /**
   * Drift scan. Mirrors `scan_preview_drift`. Gated behind DRIFT_SCAN_ENABLED and a
   * real Kubernetes layer (KUBERNETES_ENABLED). Since the NestJS k8s layer is a
   * mock, this is a no-op seam by default - it never fabricates drift findings.
   */
  @Cron(CronExpression.EVERY_5_MINUTES)
  async handleDriftScan(): Promise<void> {
    const driftEnabled = this.flag('DRIFT_SCAN_ENABLED');
    const k8sEnabled = this.flag('KUBERNETES_ENABLED');
    if (!driftEnabled || !k8sEnabled) {
      this.logger.debug('Drift scan skipped (DRIFT_SCAN_ENABLED / KUBERNETES_ENABLED not set)');
      return;
    }
    try {
      const running = await this.db
        .select()
        .from(environments)
        .where(eq(environments.status, 'RUNNING'));
      let recorded = 0;
      for (const env of running) {
        const finding = await this.k8s.scanEnvironmentDrift({
          id: env.id,
          namespaceName: env.namespaceName,
        });
        if (finding?.drifted) {
          recorded += 1;
          await this.events.emitLog(env.id, `DRIFT - ${finding.summary}`, {
            logLevel: 'WARN',
            stage: 'APPLY',
            status: env.status as any,
            commitSha: env.latestCommitSha || null,
          });
        }
      }
      this.logger.debug(`Drift scan complete: recorded=${recorded} scanned=${running.length}`);
    } catch (err) {
      this.logger.error(`Drift scan failed: ${(err as Error)?.message || err}`);
    }
  }

  /**
   * Cost metering. Mirrors `sample_environment_costs`. Gated behind
   * COST_METERING_ENABLED. Samples billable RUNNING environments; without a cost
   * accrual column it logs the sampled burn rate rather than persisting accrual.
   */
  @Cron(CronExpression.EVERY_5_MINUTES)
  async handleCostMetering(): Promise<void> {
    if (!this.flag('COST_METERING_ENABLED')) {
      this.logger.debug('Cost metering skipped (COST_METERING_ENABLED not set)');
      return;
    }
    const k8sEnabled = this.flag('KUBERNETES_ENABLED');
    try {
      const running = await this.db
        .select()
        .from(environments)
        .where(and(eq(environments.status, 'RUNNING'), ne(environments.lifecycleStage, 'production')));
      for (const env of running) {
        // Sample namespace usage from the (mock) cluster when k8s is enabled, the
        // same input FastAPI feeds into its rate-card accrual.
        const usage = k8sEnabled
          ? await this.k8s.readNamespaceUsage(env.namespaceName || undefined)
          : null;
        this.logger.log(
          `cost_metering_sampled env=${env.id} hourly=${env.costEstimateHourly ?? '0.00'}` +
            (usage ? ` cpu=${usage.cpuMillicores}m mem=${usage.memoryMib}Mi` : ''),
        );
      }
      if (running.length > 0) {
        this.logger.log(`Cost metering complete: sampled ${running.length} environment(s)`);
      }
    } catch (err) {
      this.logger.error(`Cost metering failed: ${(err as Error)?.message || err}`);
    }
  }

  private flag(name: string): boolean {
    const raw = (this.configService.get<string>(name) ?? '').toString().toLowerCase();
    return raw === '1' || raw === 'true' || raw === 'yes' || raw === 'on';
  }
}

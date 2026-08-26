import { Inject, Injectable, Logger, OnModuleDestroy } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'crypto';
import Redis from 'ioredis';

import { Database, DRIZZLE } from '../database/database.module';
import { deploymentLogs } from '../database/schema';

/**
 * Environment lifecycle vocabulary, kept 1:1 with the FastAPI control plane
 * (app/models/domain.py). The two backends publish to the same Redis channel and
 * the same SSE consumers, so these string values MUST match exactly.
 */
export type EnvironmentStatus =
  | 'PROVISIONING'
  | 'RUNNING'
  | 'PAUSED'
  | 'EXPIRED'
  | 'TEARDOWN_PENDING'
  | 'DESTROYED'
  | 'FAILED';

export type ExecutionStage = 'INIT' | 'VALIDATE' | 'PLAN' | 'BUILD' | 'APPLY';

export type LogLevel = 'INFO' | 'WARN' | 'ERROR';

export type EnvEventType = 'STATUS_CHANGE' | 'LOG' | 'EXECUTION_FAILED';

/**
 * Mirror of FastAPI's `EnvChannelEvent` (app/core/events.py). Field names are the
 * snake_case JSON keys the SSE stream forwards verbatim to the frontend, so every
 * key here has to line up with the Python model - including the ones we leave null.
 */
export interface EnvChannelEvent {
  type: EnvEventType;
  status: string | null;
  commit_sha: string | null;
  message: string | null;
  log_level: string | null;
  environment_id: string;
  stage: ExecutionStage | null;
  timestamp: string;
  preview_url: string | null;
  node_port: number | null;
  app_ready: boolean | null;
  notice: string | null;
  error_message: string | null;
  preview_endpoints: Array<Record<string, unknown>> | null;
  failure_summary: string | null;
}

export interface EmitOptions {
  status?: EnvironmentStatus | null;
  commitSha?: string | null;
  logLevel?: LogLevel;
  stage?: ExecutionStage | null;
  previewUrl?: string | null;
  nodePort?: number | null;
  appReady?: boolean | null;
  notice?: string | null;
  errorMessage?: string | null;
  failureSummary?: string | null;
  previewEndpoints?: Array<Record<string, unknown>> | null;
  eventType?: EnvEventType;
}

/**
 * Strip C0/C1 control characters (except tab, newline, carriage return) so a rogue
 * log line can not corrupt the JSON envelope or the terminal SSE frame. Mirrors the
 * intent of FastAPI's `sanitize_log_message`.
 */
export function sanitizeLogMessage(message: string): string {
  let out = '';
  for (const ch of message) {
    const code = ch.charCodeAt(0);
    const isTabOrNewline = code === 0x09 || code === 0x0a || code === 0x0d;
    const isControl = (code >= 0x00 && code <= 0x1f) || (code >= 0x7f && code <= 0x9f);
    if (isControl && !isTabOrNewline) continue;
    out += ch;
  }
  return out.trim();
}

export function envChannel(environmentId: string): string {
  return `env_channel:${environmentId}`;
}

/**
 * Shared publisher for environment lifecycle events. Used by both the BullMQ
 * provisioning processor and the scheduled reaper so the Redis event envelope and
 * the deployment_logs writes stay identical no matter which one emits them
 * (parity with FastAPI's `_emit_log` / `_emit_stage` / `_publish_status` helpers).
 */
@Injectable()
export class EnvEventsService implements OnModuleDestroy {
  private readonly logger = new Logger(EnvEventsService.name);
  private redisClient: Redis | null = null;

  constructor(
    private readonly configService: ConfigService,
    @Inject(DRIZZLE) private readonly db: Database,
  ) {
    const redisUrl = this.configService.get<string>('REDIS_URL') || 'redis://127.0.0.1:6379/0';
    try {
      this.redisClient = new Redis(redisUrl, { maxRetriesPerRequest: null });
      // Without an error handler a dropped Redis connection throws unhandled and
      // crashes the worker process. Log and let ioredis reconnect on its own.
      this.redisClient.on('error', (err) => {
        this.logger.warn(`Redis publisher error: ${err?.message || err}`);
      });
    } catch (err) {
      this.logger.error('Failed to initialize Redis publisher client', err as Error);
    }
  }

  async onModuleDestroy(): Promise<void> {
    if (this.redisClient) {
      try {
        await this.redisClient.quit();
      } catch (_) {
        /* best effort */
      }
      this.redisClient = null;
    }
  }

  /** Publish a lifecycle event to `env_channel:{id}` using the full envelope. */
  async publishEvent(
    environmentId: string,
    eventType: EnvEventType,
    message: string | null,
    options: EmitOptions = {},
  ): Promise<void> {
    if (!this.redisClient || !environmentId) return;

    const payload: EnvChannelEvent = {
      type: eventType,
      status: options.status ?? null,
      commit_sha: options.commitSha ?? null,
      message: message ?? null,
      log_level: options.logLevel ?? null,
      environment_id: environmentId,
      stage: options.stage ?? null,
      timestamp: new Date().toISOString(),
      preview_url: options.previewUrl ?? null,
      node_port: options.nodePort ?? null,
      app_ready: options.appReady ?? null,
      notice: options.notice ?? null,
      error_message: options.errorMessage ?? null,
      preview_endpoints: options.previewEndpoints ?? null,
      failure_summary: options.failureSummary ?? null,
    };

    try {
      await this.redisClient.publish(envChannel(environmentId), JSON.stringify(payload));
    } catch (err) {
      this.logger.error(`Failed to publish ${eventType} to ${envChannel(environmentId)}`, err as Error);
    }
  }

  /**
   * Persist a deployment log line AND publish it as a LOG (or EXECUTION_FAILED)
   * event - the same pairing FastAPI's `_emit_log` performs so the terminal SSE
   * and the persisted history never drift apart.
   */
  async emitLog(environmentId: string, message: string, options: EmitOptions = {}): Promise<void> {
    const safeMessage = sanitizeLogMessage(message);
    const logLevel = options.logLevel ?? 'INFO';
    const stage = options.stage ?? null;

    try {
      await this.db.insert(deploymentLogs).values({
        id: randomUUID(),
        environmentId,
        logLevel,
        stage,
        message: safeMessage,
        timestamp: new Date(),
      });
    } catch (err) {
      this.logger.error(`Failed to persist deployment log for env ${environmentId}`, err as Error);
    }

    await this.publishEvent(environmentId, options.eventType ?? 'LOG', safeMessage, {
      ...options,
      logLevel,
      stage,
    });
  }

  /** Publish a STATUS_CHANGE event (no deployment log row), like `_publish_status`. */
  async emitStatus(
    environmentId: string,
    status: EnvironmentStatus,
    message: string | null,
    options: EmitOptions = {},
  ): Promise<void> {
    await this.publishEvent(environmentId, 'STATUS_CHANGE', message, { ...options, status });
  }
}

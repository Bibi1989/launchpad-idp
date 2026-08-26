import { Injectable } from '@nestjs/common';

/** Lowercase statuses, kept 1:1 with FastAPI's `DockerfileBuildJobStatus`. */
export type DockerfileBuildJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

/** Shape mirrors FastAPI's `DockerfileBuildJobResponse`. */
export interface DockerfileBuildJob {
  job_id: string;
  status: DockerfileBuildJobStatus;
  image_refs: string[];
  logs: string[];
  error: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * In-process store for async Dockerfile build jobs. Shared between
 * DockerfilesService (which creates + reads jobs) and the BullMQ processor (which
 * advances them), so both see the same state without a circular module dependency.
 * The NestJS control plane runs the worker in-process, so an in-memory map is
 * sufficient; FastAPI persists these in `dockerfile_jobs` but the API contract
 * (statuses + fields) is identical.
 */
@Injectable()
export class DockerfileBuildStore {
  private readonly jobs = new Map<string, DockerfileBuildJob>();

  create(jobId: string, initialLogs: string[] = []): DockerfileBuildJob {
    const nowIso = new Date().toISOString();
    const job: DockerfileBuildJob = {
      job_id: jobId,
      status: 'queued',
      image_refs: [],
      logs: initialLogs,
      error: null,
      created_at: nowIso,
      updated_at: nowIso,
    };
    this.jobs.set(jobId, job);
    return job;
  }

  get(jobId: string): DockerfileBuildJob | null {
    return this.jobs.get(jobId) ?? null;
  }

  private update(jobId: string, patch: Partial<DockerfileBuildJob>): void {
    const job = this.jobs.get(jobId);
    if (!job) return;
    Object.assign(job, patch, { updated_at: new Date().toISOString() });
  }

  markRunning(jobId: string, appendLogs: string[] = []): void {
    const job = this.jobs.get(jobId);
    if (!job) return;
    this.update(jobId, { status: 'running', logs: [...job.logs, ...appendLogs] });
  }

  appendLogs(jobId: string, appendLogs: string[]): void {
    const job = this.jobs.get(jobId);
    if (!job) return;
    this.update(jobId, { logs: [...job.logs, ...appendLogs] });
  }

  markSucceeded(jobId: string, imageRefs: string[], appendLogs: string[] = []): void {
    const job = this.jobs.get(jobId);
    if (!job) return;
    this.update(jobId, {
      status: 'succeeded',
      image_refs: imageRefs,
      logs: [...job.logs, ...appendLogs],
    });
  }

  markFailed(jobId: string, error: string): void {
    this.update(jobId, { status: 'failed', error });
  }
}

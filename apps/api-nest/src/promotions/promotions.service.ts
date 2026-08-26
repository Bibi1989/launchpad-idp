import {
  BadRequestException,
  ConflictException,
  Inject,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { and, desc, eq } from 'drizzle-orm';
import { randomUUID } from 'crypto';

import { CurrentUser } from '../common/auth/current-user.interface';
import { Database, DRIZZLE } from '../database/database.module';
import {
  environments,
  EnvironmentRow,
  organizations,
  promotionRequests,
  PromotionRequestRow,
} from '../database/schema';

// Lifecycle stages (preview -> staging -> production). Mirrors FastAPI
// app.models.domain.LifecycleStage.
const STAGE_PREVIEW = 'preview';
const STAGE_STAGING = 'staging';
const STAGE_PRODUCTION = 'production';

// Promotion request statuses. Mirrors FastAPI PromotionRequestStatus EXACTLY:
// values are lowercase (do not invent statuses like PENDING_APPROVAL).
const STATUS_PENDING = 'pending';
const STATUS_APPROVED = 'approved';
const STATUS_REJECTED = 'rejected';
const STATUS_COMPLETED = 'completed';

// Allowed stage transitions, mirroring FastAPI _ALLOWED_TRANSITIONS.
const ALLOWED_TRANSITIONS: Record<string, string[]> = {
  [STAGE_PREVIEW]: [STAGE_STAGING, STAGE_PRODUCTION],
  [STAGE_STAGING]: [STAGE_PRODUCTION],
  [STAGE_PRODUCTION]: [],
};

// Environment statuses that may be promoted from, mirroring FastAPI
// _PROMOTE_SOURCE_STATUSES (RUNNING or FAILED).
const PROMOTE_SOURCE_STATUSES = ['RUNNING', 'FAILED'];

const DEFAULT_STAGING_TTL_HOURS = 168;

@Injectable()
export class PromotionsService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  async getPolicy(orgId: string): Promise<any> {
    const org = await this.requireOrg(orgId);
    return this.policyResponse(org);
  }

  async updatePolicy(orgId: string, payload: any): Promise<any> {
    await this.requireOrg(orgId);
    const body = payload ?? {};

    const updates: Partial<typeof organizations.$inferInsert> = {};
    if (body.staging_requires_approval !== undefined && body.staging_requires_approval !== null) {
      updates.promotionStagingRequiresApproval = Boolean(body.staging_requires_approval);
    }
    if (
      body.production_requires_approval !== undefined &&
      body.production_requires_approval !== null
    ) {
      updates.promotionProductionRequiresApproval = Boolean(body.production_requires_approval);
    }

    if (Object.keys(updates).length > 0) {
      await this.db.update(organizations).set(updates).where(eq(organizations.id, orgId));
    }

    const org = await this.requireOrg(orgId);
    return this.policyResponse(org);
  }

  async listForOrg(orgId: string, status?: string): Promise<any[]> {
    const conditions = [eq(promotionRequests.orgId, orgId)];
    if (status) {
      conditions.push(eq(promotionRequests.status, status));
    }

    const rows = await this.db
      .select()
      .from(promotionRequests)
      .where(conditions.length === 1 ? conditions[0] : and(...conditions))
      .orderBy(desc(promotionRequests.createdAt))
      .limit(100);

    return rows.map((row) => this.toResponse(row));
  }

  async stagePromote(envId: string, payload: any, user: CurrentUser): Promise<any> {
    const body = payload ?? {};
    const source = await this.requireEnvironment(envId);

    const targetStage: string = (body.target_stage || STAGE_STAGING).toString();
    const sourceStage = (source.lifecycleStage || STAGE_PREVIEW).toLowerCase();
    const allowed = ALLOWED_TRANSITIONS[sourceStage] ?? [];
    if (!allowed.includes(targetStage)) {
      throw new BadRequestException({
        code: 'invalid_stage_transition',
        message:
          `Cannot promote from ${sourceStage} to ${targetStage}. ` +
          `Allowed: ${allowed.slice().sort().join(', ') || 'none'}`,
      });
    }

    if (!PROMOTE_SOURCE_STATUSES.includes(source.status)) {
      throw new ConflictException({
        code: 'environment_not_promotable',
        message: 'Environment must be RUNNING or FAILED to promote',
      });
    }

    const pending = await this.pendingForSource(source.id);
    if (pending) {
      throw new ConflictException({
        code: 'promotion_already_pending',
        message: 'A promotion request is already pending for this environment',
        details: { promotion_id: pending.id },
      });
    }

    const orgId = source.orgId;
    if (!orgId) {
      throw new NotFoundException({
        code: 'org_not_found',
        message: 'Organization not found',
      });
    }
    const org = await this.requireOrg(orgId);

    const requiresApproval =
      (targetStage === STAGE_PRODUCTION && Boolean(org.promotionProductionRequiresApproval)) ||
      (targetStage === STAGE_STAGING && Boolean(org.promotionStagingRequiresApproval));

    const name = (body.name || `${source.name}-${targetStage}`).toString().slice(0, 64);
    const payloadJson = JSON.stringify({
      name,
      ttl_hours: body.ttl_hours ?? null,
    });

    const [request] = await this.db
      .insert(promotionRequests)
      .values({
        // Explicit id: FastAPI-owned table has no DB-level id default, so relying on
        // Drizzle's default inserts NULL -> PK violation -> 500.
        id: randomUUID(),
        orgId,
        sourceEnvironmentId: source.id,
        targetStage,
        status: STATUS_PENDING,
        requestedBy: user.userId,
        payloadJson,
      })
      .returning();

    if (requiresApproval) {
      return this.toResponse(request);
    }

    // No approval required: execute the promotion immediately, mirroring
    // FastAPI PromotionService.request_promote's non-approval branch.
    const executed = await this.executePromotion(request, source);
    return this.toResponse(executed);
  }

  async approve(promotionId: string, payload: any, user: CurrentUser): Promise<any> {
    const body = payload ?? {};
    const request = await this.requireRequest(promotionId);

    if (request.status !== STATUS_PENDING) {
      throw new ConflictException({
        code: 'promotion_not_pending',
        message: `Promotion is ${request.status}, not pending`,
      });
    }

    const source = await this.db
      .select()
      .from(environments)
      .where(eq(environments.id, request.sourceEnvironmentId))
      .then((rows) => rows[0]);
    if (!source) {
      throw new NotFoundException({
        code: 'source_not_found',
        message: 'Source environment missing',
      });
    }

    const note = (body.note ?? '').toString().trim() || null;
    const [approved] = await this.db
      .update(promotionRequests)
      .set({
        status: STATUS_APPROVED,
        reviewedBy: user.userId,
        reviewedAt: new Date(),
        reviewNote: note,
      })
      .where(eq(promotionRequests.id, request.id))
      .returning();

    // Execute the promotion, mirroring FastAPI PromotionService.approve which
    // launches the target environment and marks the request completed.
    const executed = await this.executePromotion(approved, source);
    return this.toResponse(executed);
  }

  async reject(promotionId: string, payload: any, user: CurrentUser): Promise<any> {
    const body = payload ?? {};
    const request = await this.requireRequest(promotionId);

    if (request.status !== STATUS_PENDING) {
      throw new ConflictException({
        code: 'promotion_not_pending',
        message: `Promotion is ${request.status}, not pending`,
      });
    }

    const note = (body.note ?? '').toString().trim() || null;
    const [rejected] = await this.db
      .update(promotionRequests)
      .set({
        status: STATUS_REJECTED,
        reviewedBy: user.userId,
        reviewedAt: new Date(),
        reviewNote: note,
      })
      .where(eq(promotionRequests.id, request.id))
      .returning();

    return this.toResponse(rejected);
  }

  // --- helpers ------------------------------------------------------------

  /**
   * Launch the target environment and mark the request completed. Mirrors
   * FastAPI PromotionService._execute_promotion (control-plane simulated: the
   * target environment row is created and the request is transitioned to
   * completed; provisioning enqueue is handled elsewhere by the worker).
   */
  private async executePromotion(
    request: PromotionRequestRow,
    source: EnvironmentRow,
  ): Promise<PromotionRequestRow> {
    let name = `${source.name}-${request.targetStage}`;
    let ttlHours: number | null = DEFAULT_STAGING_TTL_HOURS;
    if (request.payloadJson) {
      try {
        const body = JSON.parse(request.payloadJson);
        if (body && typeof body.name === 'string' && body.name.length > 0) {
          name = body.name;
        }
        if (body && (body.ttl_hours === null || typeof body.ttl_hours === 'number')) {
          ttlHours = body.ttl_hours;
        }
      } catch {
        // Ignore malformed payload; fall back to defaults.
      }
    }
    name = name.slice(0, 64);

    const targetStage = request.targetStage;
    let ttlExpiresAt: Date | null = null;
    if (targetStage !== STAGE_PRODUCTION) {
      const hours = ttlHours ?? DEFAULT_STAGING_TTL_HOURS;
      ttlExpiresAt = new Date(Date.now() + hours * 3600 * 1000);
    }

    const targetId = randomUUID();
    await this.db.insert(environments).values({
      id: targetId,
      workspaceId: source.workspaceId,
      ownerId: source.ownerId,
      orgId: request.orgId,
      projectId: source.projectId,
      name,
      gitBranch: source.gitBranch,
      gitRepoUrl: source.gitRepoUrl,
      latestCommitSha: source.latestCommitSha,
      status: source.status,
      lifecycleStage: targetStage,
      provider: source.provider,
      namespaceName: `launchpad-env-${targetId}`,
      ttlExpiresAt,
      costEstimateHourly: source.costEstimateHourly,
      deployMode: source.deployMode || 'preview',
    });

    const [completed] = await this.db
      .update(promotionRequests)
      .set({
        targetEnvironmentId: targetId,
        status: STATUS_COMPLETED,
        completedAt: new Date(),
      })
      .where(eq(promotionRequests.id, request.id))
      .returning();

    return completed;
  }

  private async requireOrg(orgId: string) {
    const [org] = await this.db
      .select()
      .from(organizations)
      .where(eq(organizations.id, orgId));
    if (!org) {
      throw new NotFoundException({
        code: 'org_not_found',
        message: 'Organization not found',
      });
    }
    return org;
  }

  private async requireEnvironment(envId: string): Promise<EnvironmentRow> {
    const [env] = await this.db
      .select()
      .from(environments)
      .where(eq(environments.id, envId));
    if (!env) {
      throw new NotFoundException({
        code: 'environment_not_found',
        message: 'Environment not found',
      });
    }
    return env;
  }

  private async requireRequest(promotionId: string): Promise<PromotionRequestRow> {
    const [request] = await this.db
      .select()
      .from(promotionRequests)
      .where(eq(promotionRequests.id, promotionId));
    if (!request) {
      throw new NotFoundException({
        code: 'promotion_not_found',
        message: 'Promotion request not found',
      });
    }
    return request;
  }

  private async pendingForSource(sourceId: string): Promise<PromotionRequestRow | undefined> {
    const [row] = await this.db
      .select()
      .from(promotionRequests)
      .where(
        and(
          eq(promotionRequests.sourceEnvironmentId, sourceId),
          eq(promotionRequests.status, STATUS_PENDING),
        ),
      )
      .limit(1);
    return row;
  }

  private policyResponse(org: {
    promotionStagingRequiresApproval: boolean;
    promotionProductionRequiresApproval: boolean;
  }): any {
    // Mirror FastAPI OrgPromotionPolicyRead exactly (snake_case keys) so both
    // control planes serve an identical /api/v1 promotion-policy contract.
    return {
      staging_requires_approval: Boolean(org.promotionStagingRequiresApproval),
      production_requires_approval: Boolean(org.promotionProductionRequiresApproval),
    };
  }

  private toResponse(row: PromotionRequestRow): any {
    // Mirror FastAPI PromotionRequestRead core fields (snake_case keys).
    return {
      id: row.id,
      org_id: row.orgId,
      source_environment_id: row.sourceEnvironmentId,
      target_environment_id: row.targetEnvironmentId,
      target_stage: row.targetStage,
      status: row.status,
      requested_by: row.requestedBy,
      reviewed_by: row.reviewedBy,
      review_note: row.reviewNote,
      created_at: row.createdAt ? row.createdAt.toISOString() : null,
      reviewed_at: row.reviewedAt ? row.reviewedAt.toISOString() : null,
      completed_at: row.completedAt ? row.completedAt.toISOString() : null,
    };
  }
}

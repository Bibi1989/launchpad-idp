import { Inject, Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { and, count, eq, ne } from 'drizzle-orm';

import { CurrentUser } from '../common/auth/current-user.interface';
import { Database, DRIZZLE } from '../database/database.module';
import {
  environments,
  OrganizationRow,
  organizations,
  orgMembers,
  projects,
  provisioningWorkspaces,
} from '../database/schema';

/**
 * Plan catalog + limits, mirrored 1:1 from the FastAPI backend
 * (apps/api/app/models/domain.py OrgPlan and apps/api/app/services/plans.py PLAN_LIMITS).
 * FastAPI only defines FREE and PRO plans.
 */
export type PlanId = 'free' | 'pro';

interface PlanLimits {
  maxProjects: number;
  maxWorkspaces: number;
}

const PLAN_LIMITS: Record<PlanId, PlanLimits> = {
  free: { maxProjects: 2, maxWorkspaces: 5 },
  pro: { maxProjects: 10, maxWorkspaces: 20 },
};

// Matches FastAPI PRO_MONTHLY_EUR (apps/api/app/services/plans.py).
const PRO_MONTHLY_EUR = 27;

// Environment statuses that no longer count against usage (mirrors FastAPI EnvironmentStatus.DESTROYED).
const INACTIVE_ENVIRONMENT_STATUS = 'DESTROYED';

function normalizePlan(plan: string | null | undefined): PlanId {
  const raw = (plan ?? 'free').trim().toLowerCase();
  return raw === 'pro' ? 'pro' : 'free';
}

function limitsForPlan(plan: string | null | undefined): PlanLimits {
  return PLAN_LIMITS[normalizePlan(plan)];
}

@Injectable()
export class BillingService {
  private readonly logger = new Logger(BillingService.name);

  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly config: ConfigService,
  ) {}

  // --- Stripe config gating (mirrors FastAPI settings: STRIPE_SECRET_KEY, etc.) ---

  private stripeSecretKey(): string {
    return (this.config.get<string>('STRIPE_SECRET_KEY') ?? '').trim();
  }

  private stripePriceIdPro(): string {
    return (this.config.get<string>('STRIPE_PRICE_ID_PRO') ?? '').trim();
  }

  private stripeWebhookSecret(): string {
    return (this.config.get<string>('STRIPE_WEBHOOK_SECRET') ?? '').trim();
  }

  private appUrl(): string {
    const raw = (this.config.get<string>('PUBLIC_APP_URL') ?? 'http://localhost:3000').trim();
    return raw.replace(/\/+$/, '');
  }

  private isStripeConfigured(): boolean {
    return this.stripeSecretKey().length > 0;
  }

  // --- Org resolution helpers ---

  private async getOrgById(orgId: string): Promise<OrganizationRow | null> {
    const [org] = await this.db.select().from(organizations).where(eq(organizations.id, orgId));
    return org ?? null;
  }

  /**
   * Resolve the caller's active organization: prefer the JWT org claim, otherwise
   * fall back to the first org the user is a member of. Mirrors how FastAPI resolves
   * an org context for the current user.
   */
  private async resolveUserOrg(user: CurrentUser): Promise<OrganizationRow | null> {
    if (user.orgId) {
      const org = await this.getOrgById(user.orgId);
      if (org) return org;
    }
    const [membership] = await this.db
      .select({ org: organizations })
      .from(orgMembers)
      .innerJoin(organizations, eq(organizations.id, orgMembers.orgId))
      .where(eq(orgMembers.userId, user.userId));
    return membership?.org ?? null;
  }

  private async countProjects(orgId: string): Promise<number> {
    const [row] = await this.db
      .select({ value: count() })
      .from(projects)
      .where(eq(projects.orgId, orgId));
    return Number(row?.value ?? 0);
  }

  private async countWorkspaces(orgId: string): Promise<number> {
    const [row] = await this.db
      .select({ value: count() })
      .from(provisioningWorkspaces)
      .where(eq(provisioningWorkspaces.orgId, orgId));
    return Number(row?.value ?? 0);
  }

  private async countActiveEnvironments(orgId: string): Promise<number> {
    const [row] = await this.db
      .select({ value: count() })
      .from(environments)
      .where(
        and(eq(environments.orgId, orgId), ne(environments.status, INACTIVE_ENVIRONMENT_STATUS)),
      );
    return Number(row?.value ?? 0);
  }

  // --- Public API used by the controller ---

  /**
   * Plan catalog. Mirrors the FastAPI plan definitions (OrgPlan + PLAN_LIMITS +
   * PRO_MONTHLY_EUR). FastAPI itself only models FREE and PRO plans.
   */
  getPlans() {
    return [
      {
        id: 'free',
        name: 'Free',
        price_monthly_eur: 0,
        max_projects: PLAN_LIMITS.free.maxProjects,
        max_workspaces: PLAN_LIMITS.free.maxWorkspaces,
      },
      {
        id: 'pro',
        name: 'Pro',
        price_monthly_eur: PRO_MONTHLY_EUR,
        max_projects: PLAN_LIMITS.pro.maxProjects,
        max_workspaces: PLAN_LIMITS.pro.maxWorkspaces,
      },
    ];
  }

  /**
   * Real usage for the caller's org: workspaces + active environments, with limits
   * derived from the org's current plan.
   */
  async getUsage(user: CurrentUser) {
    const org = await this.resolveUserOrg(user);
    if (!org) {
      const limits = PLAN_LIMITS.free;
      return {
        org_id: user.orgId ?? user.userId,
        plan: 'free' as PlanId,
        workspaces_used: 0,
        workspaces_limit: limits.maxWorkspaces,
        environments_used: 0,
        environments_limit: limits.maxWorkspaces,
      };
    }

    const plan = normalizePlan(org.plan);
    const limits = PLAN_LIMITS[plan];
    const workspacesUsed = await this.countWorkspaces(org.id);
    const environmentsUsed = await this.countActiveEnvironments(org.id);
    return {
      org_id: org.id,
      plan,
      workspaces_used: workspacesUsed,
      workspaces_limit: limits.maxWorkspaces,
      environments_used: environmentsUsed,
      // FastAPI plans do not model a separate environment cap; derive from the workspace limit.
      environments_limit: limits.maxWorkspaces,
    };
  }

  /**
   * Organization plan summary. Superset of the FastAPI OrgPlanRead shape
   * (org_id, plan, max_projects, max_workspaces, project_count, workspace_count,
   * pro_price_eur, stripe_customer_id, stripe_subscription_id, plan_updated_at) plus
   * the promotion-approval flags the org row carries.
   */
  async getOrgPlan(orgId: string) {
    const org = await this.getOrgById(orgId);
    if (!org) {
      const limits = PLAN_LIMITS.free;
      return {
        org_id: orgId,
        plan: 'free' as PlanId,
        max_projects: limits.maxProjects,
        max_workspaces: limits.maxWorkspaces,
        project_count: 0,
        workspace_count: 0,
        pro_price_eur: PRO_MONTHLY_EUR,
        stripe_customer_id: null,
        stripe_subscription_id: null,
        plan_updated_at: null,
        promotion_staging_requires_approval: false,
        promotion_production_requires_approval: true,
      };
    }

    const plan = normalizePlan(org.plan);
    const limits = PLAN_LIMITS[plan];
    const projectCount = await this.countProjects(org.id);
    const workspaceCount = await this.countWorkspaces(org.id);
    return {
      org_id: org.id,
      plan,
      max_projects: limits.maxProjects,
      max_workspaces: limits.maxWorkspaces,
      project_count: projectCount,
      workspace_count: workspaceCount,
      pro_price_eur: PRO_MONTHLY_EUR,
      stripe_customer_id: org.stripeCustomerId ?? null,
      stripe_subscription_id: org.stripeSubscriptionId ?? null,
      plan_updated_at: org.planUpdatedAt ?? null,
      promotion_staging_requires_approval: org.promotionStagingRequiresApproval,
      promotion_production_requires_approval: org.promotionProductionRequiresApproval,
    };
  }

  /**
   * Create a checkout session. The NestJS control-plane has no Stripe SDK, so we mirror
   * FastAPI's config gating and return a simulated URL that matches FastAPI's redirect
   * target (`{PUBLIC_APP_URL}/org?billing=success`). The Stripe env vars are read the same
   * way FastAPI reads them so both backends share one .env.
   */
  async createCheckout(orgId: string): Promise<{ checkout_url: string }> {
    const appUrl = this.appUrl();
    if (this.isStripeConfigured() && !this.stripePriceIdPro()) {
      this.logger.warn(
        'Stripe secret is set but STRIPE_PRICE_ID_PRO is missing; returning simulated checkout URL',
      );
    }
    if (!this.isStripeConfigured()) {
      this.logger.debug('Stripe not configured; returning simulated checkout URL');
    }
    return { checkout_url: `${appUrl}/org?billing=success&org_id=${orgId}` };
  }

  /**
   * Create a billing portal session. Same simulation contract as createCheckout; mirrors
   * FastAPI's portal return_url (`{PUBLIC_APP_URL}/org`).
   */
  async createPortal(orgId: string): Promise<{ portal_url: string }> {
    const appUrl = this.appUrl();
    if (!this.isStripeConfigured()) {
      this.logger.debug('Stripe not configured; returning simulated portal URL');
    }
    return { portal_url: `${appUrl}/org?billing=portal&org_id=${orgId}` };
  }

  /**
   * Persist an organization's subscription state from a webhook event. Mirrors
   * BillingService.apply_subscription_status in FastAPI: active/trialing -> pro, else free.
   */
  private async applySubscriptionStatus(params: {
    orgId: string;
    subscriptionId: string | null;
    statusValue: string;
    customerId?: string | null;
  }): Promise<void> {
    const org = await this.getOrgById(params.orgId);
    if (!org) {
      this.logger.warn(`billing_org_missing org_id=${params.orgId}`);
      return;
    }
    const active = params.statusValue === 'active' || params.statusValue === 'trialing';
    const update: Partial<typeof organizations.$inferInsert> = {
      plan: active ? 'pro' : 'free',
      planUpdatedAt: new Date(),
    };
    if (params.subscriptionId) {
      update.stripeSubscriptionId = active ? params.subscriptionId : null;
    }
    if (params.customerId) {
      update.stripeCustomerId = params.customerId;
    }
    await this.db.update(organizations).set(update).where(eq(organizations.id, org.id));
    this.logger.log(
      `org_plan_updated org_id=${org.id} plan=${update.plan} subscription_status=${params.statusValue}`,
    );
  }

  /**
   * Best-effort webhook handling. The NestJS backend cannot verify the Stripe signature
   * (no SDK), so we parse the event body and update the same org plan columns FastAPI does
   * on subscription events. Always non-crashing; returns { status: 'ok' } to the caller.
   */
  async handleWebhook(body: unknown): Promise<{ status: string }> {
    try {
      const event = (body ?? {}) as Record<string, unknown>;
      const eventType = typeof event.type === 'string' ? event.type : '';
      const data = (event.data as Record<string, unknown> | undefined) ?? {};
      const dataObject = (data.object as Record<string, unknown> | undefined) ?? {};

      if (eventType === 'checkout.session.completed') {
        const metadata = (dataObject.metadata as Record<string, unknown> | undefined) ?? {};
        const orgRaw =
          (metadata.org_id as string | undefined) ??
          (dataObject.client_reference_id as string | undefined);
        if (!orgRaw) {
          return { status: 'ok' };
        }
        const subId = dataObject.subscription;
        const customerId = dataObject.customer;
        await this.applySubscriptionStatus({
          orgId: String(orgRaw),
          subscriptionId: subId ? String(subId) : null,
          statusValue: 'active',
          customerId: customerId ? String(customerId) : null,
        });
        return { status: 'ok' };
      }

      if (
        eventType === 'customer.subscription.updated' ||
        eventType === 'customer.subscription.deleted'
      ) {
        const metadata = (dataObject.metadata as Record<string, unknown> | undefined) ?? {};
        let orgId = metadata.org_id ? String(metadata.org_id) : '';
        if (!orgId) {
          const customerId = dataObject.customer ? String(dataObject.customer) : '';
          if (!customerId) {
            return { status: 'ok' };
          }
          const [org] = await this.db
            .select()
            .from(organizations)
            .where(eq(organizations.stripeCustomerId, customerId));
          if (!org) {
            return { status: 'ok' };
          }
          orgId = org.id;
        }
        const statusValue = eventType.endsWith('deleted')
          ? 'canceled'
          : String(dataObject.status ?? 'canceled');
        await this.applySubscriptionStatus({
          orgId,
          subscriptionId: dataObject.id ? String(dataObject.id) : null,
          statusValue,
          customerId: dataObject.customer ? String(dataObject.customer) : null,
        });
        return { status: 'ok' };
      }

      return { status: 'ok' };
    } catch (error) {
      // Never let webhook handling crash the endpoint.
      this.logger.error(
        `stripe_webhook_event_failed error=${error instanceof Error ? error.message : String(error)}`,
      );
      return { status: 'ok' };
    }
  }
}

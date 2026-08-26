import {
  ConflictException,
  ForbiddenException,
  Inject,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { and, desc, eq, gt, isNull } from 'drizzle-orm';
import { randomBytes, randomUUID } from 'crypto';

import { CurrentUser } from '../common/auth/current-user.interface';
import { Database, DRIZZLE } from '../database/database.module';
import {
  environments,
  organizations,
  OrganizationRow,
  orgInvites,
  OrgInviteRow,
  orgMembers,
  OrgMemberRow,
  orgSsoRoleMappings,
  OrgSsoRoleMappingRow,
  projects,
  users,
} from '../database/schema';

// Lowercase role vocabulary, mirroring FastAPI OrgRole enum (owner/admin/member).
export type OrgRole = 'owner' | 'admin' | 'member';
const ROLE_RANK: Record<OrgRole, number> = { member: 0, admin: 1, owner: 2 };

// Defaults mirrored from FastAPI settings (app/core/config.py).
const SOFT_COST_CAP = 25.0; // preview_soft_cost_cap
const INVITE_TTL_HOURS = 168; // invite_ttl_hours (7 days)
const ACTIVE_STATUSES = new Set(['RUNNING', 'PROVISIONING']);

export interface OrgMemberView {
  userId: string;
  email: string | null;
  displayName: string | null;
  role: string;
  createdAt: Date | null;
}

export interface OrgContext {
  org: OrganizationRow;
  membership: OrgMemberRow;
}

function normalizeRole(role: string | null | undefined, fallback: OrgRole = 'member'): OrgRole {
  const value = (role || '').toLowerCase();
  if (value === 'owner' || value === 'admin' || value === 'member') {
    return value;
  }
  return fallback;
}

function roleAtLeast(role: OrgRole, minimum: OrgRole): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[minimum];
}

function round4(value: number): number {
  return Math.round((value + Number.EPSILON) * 10000) / 10000;
}

@Injectable()
export class OrgsService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  async listForUser(user: CurrentUser): Promise<OrganizationRow[]> {
    const memberships = await this.db
      .select()
      .from(orgMembers)
      .where(eq(orgMembers.userId, user.userId));

    if (memberships.length === 0) {
      return [];
    }

    const orgs: OrganizationRow[] = [];
    for (const m of memberships) {
      const [o] = await this.db.select().from(organizations).where(eq(organizations.id, m.orgId));
      if (o) orgs.push(o);
    }
    return orgs;
  }

  /** Orgs with the caller's membership role, matching FastAPI OrganizationRead. */
  async listForUserWithRole(
    user: CurrentUser,
  ): Promise<Array<OrganizationRow & { role: string }>> {
    const rows = await this.db
      .select({ org: organizations, role: orgMembers.role })
      .from(orgMembers)
      .innerJoin(organizations, eq(organizations.id, orgMembers.orgId))
      .where(eq(orgMembers.userId, user.userId));
    return rows.map((r) => ({ ...r.org, role: r.role }));
  }

  async ensurePersonalOrg(user: { userId?: string; id?: string; email?: string; displayName?: string }): Promise<OrganizationRow> {
    const targetUserId = user.userId || user.id;
    if (!targetUserId) throw new Error('ensurePersonalOrg: userId or id is required');

    // 1. Return existing organization if user already has any org membership
    const memberships = await this.db
      .select({
        org: organizations,
      })
      .from(orgMembers)
      .innerJoin(organizations, eq(organizations.id, orgMembers.orgId))
      .where(eq(orgMembers.userId, targetUserId));

    if (memberships.length > 0) {
      return memberships[0].org;
    }

    const userEmail = user.email || 'user@local';
    const emailPrefix = userEmail.split('@')[0].toLowerCase().replace(/[^a-z0-9-]/g, '-');
    const orgId = randomUUID();
    const now = new Date();
    const displayName = user.displayName || emailPrefix;
    const orgName = `${displayName}'s org`;
    const slug = emailPrefix || `org-${orgId.substring(0, 8)}`;

    const [existingOrg] = await this.db
      .select()
      .from(organizations)
      .where(eq(organizations.slug, slug));

    if (existingOrg) {
      const existingMemberships = await this.db
        .select()
        .from(orgMembers)
        .where(eq(orgMembers.userId, targetUserId));

      if (existingMemberships.length === 0) {
        await this.db.insert(orgMembers).values({
          id: randomUUID(),
          orgId: existingOrg.id,
          userId: targetUserId,
          role: 'owner',
          createdAt: now,
        });
      }
      return existingOrg;
    }

    const [org] = await this.db
      .insert(organizations)
      .values({
        id: orgId,
        name: orgName,
        slug,
        plan: 'free',
        createdAt: now,
      })
      .returning();

    // 3. Insert owner membership in org_memberships
    await this.db.insert(orgMembers).values({
      id: randomUUID(),
      orgId: org.id,
      userId: targetUserId,
      role: 'owner',
      createdAt: now,
    });

    // 4. Create default project for the organization
    await this.db.insert(projects).values({
      id: randomUUID(),
      orgId: org.id,
      name: 'Default',
      slug: 'default',
      createdByUserId: targetUserId,
      createdAt: now,
      updatedAt: now,
    });

    return org;
  }

  async createOrg(user: CurrentUser, name: string): Promise<OrganizationRow> {
    const slug = name.toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/^-+|-+$/g, '');
    const id = randomUUID();
    const now = new Date();

    const [org] = await this.db
      .insert(organizations)
      .values({
        id,
        name,
        slug: slug || `org-${id.substring(0, 8)}`,
        plan: 'free',
        createdAt: now,
      })
      .returning();

    await this.db.insert(orgMembers).values({
      id: randomUUID(),
      orgId: org.id,
      userId: user.userId,
      role: 'owner',
      createdAt: now,
    });

    // Ensure default project exists for new org
    await this.db.insert(projects).values({
      id: randomUUID(),
      orgId: org.id,
      name: 'Default',
      slug: 'default',
      createdByUserId: user.userId,
      createdAt: now,
      updatedAt: now,
    });

    return org;
  }

  /**
   * Resolve the caller's membership + organization, mirroring FastAPI
   * OrganizationService.resolve_context. Raises 404 when the user is not a
   * member of the requested org.
   */
  async resolveContext(user: CurrentUser, orgId: string): Promise<OrgContext> {
    const [membership] = await this.db
      .select()
      .from(orgMembers)
      .where(and(eq(orgMembers.orgId, orgId), eq(orgMembers.userId, user.userId)));

    if (!membership) {
      throw new NotFoundException({ code: 'org_not_found', message: 'Organization not found' });
    }

    const [org] = await this.db.select().from(organizations).where(eq(organizations.id, orgId));
    if (!org) {
      throw new NotFoundException({ code: 'org_not_found', message: 'Organization not found' });
    }

    return { org, membership };
  }

  private actorRole(ctx: OrgContext): OrgRole {
    return normalizeRole(ctx.membership.role);
  }

  private requireAdmin(ctx: OrgContext, message = 'Admin role required'): void {
    if (!roleAtLeast(this.actorRole(ctx), 'admin')) {
      throw new ForbiddenException({ code: 'forbidden', message });
    }
  }

  private guardOwnerAssignment(ctx: OrgContext, targetRole: OrgRole, message: string): void {
    if (targetRole === 'owner' && this.actorRole(ctx) !== 'owner') {
      throw new ForbiddenException({ code: 'forbidden', message });
    }
  }

  // ----- Members ------------------------------------------------------------

  async listMembers(user: CurrentUser, orgId: string): Promise<OrgMemberView[]> {
    const ctx = await this.resolveContext(user, orgId);
    const rows = await this.db
      .select({
        userId: orgMembers.userId,
        role: orgMembers.role,
        createdAt: orgMembers.createdAt,
        email: users.email,
        displayName: users.displayName,
      })
      .from(orgMembers)
      .leftJoin(users, eq(users.id, orgMembers.userId))
      .where(eq(orgMembers.orgId, ctx.org.id))
      .orderBy(orgMembers.createdAt);

    return rows.map((r) => ({
      userId: r.userId,
      email: r.email ?? null,
      displayName: r.displayName ?? null,
      role: normalizeRole(r.role),
      createdAt: r.createdAt ?? null,
    }));
  }

  async updateMemberRole(
    user: CurrentUser,
    orgId: string,
    memberUserId: string,
    role: string,
  ): Promise<OrgMemberView> {
    const ctx = await this.resolveContext(user, orgId);
    this.requireAdmin(ctx);
    const targetRole = normalizeRole(role);
    this.guardOwnerAssignment(ctx, targetRole, 'Only owners can assign the owner role');

    const [membership] = await this.db
      .select()
      .from(orgMembers)
      .where(and(eq(orgMembers.orgId, ctx.org.id), eq(orgMembers.userId, memberUserId)));

    if (!membership) {
      throw new NotFoundException({ code: 'member_not_found', message: 'Member not found' });
    }

    if (normalizeRole(membership.role) === 'owner' && this.actorRole(ctx) !== 'owner') {
      throw new ForbiddenException({ code: 'forbidden', message: 'Cannot change an owner membership' });
    }

    await this.db
      .update(orgMembers)
      .set({ role: targetRole })
      .where(and(eq(orgMembers.orgId, ctx.org.id), eq(orgMembers.userId, memberUserId)));

    const [profile] = await this.db.select().from(users).where(eq(users.id, memberUserId));

    return {
      userId: memberUserId,
      email: profile?.email ?? null,
      displayName: profile?.displayName ?? null,
      role: targetRole,
      createdAt: membership.createdAt ?? null,
    };
  }

  async removeMember(user: CurrentUser, orgId: string, memberUserId: string): Promise<void> {
    const ctx = await this.resolveContext(user, orgId);
    this.requireAdmin(ctx);

    const [membership] = await this.db
      .select()
      .from(orgMembers)
      .where(and(eq(orgMembers.orgId, ctx.org.id), eq(orgMembers.userId, memberUserId)));

    if (!membership) {
      throw new NotFoundException({ code: 'member_not_found', message: 'Member not found' });
    }

    if (normalizeRole(membership.role) === 'owner') {
      throw new ForbiddenException({
        code: 'forbidden',
        message: 'Cannot remove an organization owner',
      });
    }

    await this.db
      .delete(orgMembers)
      .where(and(eq(orgMembers.orgId, ctx.org.id), eq(orgMembers.userId, memberUserId)));
  }

  // ----- Invites ------------------------------------------------------------

  async listInvites(user: CurrentUser, orgId: string): Promise<OrgInviteRow[]> {
    const ctx = await this.resolveContext(user, orgId);
    const now = new Date();
    return this.db
      .select()
      .from(orgInvites)
      .where(
        and(
          eq(orgInvites.orgId, ctx.org.id),
          isNull(orgInvites.acceptedAt),
          isNull(orgInvites.revokedAt),
          gt(orgInvites.expiresAt, now),
        ),
      )
      .orderBy(desc(orgInvites.createdAt));
  }

  async createInvite(
    user: CurrentUser,
    orgId: string,
    email: string,
    role: string,
  ): Promise<OrgInviteRow> {
    const ctx = await this.resolveContext(user, orgId);
    this.requireAdmin(ctx, 'Admin role required to invite members');
    const targetRole = normalizeRole(role);
    this.guardOwnerAssignment(ctx, targetRole, 'Only owners can invite as owner');

    const cleanedEmail = (email || '').trim().toLowerCase();

    // Reject when the email already belongs to a member of this org.
    const [existingUser] = await this.db.select().from(users).where(eq(users.email, cleanedEmail));
    if (existingUser) {
      const [existingMembership] = await this.db
        .select()
        .from(orgMembers)
        .where(and(eq(orgMembers.orgId, ctx.org.id), eq(orgMembers.userId, existingUser.id)));
      if (existingMembership) {
        throw new ConflictException({ code: 'already_member', message: 'User is already a member' });
      }
    }

    const now = new Date();

    // Revoke any still-pending invite for the same email/org.
    await this.db
      .update(orgInvites)
      .set({ revokedAt: now })
      .where(
        and(
          eq(orgInvites.orgId, ctx.org.id),
          eq(orgInvites.email, cleanedEmail),
          isNull(orgInvites.acceptedAt),
          isNull(orgInvites.revokedAt),
        ),
      );

    const expiresAt = new Date(now.getTime() + INVITE_TTL_HOURS * 60 * 60 * 1000);

    const [invite] = await this.db
      .insert(orgInvites)
      .values({
        id: randomUUID(),
        orgId: ctx.org.id,
        email: cleanedEmail,
        role: targetRole,
        tokenHash: randomBytes(32).toString('hex'),
        invitedByUserId: user.userId,
        expiresAt,
        createdAt: now,
      })
      .returning();

    return invite;
  }

  async revokeInvite(user: CurrentUser, orgId: string, inviteId: string): Promise<void> {
    const ctx = await this.resolveContext(user, orgId);
    this.requireAdmin(ctx);

    const [invite] = await this.db.select().from(orgInvites).where(eq(orgInvites.id, inviteId));
    if (!invite || invite.orgId !== ctx.org.id) {
      throw new NotFoundException({ code: 'invite_not_found', message: 'Invite not found' });
    }

    await this.db
      .update(orgInvites)
      .set({ revokedAt: new Date() })
      .where(eq(orgInvites.id, inviteId));
  }

  // ----- SSO role mappings --------------------------------------------------

  async listSsoMappings(user: CurrentUser, orgId: string): Promise<OrgSsoRoleMappingRow[]> {
    const ctx = await this.resolveContext(user, orgId);
    return this.db
      .select()
      .from(orgSsoRoleMappings)
      .where(eq(orgSsoRoleMappings.orgId, ctx.org.id))
      .orderBy(orgSsoRoleMappings.groupName);
  }

  async upsertSsoMapping(
    user: CurrentUser,
    orgId: string,
    groupName: string,
    role: string,
  ): Promise<OrgSsoRoleMappingRow> {
    const ctx = await this.resolveContext(user, orgId);
    this.requireAdmin(ctx);
    const targetRole = normalizeRole(role);
    this.guardOwnerAssignment(ctx, targetRole, 'Only owners can map groups to owner');

    const cleaned = (groupName || '').trim();
    if (!cleaned) {
      throw new ConflictException({ code: 'invalid_group', message: 'group_name is required' });
    }

    const [existing] = await this.db
      .select()
      .from(orgSsoRoleMappings)
      .where(
        and(eq(orgSsoRoleMappings.orgId, ctx.org.id), eq(orgSsoRoleMappings.groupName, cleaned)),
      );

    if (existing) {
      const [updated] = await this.db
        .update(orgSsoRoleMappings)
        .set({ role: targetRole })
        .where(eq(orgSsoRoleMappings.id, existing.id))
        .returning();
      return updated;
    }

    const [created] = await this.db
      .insert(orgSsoRoleMappings)
      .values({
        id: randomUUID(),
        orgId: ctx.org.id,
        groupName: cleaned,
        role: targetRole,
        createdAt: new Date(),
      })
      .returning();
    return created;
  }

  async deleteSsoMapping(user: CurrentUser, orgId: string, mappingId: string): Promise<void> {
    const ctx = await this.resolveContext(user, orgId);
    this.requireAdmin(ctx);

    const [mapping] = await this.db
      .select()
      .from(orgSsoRoleMappings)
      .where(eq(orgSsoRoleMappings.id, mappingId));

    if (!mapping || mapping.orgId !== ctx.org.id) {
      throw new NotFoundException({ code: 'mapping_not_found', message: 'SSO mapping not found' });
    }

    await this.db.delete(orgSsoRoleMappings).where(eq(orgSsoRoleMappings.id, mappingId));
  }

  // ----- Cost summary -------------------------------------------------------

  /**
   * Aggregate cost for an org's active environments, mirroring the shape of
   * FastAPI EnvironmentService.org_cost_summary. Since the Nest environments
   * table stores only cost_estimate_hourly (text), accrued cost is estimated
   * as hourly rate * hours since creation.
   */
  async orgCostSummary(user: CurrentUser, orgId: string) {
    const ctx = await this.resolveContext(user, orgId);
    const rows = await this.db
      .select()
      .from(environments)
      .where(eq(environments.orgId, ctx.org.id));

    const now = Date.now();
    const items: Array<{
      environment_id: string;
      name: string;
      status: string;
      provider: string | null;
      is_local: boolean;
      cost_estimate_hourly: number;
      cost_accrued: number;
    }> = [];

    let cloudAccrued = 0;
    let localAccrued = 0;
    let activeCount = 0;
    let cloudEnvironmentCount = 0;

    for (const row of rows) {
      if (!ACTIVE_STATUSES.has(row.status)) {
        continue;
      }

      const hourly = Number.parseFloat(row.costEstimateHourly ?? '0') || 0;
      const isLocal =
        (row.provider || 'local') === 'local' || (hourly === 0 && row.workspaceId == null);

      const createdMs = row.createdAt ? new Date(row.createdAt).getTime() : now;
      const hoursRunning = Math.max(0, (now - createdMs) / (60 * 60 * 1000));
      const accrued = round4(hourly * hoursRunning);

      activeCount += 1;
      items.push({
        environment_id: row.id,
        name: row.name,
        status: row.status,
        provider: row.provider ?? null,
        is_local: isLocal,
        cost_estimate_hourly: round4(hourly),
        cost_accrued: accrued,
      });

      if (isLocal) {
        localAccrued += accrued;
      } else {
        cloudEnvironmentCount += 1;
        cloudAccrued += accrued;
      }
    }

    cloudAccrued = round4(cloudAccrued);
    localAccrued = round4(localAccrued);

    return {
      org_id: ctx.org.id,
      soft_cost_cap: SOFT_COST_CAP,
      active_count: activeCount,
      cloud_environment_count: cloudEnvironmentCount,
      cloud_accrued: cloudAccrued,
      local_accrued: localAccrued,
      total_accrued: round4(cloudAccrued + localAccrued),
      soft_cost_cap_exceeded: cloudAccrued >= SOFT_COST_CAP && cloudEnvironmentCount > 0,
      environments: items,
    };
  }
}

import {
  ConflictException,
  ForbiddenException,
  GoneException,
  Inject,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { and, count, desc, eq, isNull } from 'drizzle-orm';
import { createHash, randomBytes, randomUUID } from 'crypto';

import { CurrentUser } from '../common/auth/current-user.interface';
import { Database, DRIZZLE } from '../database/database.module';
import {
  projectInvites,
  projectMemberships,
  projects,
  provisioningWorkspaces,
  users,
  ProjectInviteRow,
  ProjectMembershipRow,
  ProjectRow,
} from '../database/schema';

export interface CreateProjectDto {
  name: string;
  slug?: string;
  description?: string;
}

/** Ordered from lowest to highest privilege, mirroring FastAPI's OrgRole ladder. */
const ROLE_RANK: Record<string, number> = {
  viewer: 0,
  member: 1,
  admin: 2,
  owner: 3,
};

function roleAtLeast(role: string | null | undefined, minimum: string): boolean {
  const have = ROLE_RANK[(role ?? '').toLowerCase()] ?? -1;
  const need = ROLE_RANK[minimum] ?? 0;
  return have >= need;
}

/** Matches app/services/orgs.py hash_invite_token (sha256 hex). */
function hashInviteToken(token: string): string {
  return createHash('sha256').update(token, 'utf-8').digest('hex');
}

function inviteBaseUrl(): string {
  return (process.env.INVITE_BASE_URL ?? 'http://localhost:3000/invite').replace(/\/+$/, '');
}

function inviteTtlHours(): number {
  const raw = Number(process.env.INVITE_TTL_HOURS ?? 168);
  return Number.isFinite(raw) && raw > 0 ? raw : 168;
}

@Injectable()
export class ProjectsService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  async listForOrg(orgId?: string): Promise<ProjectRow[]> {
    if (!orgId) return [];
    return this.db.select().from(projects).where(eq(projects.orgId, orgId));
  }

  async create(user: CurrentUser, dto: CreateProjectDto): Promise<ProjectRow> {
    const orgId = user.orgId || user.userId;
    const slug = (dto.slug || dto.name)
      .toLowerCase()
      .replace(/[^a-z0-9-]/g, '-')
      .replace(/^-+|-+$/g, '');

    const id = randomUUID();
    const now = new Date();

    const [row] = await this.db
      .insert(projects)
      .values({
        id,
        orgId,
        name: dto.name,
        slug: slug || 'project',
        createdByUserId: user.userId,
        createdAt: now,
        updatedAt: now,
      })
      .returning();

    // Seed the creator as owner so member/invite endpoints have a member to gate on.
    await this.db.insert(projectMemberships).values({
      id: randomUUID(),
      projectId: row.id,
      userId: user.userId,
      role: 'owner',
      createdAt: now,
    });

    return row;
  }

  async getById(id: string): Promise<ProjectRow> {
    const [row] = await this.db.select().from(projects).where(eq(projects.id, id));
    if (!row) {
      throw new NotFoundException(`Project ${id} not found`);
    }
    return row;
  }

  async delete(id: string): Promise<void> {
    await this.db.delete(projects).where(eq(projects.id, id));
  }

  /** Count of provisioning workspaces attached to a project. */
  async workspaceCount(projectId: string): Promise<number> {
    const [row] = await this.db
      .select({ value: count() })
      .from(provisioningWorkspaces)
      .where(eq(provisioningWorkspaces.projectId, projectId));
    return Number(row?.value ?? 0);
  }

  async getMembershipRole(projectId: string, userId: string): Promise<string | null> {
    const [row] = await this.db
      .select({ role: projectMemberships.role })
      .from(projectMemberships)
      .where(
        and(
          eq(projectMemberships.projectId, projectId),
          eq(projectMemberships.userId, userId),
        ),
      )
      .limit(1);
    return row?.role ?? null;
  }

  /** Resolve the caller's effective role for a project, falling back to token org role. */
  async resolveActorRole(projectId: string, user: CurrentUser): Promise<string> {
    const membershipRole = await this.getMembershipRole(projectId, user.userId);
    return membershipRole ?? user.orgRole ?? 'owner';
  }

  async rename(projectId: string, actor: CurrentUser, name: string): Promise<ProjectRow> {
    const project = await this.getById(projectId);
    const actorRole = await this.resolveActorRole(projectId, actor);
    if (!roleAtLeast(actorRole, 'admin')) {
      throw new ForbiddenException('admin role required');
    }
    const [row] = await this.db
      .update(projects)
      .set({ name, updatedAt: new Date() })
      .where(eq(projects.id, project.id))
      .returning();
    return row;
  }

  async listMembers(
    projectId: string,
  ): Promise<Array<{ user_id: string; email: string; display_name: string; role: string }>> {
    await this.getById(projectId);
    const rows = await this.db
      .select({
        userId: projectMemberships.userId,
        role: projectMemberships.role,
        email: users.email,
        displayName: users.displayName,
      })
      .from(projectMemberships)
      .leftJoin(users, eq(users.id, projectMemberships.userId))
      .where(eq(projectMemberships.projectId, projectId))
      .orderBy(projectMemberships.createdAt);
    return rows.map((m) => ({
      user_id: m.userId,
      email: m.email ?? '',
      display_name: m.displayName ?? '',
      role: m.role,
    }));
  }

  async updateMemberRole(
    projectId: string,
    actor: CurrentUser,
    memberUserId: string,
    role: string,
  ): Promise<{ user_id: string; email: string; display_name: string; role: string }> {
    await this.getById(projectId);
    const actorRole = await this.resolveActorRole(projectId, actor);
    if (!roleAtLeast(actorRole, 'admin')) {
      throw new ForbiddenException('admin role required');
    }
    if (role === 'owner' && actorRole !== 'owner') {
      throw new ForbiddenException('Only owners can assign owner');
    }
    const membership = await this.findMembership(projectId, memberUserId);
    if (!membership) {
      throw new NotFoundException('Member not found');
    }
    if (membership.role === 'owner' && actorRole !== 'owner') {
      throw new ForbiddenException('Cannot change an owner membership');
    }
    await this.db
      .update(projectMemberships)
      .set({ role })
      .where(eq(projectMemberships.id, membership.id));

    const [profile] = await this.db
      .select({ email: users.email, displayName: users.displayName })
      .from(users)
      .where(eq(users.id, memberUserId))
      .limit(1);
    return {
      user_id: memberUserId,
      email: profile?.email ?? '',
      display_name: profile?.displayName ?? '',
      role,
    };
  }

  async createInvite(
    projectId: string,
    actor: CurrentUser,
    email: string,
    role: string,
  ): Promise<Record<string, unknown>> {
    const project = await this.getById(projectId);
    const actorRole = await this.resolveActorRole(projectId, actor);
    if (!roleAtLeast(actorRole, 'admin')) {
      throw new ForbiddenException('admin role required');
    }
    if (role === 'owner' && actorRole !== 'owner') {
      throw new ForbiddenException('Only owners can invite as owner');
    }
    const cleaned = email.trim().toLowerCase();

    const [existingUser] = await this.db
      .select({ id: users.id })
      .from(users)
      .where(eq(users.email, cleaned))
      .limit(1);
    if (existingUser) {
      const membership = await this.findMembership(projectId, existingUser.id);
      if (membership) {
        throw new ConflictException('User is already a member');
      }
    }

    // Revoke any pending invite for the same email on this project.
    const now = new Date();
    await this.db
      .update(projectInvites)
      .set({ revokedAt: now })
      .where(
        and(
          eq(projectInvites.projectId, projectId),
          eq(projectInvites.email, cleaned),
          isNull(projectInvites.acceptedAt),
          isNull(projectInvites.revokedAt),
        ),
      );

    const rawToken = randomBytes(32).toString('base64url');
    const expiresAt = new Date(now.getTime() + inviteTtlHours() * 3600 * 1000);
    const [invite] = await this.db
      .insert(projectInvites)
      .values({
        id: randomUUID(),
        projectId,
        email: cleaned,
        role,
        tokenHash: hashInviteToken(rawToken),
        invitedByUserId: actor.userId,
        expiresAt,
        createdAt: now,
      })
      .returning();

    const base = inviteBaseUrl();
    const inviteUrl = base.endsWith('/invite')
      ? `${base}/project/${rawToken}`
      : `${base}/invite/project/${rawToken}`;

    return {
      id: invite.id,
      project_id: invite.projectId,
      project_name: project.name,
      org_id: project.orgId,
      email: invite.email,
      role: invite.role,
      expires_at: invite.expiresAt,
      accepted_at: invite.acceptedAt,
      revoked_at: invite.revokedAt,
      created_at: invite.createdAt,
      invite_url: inviteUrl,
      email_sent: false,
      email_error: null,
    };
  }

  async listInvites(projectId: string): Promise<Array<Record<string, unknown>>> {
    const project = await this.getById(projectId);
    const rows = await this.db
      .select()
      .from(projectInvites)
      .where(
        and(eq(projectInvites.projectId, projectId), isNull(projectInvites.revokedAt)),
      )
      .orderBy(desc(projectInvites.createdAt));
    return rows.map((inv) => ({
      id: inv.id,
      project_id: inv.projectId,
      project_name: project.name,
      org_id: project.orgId,
      email: inv.email,
      role: inv.role,
      expires_at: inv.expiresAt,
      accepted_at: inv.acceptedAt,
      revoked_at: inv.revokedAt,
      created_at: inv.createdAt,
    }));
  }

  async revokeInvite(projectId: string, inviteId: string, actor: CurrentUser): Promise<void> {
    await this.getById(projectId);
    const actorRole = await this.resolveActorRole(projectId, actor);
    if (!roleAtLeast(actorRole, 'admin')) {
      throw new ForbiddenException('admin role required');
    }
    const [invite] = await this.db
      .select()
      .from(projectInvites)
      .where(eq(projectInvites.id, inviteId))
      .limit(1);
    if (!invite || invite.projectId !== projectId) {
      throw new NotFoundException('Invite not found');
    }
    await this.db
      .update(projectInvites)
      .set({ revokedAt: new Date() })
      .where(eq(projectInvites.id, inviteId));
  }

  async acceptInvite(user: CurrentUser, token: string): Promise<Record<string, unknown>> {
    const tokenHash = hashInviteToken(token.trim());
    const [invite] = await this.db
      .select()
      .from(projectInvites)
      .where(eq(projectInvites.tokenHash, tokenHash))
      .limit(1);
    if (!invite || invite.revokedAt) {
      throw new NotFoundException('Invite not found');
    }

    const project = await this.getById(invite.projectId);

    if ((user.email ?? '').trim().toLowerCase() !== invite.email.trim().toLowerCase()) {
      throw new ForbiddenException('Signed-in email does not match the invite');
    }

    const existing = await this.findMembership(project.id, user.userId);
    if (existing) {
      if (!invite.acceptedAt) {
        await this.db
          .update(projectInvites)
          .set({ acceptedAt: new Date() })
          .where(eq(projectInvites.id, invite.id));
      }
      return this.acceptResponse(project, existing.role);
    }

    if (invite.acceptedAt) {
      throw new ConflictException('Invite already accepted');
    }
    const expires =
      invite.expiresAt instanceof Date ? invite.expiresAt : new Date(invite.expiresAt);
    if (expires.getTime() < Date.now()) {
      throw new GoneException('Invite expired');
    }

    const now = new Date();
    await this.db.insert(projectMemberships).values({
      id: randomUUID(),
      projectId: project.id,
      userId: user.userId,
      role: invite.role,
      createdAt: now,
    });
    await this.db
      .update(projectInvites)
      .set({ acceptedAt: now })
      .where(eq(projectInvites.id, invite.id));

    return this.acceptResponse(project, invite.role);
  }

  private async acceptResponse(
    project: ProjectRow,
    role: string,
  ): Promise<Record<string, unknown>> {
    const [org] = await this.db
      .select({ name: users.displayName })
      .from(projects)
      .where(eq(projects.id, project.id))
      .limit(1);
    // Org name best-effort; fall back to a stable label like FastAPI.
    return {
      project_id: project.id,
      project_name: project.name,
      org_id: project.orgId,
      org_name: org?.name ?? 'Organization',
      role,
    };
  }

  private async findMembership(
    projectId: string,
    userId: string,
  ): Promise<ProjectMembershipRow | undefined> {
    const [row] = await this.db
      .select()
      .from(projectMemberships)
      .where(
        and(
          eq(projectMemberships.projectId, projectId),
          eq(projectMemberships.userId, userId),
        ),
      )
      .limit(1);
    return row;
  }
}

// Keep types referenced for downstream consumers.
export type { ProjectInviteRow };

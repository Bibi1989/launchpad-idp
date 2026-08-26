import {
  ConflictException,
  ForbiddenException,
  Inject,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { and, eq, gt, isNull } from 'drizzle-orm';
import { randomUUID } from 'crypto';

import { CurrentUser } from '../common/auth/current-user.interface';
import { Database, DRIZZLE } from '../database/database.module';
import {
  organizations,
  orgInvites,
  orgMembers,
  projectInvites,
  projectMemberships,
  projects,
  users,
} from '../database/schema';

/**
 * Authenticated invite inbox, mirroring apps/api/app/routers/invites.py:
 * list pending org+project invites for the current user (matched by email) and
 * accept an invite by its id.
 */
@Injectable()
export class InvitesService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  private normalizedEmail(user: CurrentUser): string {
    return (user.email ?? '').trim().toLowerCase();
  }

  async listPending(user: CurrentUser): Promise<any[]> {
    const email = this.normalizedEmail(user);
    if (!email) return [];
    const now = new Date();
    const rows: any[] = [];

    const orgRows = await this.db
      .select({
        invite: orgInvites,
        orgName: organizations.name,
        inviterName: users.displayName,
        inviterEmail: users.email,
      })
      .from(orgInvites)
      .leftJoin(organizations, eq(organizations.id, orgInvites.orgId))
      .leftJoin(users, eq(users.id, orgInvites.invitedByUserId))
      .where(
        and(
          eq(orgInvites.email, email),
          isNull(orgInvites.acceptedAt),
          isNull(orgInvites.revokedAt),
          gt(orgInvites.expiresAt, now),
        ),
      );
    for (const r of orgRows) {
      rows.push({
        kind: 'org',
        invite_id: r.invite.id,
        role: r.invite.role,
        org_id: r.invite.orgId,
        org_name: r.orgName ?? 'Organization',
        project_id: null,
        project_name: null,
        invited_by: r.inviterName || r.inviterEmail || null,
        expires_at: r.invite.expiresAt,
        created_at: r.invite.createdAt,
        href: `/invite/accept/org/${r.invite.id}`,
      });
    }

    const projRows = await this.db
      .select({
        invite: projectInvites,
        projectName: projects.name,
        orgId: projects.orgId,
        orgName: organizations.name,
        inviterName: users.displayName,
        inviterEmail: users.email,
      })
      .from(projectInvites)
      .leftJoin(projects, eq(projects.id, projectInvites.projectId))
      .leftJoin(organizations, eq(organizations.id, projects.orgId))
      .leftJoin(users, eq(users.id, projectInvites.invitedByUserId))
      .where(
        and(
          eq(projectInvites.email, email),
          isNull(projectInvites.acceptedAt),
          isNull(projectInvites.revokedAt),
          gt(projectInvites.expiresAt, now),
        ),
      );
    for (const r of projRows) {
      if (!r.projectName) continue;
      rows.push({
        kind: 'project',
        invite_id: r.invite.id,
        role: r.invite.role,
        org_id: r.orgId,
        org_name: r.orgName ?? 'Organization',
        project_id: r.invite.projectId,
        project_name: r.projectName,
        invited_by: r.inviterName || r.inviterEmail || null,
        expires_at: r.invite.expiresAt,
        created_at: r.invite.createdAt,
        href: `/invite/accept/project/${r.invite.id}`,
      });
    }

    rows.sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
    return rows;
  }

  async acceptOrgInvite(user: CurrentUser, inviteId: string): Promise<any> {
    const email = this.normalizedEmail(user);
    const [invite] = await this.db
      .select()
      .from(orgInvites)
      .where(eq(orgInvites.id, inviteId))
      .limit(1);
    if (!invite || invite.revokedAt) {
      throw new NotFoundException('Invite not found');
    }
    if (invite.email.trim().toLowerCase() !== email) {
      throw new ForbiddenException('Signed-in email does not match the invite');
    }
    if (invite.expiresAt && new Date(invite.expiresAt).getTime() < Date.now()) {
      throw new ForbiddenException('Invite has expired');
    }

    const [org] = await this.db
      .select()
      .from(organizations)
      .where(eq(organizations.id, invite.orgId))
      .limit(1);
    if (!org) throw new NotFoundException('Organization not found');

    // Idempotent: reuse an existing membership; otherwise create one.
    const [existing] = await this.db
      .select()
      .from(orgMembers)
      .where(and(eq(orgMembers.orgId, invite.orgId), eq(orgMembers.userId, user.userId)))
      .limit(1);

    let role = invite.role;
    if (existing) {
      role = existing.role;
    } else {
      await this.db.insert(orgMembers).values({
        id: randomUUID(),
        orgId: invite.orgId,
        userId: user.userId,
        role: invite.role,
        createdAt: new Date(),
      });
    }
    if (!invite.acceptedAt) {
      await this.db
        .update(orgInvites)
        .set({ acceptedAt: new Date() })
        .where(eq(orgInvites.id, invite.id));
    } else if (!existing) {
      throw new ConflictException('Invite already accepted');
    }

    return {
      user_id: user.userId,
      email: user.email,
      display_name: user.email,
      role,
      org_id: org.id,
      org_name: org.name,
    };
  }

  async acceptProjectInvite(user: CurrentUser, inviteId: string): Promise<any> {
    const email = this.normalizedEmail(user);
    const [invite] = await this.db
      .select()
      .from(projectInvites)
      .where(eq(projectInvites.id, inviteId))
      .limit(1);
    if (!invite || invite.revokedAt) {
      throw new NotFoundException('Invite not found');
    }
    if (invite.email.trim().toLowerCase() !== email) {
      throw new ForbiddenException('Signed-in email does not match the invite');
    }
    if (invite.expiresAt && new Date(invite.expiresAt).getTime() < Date.now()) {
      throw new ForbiddenException('Invite has expired');
    }

    const [project] = await this.db
      .select()
      .from(projects)
      .where(eq(projects.id, invite.projectId))
      .limit(1);
    if (!project) throw new NotFoundException('Project not found');

    const [org] = await this.db
      .select()
      .from(organizations)
      .where(eq(organizations.id, project.orgId))
      .limit(1);

    const [existing] = await this.db
      .select()
      .from(projectMemberships)
      .where(
        and(
          eq(projectMemberships.projectId, invite.projectId),
          eq(projectMemberships.userId, user.userId),
        ),
      )
      .limit(1);

    let role = invite.role;
    if (existing) {
      role = existing.role;
    } else {
      await this.db.insert(projectMemberships).values({
        id: randomUUID(),
        projectId: invite.projectId,
        userId: user.userId,
        role: invite.role,
        createdAt: new Date(),
      });
    }
    if (!invite.acceptedAt) {
      await this.db
        .update(projectInvites)
        .set({ acceptedAt: new Date() })
        .where(eq(projectInvites.id, invite.id));
    } else if (!existing) {
      throw new ConflictException('Invite already accepted');
    }

    return {
      project_id: project.id,
      project_name: project.name,
      org_id: project.orgId,
      org_name: org?.name ?? 'Organization',
      role,
    };
  }
}

import {
  Body,
  Controller,
  Delete,
  Get,
  HttpCode,
  HttpStatus,
  Param,
  Patch,
  Post,
  UseGuards,
} from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';

import { AuthUser } from '../common/auth/current-user.decorator';
import { CurrentUser } from '../common/auth/current-user.interface';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { OrgInviteRow, OrgSsoRoleMappingRow } from '../database/schema';
import { OrgMemberView, OrgsService } from './orgs.service';

function toIso(value: Date | string | null | undefined): string | null {
  if (!value) return null;
  return value instanceof Date ? value.toISOString() : new Date(value).toISOString();
}

@ApiTags('orgs')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller('orgs')
export class OrgsController {
  constructor(private readonly service: OrgsService) {}

  @Get()
  async list(@AuthUser() user: CurrentUser) {
    // Match FastAPI OrganizationRead: {id, slug, name, role, plan, created_at}.
    const orgs = await this.service.listForUserWithRole(user);
    return orgs.map((o) => ({
      id: o.id,
      slug: o.slug,
      name: o.name,
      role: o.role,
      plan: o.plan,
      created_at: o.createdAt,
    }));
  }

  @Post()
  @HttpCode(HttpStatus.CREATED)
  async create(@Body() body: { name: string }, @AuthUser() user: CurrentUser) {
    const o = await this.service.createOrg(user, body.name);
    // Match OrganizationRead {id, slug, name, role, plan, created_at}. The creator is
    // the owner. Legacy keys (is_personal/updated_at) kept additively for non-breakage.
    return {
      id: o.id,
      slug: o.slug,
      name: o.name,
      role: 'owner',
      plan: o.plan,
      created_at: o.createdAt,
      is_personal: false,
      updated_at: o.createdAt,
    };
  }

  @Get(':orgId/costs')
  async getOrgCosts(@Param('orgId') orgId: string, @AuthUser() user: CurrentUser) {
    return this.service.orgCostSummary(user, orgId);
  }

  @Get(':orgId/members')
  async getMembers(@Param('orgId') orgId: string, @AuthUser() user: CurrentUser) {
    const members = await this.service.listMembers(user, orgId);
    return members.map((m) => this.serializeMember(m));
  }

  @Patch(':orgId/members/:memberUserId')
  async updateMemberRole(
    @Param('orgId') orgId: string,
    @Param('memberUserId') memberUserId: string,
    @Body() body: { role: string },
    @AuthUser() user: CurrentUser,
  ) {
    const member = await this.service.updateMemberRole(user, orgId, memberUserId, body.role);
    return this.serializeMember(member);
  }

  @Delete(':orgId/members/:memberUserId')
  @HttpCode(HttpStatus.NO_CONTENT)
  async removeMember(
    @Param('orgId') orgId: string,
    @Param('memberUserId') memberUserId: string,
    @AuthUser() user: CurrentUser,
  ) {
    await this.service.removeMember(user, orgId, memberUserId);
  }

  @Get(':orgId/invites')
  async getInvites(@Param('orgId') orgId: string, @AuthUser() user: CurrentUser) {
    const invites = await this.service.listInvites(user, orgId);
    return invites.map((i) => this.serializeInvite(i));
  }

  @Post(':orgId/invites')
  @HttpCode(HttpStatus.CREATED)
  async createInvite(
    @Param('orgId') orgId: string,
    @Body() body: { email: string; role?: string },
    @AuthUser() user: CurrentUser,
  ) {
    const invite = await this.service.createInvite(user, orgId, body.email, body.role || 'member');
    return this.serializeInvite(invite);
  }

  @Delete(':orgId/invites/:inviteId')
  @HttpCode(HttpStatus.NO_CONTENT)
  async deleteInvite(
    @Param('orgId') orgId: string,
    @Param('inviteId') inviteId: string,
    @AuthUser() user: CurrentUser,
  ) {
    await this.service.revokeInvite(user, orgId, inviteId);
  }

  @Get(':orgId/sso-mappings')
  async getSsoMappings(@Param('orgId') orgId: string, @AuthUser() user: CurrentUser) {
    const rows = await this.service.listSsoMappings(user, orgId);
    return rows.map((r) => this.serializeSsoMapping(r));
  }

  @Post(':orgId/sso-mappings')
  @HttpCode(HttpStatus.CREATED)
  async createSsoMapping(
    @Param('orgId') orgId: string,
    @Body() body: { group_name?: string; groupName?: string; role?: string },
    @AuthUser() user: CurrentUser,
  ) {
    const groupName = body.group_name ?? body.groupName ?? '';
    const row = await this.service.upsertSsoMapping(user, orgId, groupName, body.role || 'member');
    return this.serializeSsoMapping(row);
  }

  @Delete(':orgId/sso-mappings/:mappingId')
  @HttpCode(HttpStatus.NO_CONTENT)
  async deleteSsoMapping(
    @Param('orgId') orgId: string,
    @Param('mappingId') mappingId: string,
    @AuthUser() user: CurrentUser,
  ) {
    await this.service.deleteSsoMapping(user, orgId, mappingId);
  }

  private serializeMember(member: OrgMemberView) {
    return {
      user_id: member.userId,
      email: member.email,
      display_name: member.displayName,
      role: member.role,
      created_at: toIso(member.createdAt),
    };
  }

  private serializeInvite(invite: OrgInviteRow) {
    // Mirror OrgInviteRead. Never expose token_hash. org_name/invite_url are null:
    // this control plane stores only a token hash (no recoverable raw token), so a
    // shareable invite_url would need the raw-token flow projects uses. email_sent is
    // false (no mailer wired). Shape matches so the SPA never reads undefined.
    return {
      id: invite.id,
      org_id: invite.orgId,
      org_name: null,
      email: invite.email,
      role: invite.role,
      expires_at: toIso(invite.expiresAt),
      accepted_at: toIso(invite.acceptedAt),
      revoked_at: toIso(invite.revokedAt),
      created_at: toIso(invite.createdAt),
      invite_url: null,
      email_sent: false,
      email_error: null,
    };
  }

  private serializeSsoMapping(row: OrgSsoRoleMappingRow) {
    return {
      id: row.id,
      org_id: row.orgId,
      group_name: row.groupName,
      role: row.role,
      created_at: toIso(row.createdAt),
    };
  }
}

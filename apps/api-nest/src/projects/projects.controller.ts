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
import { CreateProjectDto, ProjectsService } from './projects.service';

@ApiTags('projects')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller('projects')
export class ProjectsController {
  constructor(private readonly service: ProjectsService) {}

  @Get()
  async list(@AuthUser() user: CurrentUser) {
    const rows = await this.service.listForOrg(user.orgId);
    return rows.map((p) => ({
      id: p.id,
      org_id: p.orgId,
      name: p.name,
      slug: p.slug,
      role: 'owner',
      workspace_count: 0,
      created_at: p.createdAt,
      updated_at: p.updatedAt,
    }));
  }

  @Post()
  @HttpCode(HttpStatus.CREATED)
  async create(@Body() body: CreateProjectDto, @AuthUser() user: CurrentUser) {
    const p = await this.service.create(user, body);
    return {
      id: p.id,
      org_id: p.orgId,
      name: p.name,
      slug: p.slug,
      role: 'owner',
      workspace_count: 0,
      created_at: p.createdAt,
      updated_at: p.updatedAt,
    };
  }

  // Accept a project invite by token. Declared before the :id routes so the static
  // 'invites/accept' segment is not shadowed by ':id'.
  @Post('invites/accept')
  async acceptInvite(@Body() body: { token: string }, @AuthUser() user: CurrentUser) {
    return this.service.acceptInvite(user, body?.token ?? '');
  }

  @Get(':id')
  async getOne(@Param('id') id: string, @AuthUser() user: CurrentUser) {
    const p = await this.service.getById(id);
    const [role, workspaceCount] = await Promise.all([
      this.service.resolveActorRole(id, user),
      this.service.workspaceCount(id),
    ]);
    return {
      id: p.id,
      org_id: p.orgId,
      name: p.name,
      slug: p.slug,
      role,
      workspace_count: workspaceCount,
      created_at: p.createdAt,
      updated_at: p.updatedAt,
    };
  }

  @Patch(':id')
  async rename(
    @Param('id') id: string,
    @Body() body: { name: string },
    @AuthUser() user: CurrentUser,
  ) {
    const p = await this.service.rename(id, user, body?.name ?? '');
    const workspaceCount = await this.service.workspaceCount(id);
    return {
      id: p.id,
      org_id: p.orgId,
      name: p.name,
      slug: p.slug,
      role: await this.service.resolveActorRole(id, user),
      workspace_count: workspaceCount,
      created_at: p.createdAt,
      updated_at: p.updatedAt,
    };
  }

  @Get(':id/members')
  async listMembers(@Param('id') id: string) {
    return this.service.listMembers(id);
  }

  @Patch(':id/members/:memberUserId')
  async updateMember(
    @Param('id') id: string,
    @Param('memberUserId') memberUserId: string,
    @Body() body: { role: string },
    @AuthUser() user: CurrentUser,
  ) {
    return this.service.updateMemberRole(id, user, memberUserId, body?.role ?? 'member');
  }

  @Get(':id/invites')
  async listInvites(@Param('id') id: string) {
    return this.service.listInvites(id);
  }

  @Post(':id/invites')
  @HttpCode(HttpStatus.CREATED)
  async createInvite(
    @Param('id') id: string,
    @Body() body: { email: string; role?: string },
    @AuthUser() user: CurrentUser,
  ) {
    return this.service.createInvite(id, user, body?.email ?? '', body?.role ?? 'member');
  }

  @Delete(':id/invites/:inviteId')
  @HttpCode(HttpStatus.NO_CONTENT)
  async revokeInvite(
    @Param('id') id: string,
    @Param('inviteId') inviteId: string,
    @AuthUser() user: CurrentUser,
  ) {
    await this.service.revokeInvite(id, inviteId, user);
  }

  @Delete(':id')
  @HttpCode(HttpStatus.NO_CONTENT)
  async remove(@Param('id') id: string) {
    await this.service.delete(id);
  }
}

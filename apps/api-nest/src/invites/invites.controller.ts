import { Controller, Get, Param, Post, UseGuards } from '@nestjs/common';
import { ApiBearerAuth, ApiOperation, ApiTags } from '@nestjs/swagger';

import { AuthUser } from '../common/auth/current-user.decorator';
import { CurrentUser } from '../common/auth/current-user.interface';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { InvitesService } from './invites.service';

@ApiTags('invites')
@Controller('invites')
@UseGuards(JwtAuthGuard)
@ApiBearerAuth()
export class InvitesController {
  constructor(private readonly invitesService: InvitesService) {}

  @Get('pending')
  @ApiOperation({ summary: 'List all pending organization and project invites for current user' })
  listPending(@AuthUser() user: CurrentUser): Promise<any[]> {
    return this.invitesService.listPending(user);
  }

  @Post('org/:id/accept')
  @ApiOperation({ summary: 'Accept an organization invitation by ID' })
  acceptOrgInvite(
    @Param('id') inviteId: string,
    @AuthUser() user: CurrentUser,
  ): Promise<any> {
    return this.invitesService.acceptOrgInvite(user, inviteId);
  }

  @Post('project/:id/accept')
  @ApiOperation({ summary: 'Accept a project invitation by ID' })
  acceptProjectInvite(
    @Param('id') inviteId: string,
    @AuthUser() user: CurrentUser,
  ): Promise<any> {
    return this.invitesService.acceptProjectInvite(user, inviteId);
  }
}

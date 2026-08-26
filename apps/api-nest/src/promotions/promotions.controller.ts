import { Body, Controller, Get, Param, Patch, Post, Query, UseGuards } from '@nestjs/common';
import { ApiBearerAuth, ApiOperation, ApiTags } from '@nestjs/swagger';

import { AuthUser } from '../common/auth/current-user.decorator';
import { CurrentUser } from '../common/auth/current-user.interface';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { PromotionsService } from './promotions.service';

@ApiTags('promotions')
@Controller()
@UseGuards(JwtAuthGuard)
@ApiBearerAuth()
export class PromotionsController {
  constructor(private readonly promotionsService: PromotionsService) {}

  @Get('orgs/:orgId/promotion-policy')
  @ApiOperation({ summary: 'Get promotion policy for an organization' })
  getPolicy(@Param('orgId') orgId: string): Promise<any> {
    return this.promotionsService.getPolicy(orgId);
  }

  @Patch('orgs/:orgId/promotion-policy')
  @ApiOperation({ summary: 'Update promotion policy for an organization' })
  updatePolicy(@Param('orgId') orgId: string, @Body() payload: any): Promise<any> {
    return this.promotionsService.updatePolicy(orgId, payload);
  }

  @Get('orgs/:orgId/promotions')
  @ApiOperation({ summary: 'List promotion requests for an organization' })
  listPromotions(
    @Param('orgId') orgId: string,
    @Query('status') status?: string,
  ): Promise<any[]> {
    return this.promotionsService.listForOrg(orgId, status);
  }

  @Post('environments/:id/stage-promote')
  @ApiOperation({ summary: 'Promote an environment to next lifecycle stage' })
  stagePromote(
    @Param('id') envId: string,
    @Body() payload: any,
    @AuthUser() user: CurrentUser,
  ): Promise<any> {
    return this.promotionsService.stagePromote(envId, payload, user);
  }

  @Post('promotions/:id/approve')
  @ApiOperation({ summary: 'Approve a pending promotion request' })
  approvePromotion(
    @Param('id') promotionId: string,
    @Body() payload: any,
    @AuthUser() user: CurrentUser,
  ): Promise<any> {
    return this.promotionsService.approve(promotionId, payload, user);
  }

  @Post('promotions/:id/reject')
  @ApiOperation({ summary: 'Reject a pending promotion request' })
  rejectPromotion(
    @Param('id') promotionId: string,
    @Body() payload: any,
    @AuthUser() user: CurrentUser,
  ): Promise<any> {
    return this.promotionsService.reject(promotionId, payload, user);
  }
}

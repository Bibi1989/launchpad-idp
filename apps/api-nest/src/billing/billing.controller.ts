import { Body, Controller, Get, Param, Post, UseGuards } from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';

import { AuthUser } from '../common/auth/current-user.decorator';
import { CurrentUser } from '../common/auth/current-user.interface';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { BillingService } from './billing.service';

@ApiTags('billing')
@Controller('billing')
export class BillingController {
  constructor(private readonly billing: BillingService) {}

  @UseGuards(JwtAuthGuard)
  @ApiBearerAuth()
  @Get('plans')
  getPlans() {
    return this.billing.getPlans();
  }

  @UseGuards(JwtAuthGuard)
  @ApiBearerAuth()
  @Get('usage')
  getUsage(@AuthUser() user: CurrentUser) {
    return this.billing.getUsage(user);
  }

  @UseGuards(JwtAuthGuard)
  @ApiBearerAuth()
  @Get('orgs/:orgId/plan')
  getOrgPlan(@Param('orgId') orgId: string) {
    return this.billing.getOrgPlan(orgId);
  }

  @UseGuards(JwtAuthGuard)
  @ApiBearerAuth()
  @Post('orgs/:orgId/checkout')
  createCheckout(@Param('orgId') orgId: string) {
    return this.billing.createCheckout(orgId);
  }

  @UseGuards(JwtAuthGuard)
  @ApiBearerAuth()
  @Post('orgs/:orgId/portal')
  createPortal(@Param('orgId') orgId: string) {
    return this.billing.createPortal(orgId);
  }

  // Stripe webhook: public (no auth guard), like the FastAPI webhook endpoint.
  @Post('webhook')
  stripeWebhook(@Body() body: any) {
    return this.billing.handleWebhook(body);
  }
}

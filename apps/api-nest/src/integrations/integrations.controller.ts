import {
  Body,
  Controller,
  Delete,
  Get,
  Param,
  Put,
  UseGuards,
} from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';

import { AuthUser } from '../common/auth/current-user.decorator';
import { CurrentUser } from '../common/auth/current-user.interface';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import {
  IntegrationsService,
  JiraIntegrationStatus,
  JiraIntegrationUpdate,
  SlackIntegrationStatus,
  SlackIntegrationUpdate,
} from './integrations.service';

@ApiTags('integrations')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller('integrations')
export class IntegrationsController {
  constructor(private readonly integrations: IntegrationsService) {}

  @Get()
  listIntegrations(@AuthUser() user: CurrentUser) {
    return this.integrations.getSummary(user);
  }

  @Get('orgs/:orgId/slack')
  getSlack(
    @AuthUser() user: CurrentUser,
    @Param('orgId') orgId: string,
  ): Promise<SlackIntegrationStatus> {
    return this.integrations.getSlack(user, orgId);
  }

  @Put('orgs/:orgId/slack')
  upsertSlack(
    @AuthUser() user: CurrentUser,
    @Param('orgId') orgId: string,
    @Body() body: SlackIntegrationUpdate,
  ): Promise<SlackIntegrationStatus> {
    return this.integrations.upsertSlack(user, orgId, body ?? {});
  }

  @Delete('orgs/:orgId/slack')
  disconnectSlack(
    @AuthUser() user: CurrentUser,
    @Param('orgId') orgId: string,
  ): Promise<SlackIntegrationStatus> {
    return this.integrations.disconnectSlack(user, orgId);
  }

  @Get('orgs/:orgId/jira')
  getJira(
    @AuthUser() user: CurrentUser,
    @Param('orgId') orgId: string,
  ): Promise<JiraIntegrationStatus> {
    return this.integrations.getJira(user, orgId);
  }

  @Put('orgs/:orgId/jira')
  upsertJira(
    @AuthUser() user: CurrentUser,
    @Param('orgId') orgId: string,
    @Body() body: JiraIntegrationUpdate,
  ): Promise<JiraIntegrationStatus> {
    return this.integrations.upsertJira(user, orgId, body ?? {});
  }

  @Delete('orgs/:orgId/jira')
  disconnectJira(
    @AuthUser() user: CurrentUser,
    @Param('orgId') orgId: string,
  ): Promise<JiraIntegrationStatus> {
    return this.integrations.disconnectJira(user, orgId);
  }
}

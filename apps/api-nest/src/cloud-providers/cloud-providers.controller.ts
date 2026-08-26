import { Controller, Get, Param, UseGuards } from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';

import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { AuthUser } from '../common/auth/current-user.decorator';
import type { CurrentUser } from '../common/auth/current-user.interface';
import { mergeCatalog } from './data/service-plugins';
import { PluginsService } from '../plugins/plugins.service';
import { CloudProvidersService } from './cloud-providers.service';

/**
 * Mirrors the FastAPI cloud-provider catalog routes. The global prefix (api/v1) is
 * applied in main.ts, so these map to:
 *   GET /api/v1/cloud-providers
 *   GET /api/v1/cloud-providers/:id
 *   GET /api/v1/cloud-providers/:id/services
 *   GET /api/v1/cloud-providers/:id/tools
 *   GET /api/v1/provisioning-tools
 */
@ApiTags('cloud-providers')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller()
export class CloudProvidersController {
  constructor(
    private readonly service: CloudProvidersService,
    private readonly plugins: PluginsService,
  ) {}

  @Get('cloud-providers')
  async listProviders(@AuthUser() user: CurrentUser) {
    const builtin = this.service.listProviders();
    if (!user?.orgId) return builtin;
    const extra = await this.plugins.catalogEntries(user.orgId, user.userId);
    return mergeCatalog(builtin, extra);
  }

  @Get('provisioning-tools')
  listProvisioningTools() {
    return this.service.listAllTools();
  }

  @Get('cloud-providers/:providerId')
  getProvider(@Param('providerId') providerId: string) {
    return this.service.getProvider(providerId);
  }

  @Get('cloud-providers/:providerId/services')
  getProviderServices(@Param('providerId') providerId: string) {
    return this.service.getServices(providerId);
  }

  @Get('cloud-providers/:providerId/tools')
  getProviderTools(@Param('providerId') providerId: string) {
    return this.service.getTools(providerId);
  }
}

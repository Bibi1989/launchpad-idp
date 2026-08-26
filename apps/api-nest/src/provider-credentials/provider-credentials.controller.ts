import {
  Body,
  Controller,
  Delete,
  Get,
  NotFoundException,
  Param,
  Put,
  Post,
  UseGuards,
} from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';

import { AuthUser } from '../common/auth/current-user.decorator';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import type { CurrentUser } from '../common/auth/current-user.interface';
import { findProvider } from '../cloud-providers/data/provider-catalog';
import { ProviderCredentialsService } from './provider-credentials.service';

class UpdateCredentialsDto {
  credentials: Record<string, string> = {};
}

class ValidateCredentialsDto {
  credentials?: Record<string, string> | null;
}

/**
 * NestJS port of the credential + validate routes in the FastAPI cloud-providers router.
 * Same paths, same JSON. Credentials are stored encrypted in the shared vault table.
 */
@ApiTags('cloud-providers')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller('cloud-providers')
export class ProviderCredentialsController {
  constructor(private readonly service: ProviderCredentialsService) {}

  @Get(':providerId/credentials')
  async getStatus(
    @Param('providerId') providerId: string,
    @AuthUser() user: CurrentUser,
  ): Promise<Record<string, string[]>> {
    this.requireKnown(providerId);
    const status = await this.service.statusForUser(user.userId);
    return { [providerId]: status[providerId] ?? [] };
  }

  @Put(':providerId/credentials')
  async upsert(
    @Param('providerId') providerId: string,
    @Body() body: UpdateCredentialsDto,
    @AuthUser() user: CurrentUser,
  ): Promise<Record<string, string[]>> {
    this.requireKnown(providerId);
    return this.service.upsertProvider(user.userId, providerId, body.credentials ?? {});
  }

  @Delete(':providerId/credentials')
  async remove(
    @Param('providerId') providerId: string,
    @AuthUser() user: CurrentUser,
  ): Promise<Record<string, string[]>> {
    this.requireKnown(providerId);
    return this.service.deleteProvider(user.userId, providerId);
  }

  @Post(':providerId/validate')
  async validate(
    @Param('providerId') providerId: string,
    @Body() body: ValidateCredentialsDto,
    @AuthUser() user: CurrentUser,
  ): Promise<{ valid: boolean; message: string | null }> {
    const provider = this.requireKnown(providerId);
    const credentials =
      body.credentials ?? (await this.service.getForProvider(user.userId, providerId));
    if (!credentials || Object.keys(credentials).length === 0) {
      return { valid: false, message: 'No credentials provided or stored.' };
    }
    // Structural validation: every required field must be present. (Live network
    // validation is done by the FastAPI adapters; that port comes with the adapters.)
    const missing = provider.credential_fields
      .filter((field) => field.required)
      .filter((field) => !String(credentials[field.name] ?? '').trim())
      .map((field) => field.name);
    if (missing.length > 0) {
      return { valid: false, message: `Missing required field(s): ${missing.join(', ')}` };
    }
    return { valid: true, message: null };
  }

  private requireKnown(providerId: string) {
    const provider = findProvider(providerId);
    if (!provider) throw new NotFoundException(`unknown provider '${providerId}'`);
    return provider;
  }
}

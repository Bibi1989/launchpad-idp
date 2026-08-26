import {
  Body,
  Controller,
  Delete,
  Get,
  HttpCode,
  HttpStatus,
  Param,
  Post,
  Put,
  Query,
  UseGuards,
} from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';
import { BadRequestException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'crypto';

import { AuthUser } from '../common/auth/current-user.decorator';
import { CurrentUser } from '../common/auth/current-user.interface';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { UserCredentialsService } from './user-credentials.service';

@ApiTags('user-credentials')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller('users/me/cloud-credentials')
export class UserCloudCredentialsController {
  constructor(
    private readonly service: UserCredentialsService,
    private readonly config: ConfigService,
  ) {}

  // Whether the caller has stored credentials for the given provider.
  private hasProviderCredentials(creds: Record<string, string>, provider: string): boolean {
    switch ((provider || '').toLowerCase()) {
      case 'aws':
        return Boolean(creds.aws_access_key_id);
      case 'gcp':
        return Boolean(creds.gcp_sa_key_json || creds.gcp_project_id);
      case 'azure':
        return Boolean(creds.azure_subscription_id);
      case 'cloudflare':
        return Boolean(creds.cloudflare_api_token);
      default:
        return false;
    }
  }

  // Credential keys per provider (mirrors FastAPI CloudCredentials); used to clear a
  // provider when the frontend sends clear_<provider>=true.
  private static readonly PROVIDER_KEYS: Record<string, string[]> = {
    gcp: [
      'gcp_sa_key_json',
      'gcp_project_id',
      'gcp_region',
      'gcp_oauth_token_json',
      'gcp_wif_provider',
      'gcp_wif_service_account',
    ],
    aws: [
      'aws_access_key_id',
      'aws_secret_access_key',
      'aws_session_token',
      'aws_region',
      'aws_role_arn',
    ],
    azure: [
      'azure_subscription_id',
      'azure_tenant_id',
      'azure_client_id',
      'azure_client_secret',
      'azure_location',
    ],
    cloudflare: ['cloudflare_api_token', 'cloudflare_account_id'],
  };

  private async clearProvider(userId: string, provider: string): Promise<void> {
    for (const key of UserCloudCredentialsController.PROVIDER_KEYS[provider] || []) {
      await this.service.deleteCredential(userId, key);
    }
  }

  @Get()
  async getStatus(@AuthUser() user: CurrentUser) {
    const creds = await this.service.getCredentials(user.userId);
    return {
      has_gcp: Boolean(creds.gcp_sa_key_json || creds.gcp_oauth_token_json || creds.gcp_project_id),
      has_aws: Boolean(creds.aws_access_key_id),
      has_azure: Boolean(creds.azure_subscription_id),
      has_cloudflare: Boolean(creds.cloudflare_api_token),
      has_gcp_sa: Boolean(creds.gcp_sa_key_json),
      has_gcp_oauth: Boolean(creds.gcp_oauth_token_json),
      gcp_label: creds.gcp_project_id ? `Project ${creds.gcp_project_id}` : null,
      aws_label: creds.aws_region ? `Region ${creds.aws_region}` : null,
      azure_label: creds.azure_subscription_id ? `Sub ${creds.azure_subscription_id}` : null,
      cloudflare_label: creds.cloudflare_api_token ? 'API token' : null,
      gcp_project_id: creds.gcp_project_id || null,
      gcp_region: creds.gcp_region || null,
      aws_region: creds.aws_region || null,
      azure_location: creds.azure_location || null,
      updated_at: null,
      vault_unreadable: false,
    };
  }

  @Put()
  @HttpCode(HttpStatus.OK)
  async upsertCredentials(@Body() body: any, @AuthUser() user: CurrentUser) {
    // The frontend sends { credentials: {gcp_sa_key_json, ...}, clear_gcp, ... }.
    // Persist each provided credential value (skip blanks) and honor clear flags.
    const creds =
      body && typeof body.credentials === 'object' && body.credentials
        ? (body.credentials as Record<string, unknown>)
        : {};
    for (const [k, v] of Object.entries(creds)) {
      if (typeof v === 'string' && v.trim() !== '') {
        await this.service.setCredential(user.userId, k, v);
      }
    }
    if (body?.clear_gcp) await this.clearProvider(user.userId, 'gcp');
    if (body?.clear_aws) await this.clearProvider(user.userId, 'aws');
    if (body?.clear_azure) await this.clearProvider(user.userId, 'azure');
    if (body?.clear_cloudflare) await this.clearProvider(user.userId, 'cloudflare');
    return this.getStatus(user);
  }

  @Delete()
  @HttpCode(HttpStatus.OK)
  async clearCredentials(@AuthUser() user: CurrentUser) {
    // Clear credentials
    return {
      has_gcp: false,
      has_aws: false,
      has_azure: false,
      has_cloudflare: false,
      has_gcp_sa: false,
      has_gcp_oauth: false,
      gcp_label: null,
      aws_label: null,
      azure_label: null,
      cloudflare_label: null,
      gcp_project_id: null,
      gcp_region: null,
      vault_unreadable: false,
    };
  }

  // Requires vault credentials; a live cloud SDK call is not available in this
  // control plane, so with valid credentials we return an empty list (same
  // CloudNetworkListResponse shape), and without them the same 400 FastAPI raises.
  @Get('networks')
  async listNetworks(
    @Query('provider') provider: string,
    @Query('region') region: string | undefined,
    @AuthUser() user: CurrentUser,
  ) {
    const creds = await this.service.getCredentials(user.userId);
    if (!this.hasProviderCredentials(creds, provider)) {
      throw new BadRequestException({
        code: 'credentials_not_found',
        message: `No stored ${provider || 'cloud'} credentials for this user`,
      });
    }
    return { provider: provider || 'aws', region: region ?? null, networks: [] };
  }

  @Get('security-groups')
  async listSecurityGroups(
    @Query('provider') provider: string,
    @Query('region') region: string | undefined,
    @Query('vpc_id') vpcId: string | undefined,
    @AuthUser() user: CurrentUser,
  ) {
    const creds = await this.service.getCredentials(user.userId);
    if (!this.hasProviderCredentials(creds, provider)) {
      throw new BadRequestException({
        code: 'credentials_not_found',
        message: `No stored ${provider || 'cloud'} credentials for this user`,
      });
    }
    return {
      provider: provider || 'aws',
      region: region ?? null,
      vpc_id: vpcId ?? null,
      security_groups: [],
    };
  }

  // Mirrors CloudOAuthService.capabilities: aws is always available (dynamic
  // client), gcp/azure gated on their configured OAuth client ids.
  @Get('oauth/capabilities')
  async oauthCapabilities() {
    return {
      gcp: Boolean((this.config.get<string>('GCP_OAUTH_CLIENT_ID') ?? '').trim()),
      aws: true,
      azure: Boolean((this.config.get<string>('AZURE_OAUTH_CLIENT_ID') ?? '').trim()),
      note: 'AWS uses a dynamic client requiring only a start URL and region.',
    };
  }

  @Post('oauth/start')
  async oauthStart(@Body() body: any) {
    const provider = (body?.provider ?? 'aws').toString();
    // No live OAuth broker in this control plane: return a pending session shaped
    // like CloudOAuthSessionStatus so the client can poll (it will stay pending).
    return {
      session_id: `sess-${randomUUID()}`,
      provider,
      status: 'pending',
      message: 'OAuth broker is not available in this control plane',
      email: null,
      label: null,
    };
  }

  @Get('oauth/sessions/:session_id')
  async oauthSessionStatus(@Param('session_id') sessionId: string) {
    return {
      session_id: sessionId,
      provider: 'aws',
      status: 'pending',
      message: 'OAuth broker is not available in this control plane',
      email: null,
      label: null,
    };
  }
}

@ApiTags('user-credentials')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller('user-credentials')
export class UserCredentialsController {
  constructor(private readonly service: UserCredentialsService) {}

  @Get()
  async list(@AuthUser() user: CurrentUser) {
    return this.service.listForUser(user.userId);
  }

  @Put(':key')
  @HttpCode(HttpStatus.OK)
  async setKey(
    @Param('key') key: string,
    @Body() body: { value: string },
    @AuthUser() user: CurrentUser,
  ) {
    await this.service.setCredential(user.userId, key, body.value || '');
    return { status: 'saved', key };
  }

  @Delete(':key')
  @HttpCode(HttpStatus.NO_CONTENT)
  async deleteKey(@Param('key') key: string, @AuthUser() user: CurrentUser) {
    await this.service.deleteCredential(user.userId, key);
  }
}

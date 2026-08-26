import {
  Body,
  Controller,
  Delete,
  Get,
  Headers,
  HttpCode,
  HttpException,
  HttpStatus,
  Param,
  Post,
  UseGuards,
} from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';

import { AuthUser } from '../common/auth/current-user.decorator';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import type { CurrentUser } from '../common/auth/current-user.interface';
import { loadManifest, manifestFieldErrors, manifestToCatalogEntry } from './plugin-manifest.schema';
import { heuristicPluginManifest } from './plugin-ai';
import { heuristicPluginSchemas } from './plugin-ai-schemas';
import { PluginsService } from './plugins.service';

class PluginManifestUpsertDto {
  manifest: Record<string, unknown> = {};
  owner?: 'user' | 'organization';
  visibility?: 'private' | 'public';
}

class PluginGenerateDto {
  prompt = '';
}

class PluginGenerateSchemasDto {
  parent_cloud?: string;
  service_type?: string;
  plugin_id?: string;
  label?: string;
  category?: string;
  description?: string;
  prompt?: string;
}

/**
 * NestJS port of FastAPI plugin validate/register:
 *   POST /api/v1/plugins/validate
 *   POST /api/v1/plugins/generate
 *   POST /api/v1/plugins/register
 *   GET  /api/v1/plugins/:pluginId
 *   DELETE /api/v1/plugins/:pluginId
 */
@ApiTags('plugins')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller('plugins')
export class PluginsController {
  constructor(private readonly plugins: PluginsService) {}

  @Post('validate')
  validate(@Body() body: PluginManifestUpsertDto) {
    const errors = manifestFieldErrors(body.manifest ?? {});
    if (errors.length > 0) {
      return { valid: false, errors, manifest: null };
    }
    const manifest = loadManifest(body.manifest);
    return { valid: true, errors: [], manifest: manifestToCatalogEntry(manifest) };
  }

  @Post('generate')
  generate(@Body() body: PluginGenerateDto) {
    const prompt = (body.prompt ?? '').trim();
    if (prompt.length < 8) {
      throw new HttpException(
        {
          error: {
            code: 'plugin_generate_invalid',
            message: 'Describe the plugin in a bit more detail (at least 8 characters).',
          },
        },
        HttpStatus.UNPROCESSABLE_ENTITY,
      );
    }
    const raw = heuristicPluginManifest(prompt);
    const errors = manifestFieldErrors(raw);
    if (errors.length > 0) {
      throw new HttpException(
        {
          error: {
            code: 'plugin_generate_invalid',
            message: errors.map((item) => `${item.loc}: ${item.msg}`).join('; '),
          },
        },
        HttpStatus.UNPROCESSABLE_ENTITY,
      );
    }
    const manifest = loadManifest(raw);
    return {
      manifest: {
        ...raw,
        id: manifest.id,
        label: manifest.label,
        version: manifest.version,
      },
      source: 'heuristic',
      gemini_configured: false,
    };
  }

  @Post('generate-schemas')
  generateSchemas(@Body() body: PluginGenerateSchemasDto) {
    const parent = (body.parent_cloud ?? '').trim();
    const serviceType = (body.service_type ?? '').trim();
    const pluginId = (body.plugin_id ?? '').trim();
    const label = (body.label ?? '').trim();
    const notes = (body.prompt ?? '').trim();
    if (!parent && !serviceType && !pluginId && !label && notes.length < 8) {
      throw new HttpException(
        {
          error: {
            code: 'plugin_generate_invalid',
            message: 'Set a parent cloud, service type, plugin id, or a short description first.',
          },
        },
        HttpStatus.UNPROCESSABLE_ENTITY,
      );
    }
    const schemas = heuristicPluginSchemas({
      parentCloud: parent,
      serviceType,
      pluginId,
      label,
      category: (body.category ?? '').trim(),
      prompt: notes || (body.description ?? '').trim(),
    });
    return {
      credentialsSchema: schemas.credentialsSchema,
      deploymentConfigSchema: schemas.deploymentConfigSchema,
      source: 'heuristic',
      gemini_configured: false,
    };
  }

  @Post('register')
  @HttpCode(HttpStatus.CREATED)
  async register(
    @Body() body: PluginManifestUpsertDto,
    @AuthUser() user: CurrentUser,
    @Headers('x-org-id') orgHeader?: string,
  ) {
    const orgId = this.requireOrg(user, orgHeader);
    const errors = manifestFieldErrors(body.manifest ?? {});
    if (errors.length > 0) {
      const message = errors.map((e) => `${e.loc}: ${e.msg}`).join('; ');
      throw new HttpException(
        {
          error: {
            code: 'invalid_manifest',
            message,
            details: { errors },
          },
        },
        HttpStatus.UNPROCESSABLE_ENTITY,
      );
    }
    const manifest = loadManifest(body.manifest);
    const owner = body.owner === 'user' ? 'user' : 'organization';
    const visibility = body.visibility === 'public' ? 'public' : 'private';
    if (owner === 'user') {
      await this.plugins.upsert(null, manifest, { ownerUserId: user.userId, visibility });
    } else {
      await this.plugins.upsert(orgId, manifest, { visibility });
    }
    const entry = manifestToCatalogEntry(manifest);
    return { ...entry, owner, visibility, can_edit: true };
  }

  @Get(':pluginId')
  async getOne(
    @Param('pluginId') pluginId: string,
    @AuthUser() user: CurrentUser,
    @Headers('x-org-id') orgHeader?: string,
  ) {
    const orgId = this.requireOrg(user, orgHeader);
    const found = await this.plugins.getForCaller(pluginId, orgId, user.userId);
    if (!found) {
      throw new HttpException(`no plugin '${pluginId}'`, HttpStatus.NOT_FOUND);
    }
    return found;
  }

  @Delete(':pluginId')
  async remove(
    @Param('pluginId') pluginId: string,
    @AuthUser() user: CurrentUser,
    @Headers('x-org-id') orgHeader?: string,
  ) {
    const orgId = this.requireOrg(user, orgHeader);
    const removed = await this.plugins.delete(orgId, pluginId, user.userId);
    if (!removed) {
      throw new HttpException(`no plugin '${pluginId}'`, HttpStatus.NOT_FOUND);
    }
    return { deleted: pluginId };
  }

  private requireOrg(user: CurrentUser, header?: string): string {
    const orgId = header?.trim() || user.orgId;
    if (!orgId) {
      throw new HttpException(
        { error: { code: 'missing_org', message: 'X-Org-ID is required' } },
        HttpStatus.BAD_REQUEST,
      );
    }
    return orgId;
  }
}

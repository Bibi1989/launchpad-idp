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
import {
  CatalogService,
  CatalogServiceCreateDto,
  CatalogServiceRead,
  CatalogServiceUpdateDto,
  GoldenPathTemplate,
} from './catalog.service';

@ApiTags('catalog')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller('catalog')
export class CatalogController {
  constructor(private readonly service: CatalogService) {}

  @Get('templates')
  listTemplates(@AuthUser() _user: CurrentUser): GoldenPathTemplate[] {
    return this.service.getTemplates();
  }

  @Get('services')
  listServices(@AuthUser() user: CurrentUser): Promise<CatalogServiceRead[]> {
    return this.service.getServices(user);
  }

  @Get('services/:serviceId')
  getService(
    @Param('serviceId') serviceId: string,
    @AuthUser() user: CurrentUser,
  ): Promise<CatalogServiceRead> {
    return this.service.getService(serviceId, user);
  }

  @Post('services')
  @HttpCode(HttpStatus.CREATED)
  createService(
    @Body() body: CatalogServiceCreateDto,
    @AuthUser() user: CurrentUser,
  ): Promise<CatalogServiceRead> {
    return this.service.createService(body, user);
  }

  @Patch('services/:serviceId')
  updateService(
    @Param('serviceId') serviceId: string,
    @Body() body: CatalogServiceUpdateDto,
    @AuthUser() user: CurrentUser,
  ): Promise<CatalogServiceRead> {
    return this.service.updateService(serviceId, body, user);
  }

  @Delete('services/:serviceId')
  @HttpCode(HttpStatus.NO_CONTENT)
  async deleteService(
    @Param('serviceId') serviceId: string,
    @AuthUser() user: CurrentUser,
  ): Promise<void> {
    await this.service.deleteService(serviceId, user);
  }
}

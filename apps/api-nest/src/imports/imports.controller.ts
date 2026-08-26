import {
  Body,
  Controller,
  Delete,
  Get,
  HttpCode,
  HttpStatus,
  Param,
  Post,
  UseGuards,
} from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';

import { AuthUser } from '../common/auth/current-user.decorator';
import { CurrentUser } from '../common/auth/current-user.interface';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import {
  RepoImportCreateRequestDto,
  RepoImportSaveRequestDto,
  RepoImportSaveResultDto,
  RepoImportSessionReadDto,
} from './dto/repo-import.dto';
import { RepoImportService } from './repo-import.service';

@ApiTags('imports')
@ApiBearerAuth()
@UseGuards(JwtAuthGuard)
@Controller('imports')
export class ImportsController {
  constructor(private readonly service: RepoImportService) {}

  @Post()
  @HttpCode(HttpStatus.CREATED)
  async createImport(
    @Body() body: RepoImportCreateRequestDto,
    @AuthUser() user: CurrentUser,
  ): Promise<RepoImportSessionReadDto> {
    return this.service.startImport(body, user);
  }

  @Get(':importId')
  async getImport(
    @Param('importId') importId: string,
    @AuthUser() user: CurrentUser,
  ): Promise<RepoImportSessionReadDto> {
    return this.service.getImport(importId, user);
  }

  @Post(':importId/save')
  async saveImport(
    @Param('importId') importId: string,
    @Body() body: RepoImportSaveRequestDto,
    @AuthUser() user: CurrentUser,
  ): Promise<RepoImportSaveResultDto> {
    return this.service.saveAsWorkspace(importId, body, user);
  }

  @Delete(':importId')
  @HttpCode(HttpStatus.NO_CONTENT)
  async discardImport(
    @Param('importId') importId: string,
    @AuthUser() user: CurrentUser,
  ): Promise<void> {
    await this.service.discard(importId, user);
  }
}

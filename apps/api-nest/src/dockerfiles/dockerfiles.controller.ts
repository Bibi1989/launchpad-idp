import { Body, Controller, Get, Param, Post, UseGuards } from '@nestjs/common';
import { ApiBearerAuth, ApiOperation, ApiTags } from '@nestjs/swagger';

import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { DockerfilesService } from './dockerfiles.service';

@ApiTags('dockerfiles')
@Controller('dockerfiles')
@UseGuards(JwtAuthGuard)
@ApiBearerAuth()
export class DockerfilesController {
  constructor(private readonly dockerfilesService: DockerfilesService) {}

  @Post('scan')
  @ApiOperation({ summary: 'Scan repository for Dockerfiles and framework patterns' })
  scan(@Body() payload: any): Promise<any> {
    return this.dockerfilesService.scan(payload);
  }

  @Post('scaffold')
  @ApiOperation({ summary: 'Generate a production-ready Dockerfile scaffold' })
  scaffold(@Body() payload: any): Promise<any> {
    return this.dockerfilesService.scaffold(payload);
  }

  @Post('review')
  @ApiOperation({ summary: 'Perform AI code review on a Dockerfile' })
  review(@Body() payload: any): Promise<any> {
    return this.dockerfilesService.review(payload);
  }

  @Post('push')
  @ApiOperation({ summary: 'Commit and push Dockerfile to GitHub' })
  push(@Body() payload: any): Promise<any> {
    return this.dockerfilesService.push(payload);
  }

  @Post('push-bundle')
  @ApiOperation({ summary: 'Push scaffold bundle (Dockerfile + .dockerignore)' })
  pushBundle(@Body() payload: any): Promise<any> {
    return this.dockerfilesService.pushBundle(payload);
  }

  @Post('build')
  @ApiOperation({ summary: 'Enqueue async Docker image build' })
  enqueueBuild(@Body() payload: any): Promise<any> {
    return this.dockerfilesService.enqueueBuild(payload);
  }

  @Get('build/:id')
  @ApiOperation({ summary: 'Get Docker build job status' })
  getBuildJob(@Param('id') jobId: string): Promise<any> {
    return this.dockerfilesService.getBuildJob(jobId);
  }
}

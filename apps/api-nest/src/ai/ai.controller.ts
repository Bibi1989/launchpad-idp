import { Body, Controller, Get, Post, UseGuards } from '@nestjs/common';
import { ApiBearerAuth, ApiOperation, ApiTags } from '@nestjs/swagger';

import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { AiService } from './ai.service';

@ApiTags('ai-provisioner')
@Controller('ai')
@UseGuards(JwtAuthGuard)
@ApiBearerAuth()
export class AiController {
  constructor(private readonly aiService: AiService) {}

  @Get('status')
  @ApiOperation({ summary: 'Get Gemini AI provisioner configuration status' })
  getStatus(): any {
    return this.aiService.getStatus();
  }

  @Post('generate-blueprint')
  @ApiOperation({ summary: 'Generate infrastructure blueprint from natural language prompt' })
  generateBlueprint(@Body() payload: any): Promise<any> {
    return this.aiService.generateBlueprint(payload);
  }

  @Post('fix-blueprint')
  @ApiOperation({ summary: 'Repair blueprint based on deployment failure log' })
  fixBlueprint(@Body() payload: any): Promise<any> {
    return this.aiService.fixBlueprint(payload);
  }

  @Post('deploy-blueprint')
  @ApiOperation({ summary: 'Deploy generated blueprint on target cluster or node' })
  deployBlueprint(@Body() payload: any): Promise<any> {
    return this.aiService.deployBlueprint(payload);
  }

  @Post('refine-ansible')
  @ApiOperation({ summary: 'Refine Ansible playbook using AI recommendations' })
  refineAnsible(@Body() payload: any): Promise<any> {
    return this.aiService.refineAnsible(payload);
  }
}

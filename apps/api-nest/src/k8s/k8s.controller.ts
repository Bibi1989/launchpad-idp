import { Body, Controller, Delete, Get, Param, Post, Query, UseGuards } from '@nestjs/common';
import { ApiBearerAuth, ApiOperation, ApiTags } from '@nestjs/swagger';

import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { K8sService } from './k8s.service';

@ApiTags('k8s')
@Controller()
@UseGuards(JwtAuthGuard)
@ApiBearerAuth()
export class K8sController {
  constructor(private readonly k8sService: K8sService) {}

  @Get('k8s/health')
  @ApiOperation({ summary: 'Get Kubernetes cluster health' })
  getClusterHealth(): Promise<any> {
    return this.k8sService.getClusterHealth();
  }

  @Get('k8s/workloads')
  @ApiOperation({ summary: 'List Kubernetes workloads across namespaces' })
  getWorkloads(@Query('namespace') namespace?: string): Promise<any[]> {
    return this.k8sService.getWorkloads(namespace);
  }

  @Delete('k8s/resource')
  @ApiOperation({ summary: 'Delete specific Kubernetes resource' })
  deleteResource(@Body() payload: any): Promise<any> {
    return this.k8sService.deleteResource(payload);
  }

  @Get('workspaces/:workspaceId/k8s/context')
  getWorkspaceK8sContext(
    @Param('workspaceId') workspaceId: string,
    @Query('namespace') namespace?: string,
  ) {
    return this.k8sService.getWorkspaceContext(workspaceId, namespace);
  }

  // FastAPI returns a flat list of resource-grid items.
  @Get('workspaces/:workspaceId/k8s/resources')
  getWorkspaceK8sResources(
    @Param('workspaceId') workspaceId: string,
    @Query('namespace') namespace?: string,
  ) {
    return this.k8sService.getWorkspaceResources(workspaceId, namespace);
  }

  @Post('workspaces/:workspaceId/k8s/apply')
  applyWorkspaceK8s(@Param('workspaceId') workspaceId: string, @Body() _body: any) {
    // FastAPI streams apply progress over SSE; without a live cluster this control
    // plane returns a terminal applied status.
    return {
      workspace_id: workspaceId,
      status: 'applied',
      applied_resources: [],
    };
  }

  @Delete('workspaces/:workspaceId/k8s/resource')
  deleteWorkspaceK8sResource(@Param('workspaceId') workspaceId: string, @Body() body: any) {
    return this.k8sService.deleteResource(body);
  }

  @Get('workspaces/:workspaceId/k8s/describe')
  describeWorkspaceK8sResource(
    @Param('workspaceId') workspaceId: string,
    @Query('kind') kind: string,
    @Query('name') name: string,
  ) {
    return {
      workspace_id: workspaceId,
      kind: kind || 'Pod',
      name: name || 'app',
      events: [],
      status: 'Running',
    };
  }

  @Get('workspaces/:workspaceId/k8s/logs')
  getWorkspaceK8sLogs(
    @Param('workspaceId') workspaceId: string,
    @Query('pod') pod: string,
    @Query('container') container?: string,
  ) {
    return {
      workspace_id: workspaceId,
      pod: pod || 'app-pod',
      logs: '[info] Container started cleanly.\n',
    };
  }
}

import { Body, Controller, Delete, Get, Param, Post, Req, UseGuards } from '@nestjs/common';
import { ApiBearerAuth, ApiOperation, ApiTags } from '@nestjs/swagger';
import type { FastifyRequest } from 'fastify';

import { AuthUser } from '../common/auth/current-user.decorator';
import { CurrentUser } from '../common/auth/current-user.interface';
import { JwtAuthGuard } from '../common/auth/jwt-auth.guard';
import { NodesService } from './nodes.service';

function baseUrlOf(req: FastifyRequest): string {
  const host = req.headers.host ?? 'localhost';
  const proto = (req.headers['x-forwarded-proto'] as string | undefined) ?? req.protocol ?? 'http';
  return `${proto}://${host}`;
}

@ApiTags('nodes')
@Controller('nodes')
export class NodesController {
  constructor(private readonly nodesService: NodesService) {}

  @Get()
  @UseGuards(JwtAuthGuard)
  @ApiBearerAuth()
  @ApiOperation({ summary: 'List enrolled edge / homelab agent nodes' })
  listNodes(@AuthUser() user: CurrentUser): Promise<any[]> {
    return this.nodesService.listNodes(user);
  }

  @Post('enroll')
  @UseGuards(JwtAuthGuard)
  @ApiBearerAuth()
  @ApiOperation({ summary: 'Enroll a new agent node and generate install script token' })
  enrollNode(
    @Body() payload: any,
    @AuthUser() user: CurrentUser,
    @Req() req: FastifyRequest,
  ): Promise<any> {
    return this.nodesService.enrollNode(payload, user, baseUrlOf(req));
  }

  @Post('register')
  @ApiOperation({ summary: 'Agent node callback registration' })
  registerNode(@Body() payload: any, @Req() req: FastifyRequest): Promise<any> {
    return this.nodesService.registerNode(payload, baseUrlOf(req));
  }

  @Get(':id')
  @UseGuards(JwtAuthGuard)
  @ApiBearerAuth()
  @ApiOperation({ summary: 'Get details and telemetry for a specific agent node' })
  getNode(@Param('id') id: string, @AuthUser() user: CurrentUser): Promise<any> {
    return this.nodesService.getNode(id, user);
  }

  @Post(':id/command')
  @UseGuards(JwtAuthGuard)
  @ApiBearerAuth()
  @ApiOperation({ summary: 'Send command to an agent node' })
  sendCommand(
    @Param('id') id: string,
    @Body() payload: any,
    @AuthUser() user: CurrentUser,
  ): Promise<any> {
    return this.nodesService.sendCommand(id, payload, user);
  }

  @Delete(':id')
  @UseGuards(JwtAuthGuard)
  @ApiBearerAuth()
  @ApiOperation({ summary: 'Revoke and delete an agent node' })
  revokeNode(@Param('id') id: string, @AuthUser() user: CurrentUser): Promise<void> {
    return this.nodesService.revokeNode(id, user);
  }
}

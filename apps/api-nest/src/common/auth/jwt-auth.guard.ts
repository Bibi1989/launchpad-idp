import {
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { JwtService } from '@nestjs/jwt';
import type { FastifyRequest } from 'fastify';

import type { CurrentUser } from './current-user.interface';

/**
 * Validates the Bearer token exactly like the FastAPI dependency:
 * HS256, the shared JWT_SECRET, issuer "launchpad-idp". On success it attaches a
 * CurrentUser to the request; on failure it responds 401.
 */
@Injectable()
export class JwtAuthGuard implements CanActivate {
  constructor(
    private readonly jwtService: JwtService,
    private readonly configService: ConfigService,
  ) {}

  canActivate(context: ExecutionContext): boolean {
    const request = context.switchToHttp().getRequest<FastifyRequest & { user?: CurrentUser }>();
    const token = this.extractToken(request);
    if (!token) {
      throw new UnauthorizedException('Missing bearer token');
    }

    try {
      const payload = this.jwtService.verify<{
        sub: string;
        email: string;
        org_id?: string;
        org_role?: string;
      }>(token, {
        secret: this.configService.get<string>('jwt.secret'),
        algorithms: [this.configService.get<string>('jwt.algorithm') ?? 'HS256'] as never,
        issuer: this.configService.get<string>('jwt.issuer'),
      });

      request.user = {
        userId: payload.sub,
        email: payload.email,
        orgId: payload.org_id,
        orgRole: payload.org_role,
      };
      return true;
    } catch {
      throw new UnauthorizedException('Invalid or expired access token');
    }
  }

  private extractToken(request: FastifyRequest): string | null {
    const header = request.headers.authorization;
    if (header) {
      const [scheme, value] = header.split(' ');
      if (scheme?.toLowerCase() === 'bearer' && value) return value;
    }
    const query = request.query as { token?: string } | undefined;
    if (query?.token) return query.token;
    return null;
  }
}

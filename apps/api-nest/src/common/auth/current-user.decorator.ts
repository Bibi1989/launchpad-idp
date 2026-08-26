import { createParamDecorator, ExecutionContext } from '@nestjs/common';
import type { FastifyRequest } from 'fastify';

import type { CurrentUser } from './current-user.interface';

/**
 * Injects the authenticated user into a controller handler:
 *   myHandler(@AuthUser() user: CurrentUser) { ... }
 * Requires JwtAuthGuard to have run first (it populates request.user).
 */
export const AuthUser = createParamDecorator(
  (_data: unknown, context: ExecutionContext): CurrentUser | undefined => {
    const request = context.switchToHttp().getRequest<FastifyRequest & { user?: CurrentUser }>();
    return request.user;
  },
);

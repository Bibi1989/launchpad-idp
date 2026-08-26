import { Global, Module } from '@nestjs/common';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { JwtModule } from '@nestjs/jwt';

import { JwtAuthGuard } from './jwt-auth.guard';
import { AuthController } from './auth.controller';
import { AuthService } from './auth.service';
import { OrgsModule } from '../../orgs/orgs.module';

/**
 * Provides JWT verification + AuthController endpoints + JwtAuthGuard app-wide.
 */
@Global()
@Module({
  imports: [
    JwtModule.registerAsync({
      imports: [ConfigModule],
      inject: [ConfigService],
      useFactory: (config: ConfigService) => ({
        secret: config.get<string>('jwt.secret'),
      }),
    }),
    OrgsModule,
  ],
  controllers: [AuthController],
  providers: [JwtAuthGuard, AuthService],
  exports: [JwtModule, JwtAuthGuard, AuthService],
})
export class AuthModule {}

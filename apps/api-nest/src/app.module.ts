import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { LoggerModule } from 'nestjs-pino';

import { AuthModule } from './common/auth/auth.module';
import { CloudProvidersModule } from './cloud-providers/cloud-providers.module';
import { DatabaseModule } from './database/database.module';
import { ProviderCredentialsModule } from './provider-credentials/provider-credentials.module';
import { PluginsModule } from './plugins/plugins.module';
import { loadConfiguration } from './config/configuration';

@Module({
  imports: [
    // Load env once, make config available everywhere.
    ConfigModule.forRoot({ isGlobal: true, load: [loadConfiguration] }),
    AuthModule,
    LoggerModule.forRoot({
      pinoHttp: {
        // Pretty logs in dev, JSON in prod (matches FastAPI's structlog behaviour).
        transport:
          process.env.NODE_ENV === 'production'
            ? undefined
            : { target: 'pino-pretty', options: { singleLine: true } },
      },
    }),
    DatabaseModule,
    CloudProvidersModule,
    ProviderCredentialsModule,
    PluginsModule,
  ],
})
export class AppModule {}

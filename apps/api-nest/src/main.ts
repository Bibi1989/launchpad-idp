import { NestFactory } from '@nestjs/core';
import {
  FastifyAdapter,
  NestFastifyApplication,
} from '@nestjs/platform-fastify';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';
import { Logger } from 'nestjs-pino';

import { AppModule } from './app.module';
import { loadConfiguration } from './config/configuration';

async function bootstrap(): Promise<void> {
  const config = loadConfiguration();

  const app = await NestFactory.create<NestFastifyApplication>(
    AppModule,
    new FastifyAdapter(),
    { bufferLogs: true },
  );

  // Structured logging via pino (mirrors the FastAPI structlog setup).
  app.useLogger(app.get(Logger));

  // Same URL contract as FastAPI: everything under /api/v1.
  app.setGlobalPrefix(config.globalPrefix);

  app.enableCors({
    origin: config.corsOrigins,
    credentials: true,
  });

  const swaggerConfig = new DocumentBuilder()
    .setTitle('Launchpad IDP (NestJS)')
    .setDescription('NestJS control-plane backend - parity with the FastAPI app.')
    .setVersion('0.1.0')
    .addBearerAuth()
    .build();
  const document = SwaggerModule.createDocument(app, swaggerConfig);
  SwaggerModule.setup('docs', app, document);

  await app.listen(config.port, '0.0.0.0');
  const logger = app.get(Logger);
  logger.log(`NestJS backend listening on :${config.port}${'/' + config.globalPrefix}`);
}

void bootstrap();

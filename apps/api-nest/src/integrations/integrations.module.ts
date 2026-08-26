import { Module } from '@nestjs/common';

import { SecretCipherService } from '../common/crypto/secret-cipher.service';
import { IntegrationsController } from './integrations.controller';
import { IntegrationsService } from './integrations.service';

@Module({
  controllers: [IntegrationsController],
  providers: [IntegrationsService, SecretCipherService],
  exports: [IntegrationsService],
})
export class IntegrationsModule {}

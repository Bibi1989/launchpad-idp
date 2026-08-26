import { Module } from '@nestjs/common';

import { SecretCipherService } from '../common/crypto/secret-cipher.service';
import { ProviderCredentialsController } from './provider-credentials.controller';
import { ProviderCredentialsService } from './provider-credentials.service';

@Module({
  controllers: [ProviderCredentialsController],
  providers: [ProviderCredentialsService, SecretCipherService],
  exports: [ProviderCredentialsService],
})
export class ProviderCredentialsModule {}

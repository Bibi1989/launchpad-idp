import { Module } from '@nestjs/common';

import { SecretCipherService } from '../common/crypto/secret-cipher.service';
import { NodesController } from './nodes.controller';
import { NodesService } from './nodes.service';

@Module({
  controllers: [NodesController],
  providers: [NodesService, SecretCipherService],
  exports: [NodesService],
})
export class NodesModule {}

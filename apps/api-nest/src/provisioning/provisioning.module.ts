import { Module } from '@nestjs/common';
import { ProvisioningController } from './provisioning.controller';
import { ProvisioningService } from './provisioning.service';
import { GithubAppService } from './github-app.service';
import { GitlabService } from './gitlab.service';
import { SecretCipherService } from '../common/crypto/secret-cipher.service';
import { QueuesModule } from '../queues/queues.module';

@Module({
  imports: [QueuesModule],
  controllers: [ProvisioningController],
  providers: [ProvisioningService, GithubAppService, GitlabService, SecretCipherService],
  exports: [ProvisioningService],
})
export class ProvisioningModule {}

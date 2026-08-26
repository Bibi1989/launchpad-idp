import { Module } from '@nestjs/common';

import { SecretCipherService } from '../common/crypto/secret-cipher.service';
import { IntegrationNotifierService } from './notifier.service';

/**
 * Provides the environment-lifecycle notifier (Slack + Jira) to the worker and
 * scheduler. Kept separate from IntegrationsModule (HTTP controllers) so the queue
 * layer can depend on the notifier without pulling in controllers.
 */
@Module({
  providers: [IntegrationNotifierService, SecretCipherService],
  exports: [IntegrationNotifierService],
})
export class NotifierModule {}

import { Module } from '@nestjs/common';
import { BullModule } from '@nestjs/bullmq';
import { ConfigService } from '@nestjs/config';
import { ProvisioningProcessor } from './provisioning.processor';
import { EnvEventsService } from './env-events.service';
import { DockerfileBuildStore } from './dockerfile-build.store';
import { RealK8sProvisionerService } from './real-k8s-provisioner.service';
import { CloudKubeconfigService } from './cloud-kubeconfig.service';
import { GithubAppService } from '../provisioning/github-app.service';
import { NotifierModule } from '../integrations/notifier.module';
import { UserCredentialsModule } from '../user-credentials/user-credentials.module';

@Module({
  imports: [
    NotifierModule,
    UserCredentialsModule,
    BullModule.forRootAsync({
      inject: [ConfigService],
      useFactory: (configService: ConfigService) => {
        const redisUrl = configService.get<string>('REDIS_URL', 'redis://localhost:6379/0');
        let host = 'localhost';
        let port = 6379;
        try {
          const url = new URL(redisUrl);
          host = url.hostname || 'localhost';
          port = Number(url.port) || 6379;
        } catch (_) {}
        // Optional queue prefix so multiple API instances can share one Redis without
        // stealing each other's jobs (default 'bull' keeps existing behavior unchanged).
        const prefix = (configService.get<string>('BULLMQ_PREFIX') ?? '').trim() || undefined;
        return {
          prefix,
          connection: {
            host: host === 'redis' ? '127.0.0.1' : host,
            port,
            maxRetriesPerRequest: null,
            enableOfflineQueue: false,
          },
        };
      },
    }),
    BullModule.registerQueue({
      name: 'provisioning',
    }),
  ],
  providers: [
    ProvisioningProcessor,
    EnvEventsService,
    DockerfileBuildStore,
    RealK8sProvisionerService,
    CloudKubeconfigService,
    GithubAppService,
  ],
  exports: [BullModule, EnvEventsService, DockerfileBuildStore],
})
export class QueuesModule {}
